import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import judge
from weakest import load_snapshot, recompute_weakest

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
                ("汇流箱A", "阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("汇流箱A", "阵列A-串09", 38.6, 8.6, 0.74, "合格"),
                ("汇流箱B", "阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for i, (box, code, voc, isc, ff, expect) in enumerate(samples):
                verdict, reason = judge(ff)
                assert verdict == expect
                ts = now - timedelta(seconds=i)
                conn.execute(
                    """INSERT INTO iv_scans
                       (box_code, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (box, code, voc, isc, ff, verdict, reason, ts, ts),
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
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可报送IV扫描")
    return user


def parse_dt(value, field: str):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{field} 时间格式不正确")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
            """SELECT id, box_code, string_code, voc_v, isc_a, fill_factor, status, verdict,
                      reason, created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    box = (data.get("box_code") or "").strip()
    code = (data.get("string_code") or "").strip()
    if not box:
        raise HTTPException(status_code=400, detail="汇流箱号不能为空")
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
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
               (box_code, string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, box_code, string_code, voc_v, isc_a, fill_factor, status, verdict,
                         reason, created_by, created_at, processed_at""",
            (box, code, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/boxes")
async def list_boxes(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT box_code FROM iv_scans WHERE box_code <> '' ORDER BY box_code"
        ).fetchall()
        return [r["box_code"] for r in rows]


async def _window_payload(request: Request):
    data = await request.json()
    end = parse_dt(data.get("window_end"), "截止钟点")
    if end is None:
        end = datetime.now(timezone.utc)
    start = parse_dt(data.get("window_start"), "起始钟点")
    if start is None:
        start = end - timedelta(hours=24)
    if start >= end:
        raise HTTPException(status_code=400, detail="起始钟点必须早于截止钟点")
    return start, end


@post("/api/boxes/{box:str}/recompute")
async def recompute_box(request: Request, box: str) -> dict:
    # 报送才限扫描员；最弱串是派生口径，观察员登录后也可触发重算查看，但永远改不了原始读数
    user = need_login(request)
    start, end = await _window_payload(request)
    # 注意：请求体里的 delta_v / is_weakest 等字段一律不读，差值与最弱标记只由办结数据算出
    return recompute_weakest(box, start, end, user["username"])


@get("/api/boxes/{box:str}/weakest")
async def get_weakest(request: Request, box: str) -> dict:
    need_login(request)
    start = parse_dt(request.query_params.get("window_start"), "起始钟点")
    end = parse_dt(request.query_params.get("window_end"), "截止钟点")
    if end is None:
        raise HTTPException(status_code=400, detail="缺少截止钟点")
    if start is None:
        start = end - timedelta(hours=24)
    if start >= end:
        raise HTTPException(status_code=400, detail="起始钟点必须早于截止钟点")
    with connect() as conn:
        snap = load_snapshot(conn, box, start, end)
    if snap is None:
        raise HTTPException(status_code=404, detail="该箱此钟段尚未重算")
    return snap


app = Litestar(
    route_handlers=[health, login, list_logs, create_log, list_boxes, recompute_box, get_weakest]
)
