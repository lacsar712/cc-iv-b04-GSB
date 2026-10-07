import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext
from psycopg.types.json import Jsonb

from db import SCHEMA, connect
from rules import judge, weakest

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("箱01", "阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("箱01", "阵列A-串05", 36.4, 8.9, 0.75, "合格"),
                ("箱01", "阵列A-串08", 40.9, 9.0, 0.73, "合格"),
                ("箱02", "阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
                ("箱02", "阵列B-串12", 39.6, 8.8, 0.80, "合格"),
            ]
            for box, code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, box_no, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, box, voc, isc, ff, verdict, reason, now, now),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可提交IV扫描")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, box_no, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    box_no = (data.get("box_no") or "").strip() or None
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, box_no, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, box_no, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, box_no, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/boxes")
async def list_boxes(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT box_no FROM (
                   SELECT box_no FROM iv_scans WHERE box_no IS NOT NULL
                   UNION
                   SELECT box_no FROM box_weakest_reports
               ) t ORDER BY box_no"""
        ).fetchall()
        return [r["box_no"] for r in rows]


@post("/api/box-weakest/recalc", status_code=201)
async def recalc_box_weakest(request: Request) -> dict:
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="观察员不能报送，仅扫描员可重算")
    data = await request.json()
    box_no = (data.get("box_no") or "").strip()
    cutoff_raw = (data.get("cutoff") or "").strip()
    if not box_no:
        raise HTTPException(status_code=400, detail="箱号不能为空")
    if not cutoff_raw:
        raise HTTPException(status_code=400, detail="截止钟点不能为空，须与重算在同一次提交里")
    try:
        cutoff = datetime.fromisoformat(cutoff_raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="截止钟点格式不正确")
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        pending_count = conn.execute(
            """SELECT COUNT(*) AS n FROM iv_scans
               WHERE box_no = %s AND status = 'pending' AND created_at <= %s""",
            (box_no, cutoff),
        ).fetchone()["n"]
        done_rows = conn.execute(
            """SELECT DISTINCT ON (string_code) string_code, voc_v, created_at
               FROM iv_scans
               WHERE box_no = %s AND status = 'done' AND created_at <= %s
               ORDER BY string_code, created_at DESC""",
            (box_no, cutoff),
        ).fetchall()
        complete = pending_count == 0
        if complete:
            result = weakest(
                [
                    {
                        "string_code": r["string_code"],
                        "voc_v": r["voc_v"],
                        "scanned_at": r["created_at"].isoformat(),
                    }
                    for r in done_rows
                ]
            )
        else:
            # 箱内还有未办结读数：不出最弱标记，也不填假数
            result = {
                "reference_voc": None,
                "rows": [
                    {
                        "string_code": r["string_code"],
                        "voc_v": float(r["voc_v"]),
                        "scanned_at": r["created_at"].isoformat(),
                        "drop": None,
                        "is_weakest": False,
                    }
                    for r in sorted(done_rows, key=lambda r: r["string_code"])
                ],
                "weakest_string": None,
                "weakest_voc": None,
                "weakest_drop": None,
            }
        row = conn.execute(
            """INSERT INTO box_weakest_reports
               (box_no, cutoff_at, complete, pending_count, reference_voc,
                weakest_string, weakest_voc, weakest_drop, rows, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id, created_at""",
            (
                box_no,
                cutoff,
                complete,
                pending_count,
                result["reference_voc"],
                result["weakest_string"],
                result["weakest_voc"],
                result["weakest_drop"],
                Jsonb(result["rows"]),
                user["username"],
                now,
            ),
        ).fetchone()
        conn.commit()
        return {
            "id": row["id"],
            "box_no": box_no,
            "cutoff_at": cutoff.isoformat(),
            "complete": complete,
            "pending_count": pending_count,
            "reference_voc": result["reference_voc"],
            "weakest_string": result["weakest_string"],
            "weakest_voc": result["weakest_voc"],
            "weakest_drop": result["weakest_drop"],
            "rows": result["rows"],
            "created_by": user["username"],
            "created_at": row["created_at"].isoformat(),
        }


@get("/api/box-weakest/latest")
async def latest_box_weakest(request: Request) -> dict:
    need_login(request)
    box_no = (request.query_params.get("box_no") or "").strip()
    if not box_no:
        raise HTTPException(status_code=400, detail="箱号不能为空")
    with connect() as conn:
        row = conn.execute(
            """SELECT id, box_no, cutoff_at, complete, pending_count, reference_voc,
                      weakest_string, weakest_voc, weakest_drop, rows, created_by, created_at
               FROM box_weakest_reports WHERE box_no = %s ORDER BY id DESC LIMIT 1""",
            (box_no,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="该箱暂无报送结果")
        return dump(row)


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        list_boxes,
        recalc_box_weakest,
        latest_box_weakest,
    ]
)
