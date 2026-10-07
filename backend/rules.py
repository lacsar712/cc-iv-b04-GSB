FF_MIN = 0.72


def judge(fill_factor: float) -> tuple[str, str]:
    if fill_factor >= FF_MIN:
        return "合格", f"填充因子 {fill_factor} 不低于 {FF_MIN}"
    return "衰减", f"填充因子 {fill_factor} 低于 {FF_MIN}"


def weakest(rows: list[dict]) -> dict:
    """同一汇流箱内找开路电压掉得最狠的一串。

    rows 为各串"最近办结"读数：{"string_code", "voc_v", "scanned_at"}，
    调用方需已按钟段过滤并保证每串至多一条。基准取箱内最高开路电压，
    差值 = 基准 - 该串电压，差值最大者为最弱串；并列时取组串编号靠前者，
    保证同一批读数重算结果稳定。
    """
    annotated = []
    if rows:
        reference = max(float(r["voc_v"]) for r in rows)
        for r in sorted(rows, key=lambda r: r["string_code"]):
            annotated.append(
                {
                    "string_code": r["string_code"],
                    "voc_v": float(r["voc_v"]),
                    "scanned_at": r["scanned_at"],
                    "drop": round(reference - float(r["voc_v"]), 2),
                    "is_weakest": False,
                }
            )
        top = max(annotated, key=lambda r: r["drop"])
        top["is_weakest"] = True
        return {
            "reference_voc": reference,
            "rows": annotated,
            "weakest_string": top["string_code"],
            "weakest_voc": top["voc_v"],
            "weakest_drop": top["drop"],
        }
    return {
        "reference_voc": None,
        "rows": [],
        "weakest_string": None,
        "weakest_voc": None,
        "weakest_drop": None,
    }
