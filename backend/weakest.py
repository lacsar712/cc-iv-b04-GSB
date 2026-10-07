"""同箱最弱串重算。

口径：钟段 [window_start, window_end) 内，每串取最近一条读数；
任一串最近一条仍为 pending，则整箱暂不判定（blocked），差值与最弱标记一律留空；
全部办结后，以箱内最高开路电压为基准，压降最大（电压最低）的一串标为最弱。

截止钟点 window_end 与全部结果在同一个事务里一次提交，外面偷填 delta_v /
is_weakest 不会被读取：这两个值只在本模块内由办结数据算出。
"""
from datetime import datetime, timezone

from db import connect

STATE_OK = "ok"          # 各串最近读数均已办结，已给出最弱标记
STATE_BLOCKED = "blocked"  # 有串最近读数未办结，不标最弱、不填差值
STATE_EMPTY = "empty"    # 钟段内没有任何读数


def _dump_run(run, rows):
    return {
        "box_code": run["box_code"],
        "window_start": run["window_start"].isoformat() if run["window_start"] else None,
        "window_end": run["window_end"].isoformat(),
        "state": run["state"],
        "baseline_voc": run["baseline_voc"],
        "weakest_string": run["weakest_string"],
        "weakest_voc": run["weakest_voc"],
        "recomputed_by": run["recomputed_by"],
        "recomputed_at": run["recomputed_at"].isoformat(),
        "rows": [
            {
                "string_code": r["string_code"],
                "scan_id": r["scan_id"],
                "voc_v": r["voc_v"],
                "status": r["status"],
                "is_latest_pending": r["is_latest_pending"],
                "delta_v": r["delta_v"],
                "is_weakest": r["is_weakest"],
            }
            for r in rows
        ],
    }


def recompute_weakest(box_code: str, window_start: datetime, window_end: datetime,
                      username: str) -> dict:
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 一个事务：锁读数 → 判定 → 写截止钟点与结果，最后一起 commit
        with conn.transaction():
            time_filter = "created_at >= %s AND created_at < %s"
            params = [box_code, window_start, window_end]

            # 先锁住钟段内本箱全部读数，防止重算途中工人办结/新报送串口径
            conn.execute(
                f"""SELECT id FROM iv_scans
                    WHERE box_code = %s AND {time_filter}
                    ORDER BY id FOR UPDATE""",
                params,
            ).fetchall()

            # 每串最近一条（起含止不含）
            latest = conn.execute(
                f"""SELECT s.id AS scan_id, s.string_code, s.voc_v, s.status
                    FROM iv_scans s
                    JOIN (
                        SELECT DISTINCT ON (string_code) id
                        FROM iv_scans
                        WHERE box_code = %s AND {time_filter}
                        ORDER BY string_code, created_at DESC, id DESC
                    ) t ON t.id = s.id
                    ORDER BY s.string_code
                    FOR UPDATE OF s""",
                params,
            ).fetchall()

            if not latest:
                state, baseline, weakest_string, weakest_voc = STATE_EMPTY, None, None, None
                row_payload = []
            elif any(r["status"] != "done" for r in latest):
                # 有一侧还未办结：不标最弱、不填假差值
                state = STATE_BLOCKED
                baseline = weakest_string = weakest_voc = None
                row_payload = [
                    (r["string_code"], r["scan_id"], float(r["voc_v"]), r["status"],
                     r["status"] != "done", None, False)
                    for r in latest
                ]
            else:
                state = STATE_OK
                baseline = max(float(r["voc_v"]) for r in latest)
                # 电压最低即压降最狠；并列时取编号靠前的一串
                weak = min(latest, key=lambda r: (float(r["voc_v"]), r["string_code"]))
                weakest_string = weak["string_code"]
                weakest_voc = float(weak["voc_v"])
                row_payload = [
                    (r["string_code"], r["scan_id"], float(r["voc_v"]), "done", False,
                     round(baseline - float(r["voc_v"]), 3),
                     r["string_code"] == weakest_string)
                    for r in latest
                ]

            run = conn.execute(
                """INSERT INTO box_weakest_runs
                   (box_code, window_start, window_end, state, baseline_voc,
                    weakest_string, weakest_voc, recomputed_by, recomputed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (box_code, window_start, window_end) DO UPDATE
                   SET state = EXCLUDED.state,
                       baseline_voc = EXCLUDED.baseline_voc,
                       weakest_string = EXCLUDED.weakest_string,
                       weakest_voc = EXCLUDED.weakest_voc,
                       recomputed_by = EXCLUDED.recomputed_by,
                       recomputed_at = EXCLUDED.recomputed_at
                   RETURNING id""",
                (box_code, window_start, window_end, state, baseline,
                 weakest_string, weakest_voc, username, now),
            ).fetchone()

            conn.execute("DELETE FROM box_weakest_rows WHERE run_id = %s", (run["id"],))
            if row_payload:
                conn.executemany(
                    """INSERT INTO box_weakest_rows
                       (run_id, string_code, scan_id, voc_v, status,
                        is_latest_pending, delta_v, is_weakest)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(run["id"], *p) for p in row_payload],
                )
        conn.commit()
        return load_snapshot(conn, box_code, window_start, window_end)


def load_snapshot(conn, box_code: str, window_start: datetime, window_end: datetime):
    run = conn.execute(
        """SELECT * FROM box_weakest_runs
           WHERE box_code = %s AND window_start = %s AND window_end = %s""",
        (box_code, window_start, window_end),
    ).fetchone()
    if run is None:
        return None
    rows = conn.execute(
        "SELECT * FROM box_weakest_rows WHERE run_id = %s ORDER BY string_code",
        (run["id"],),
    ).fetchall()
    return _dump_run(run, rows)
