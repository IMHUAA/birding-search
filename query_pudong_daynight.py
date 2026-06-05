#!/usr/bin/env python3
"""
浦东观测记录日观/夜观拆分查询
==================================
读取 output/pudong_观测记录.csv，按日观/夜观分类，
逐条查询各观测的物种列表并聚合，输出两份鸟种 CSV。

夜观标准：观测时间跨度与 19:00–次日05:00 有重叠
日观：同一天内，05:00 ≤ 开始时间 且 结束时间 ≤ 19:00
"""

import csv
import json
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

# 复用现有抓取器
sys.path.insert(0, str(Path(__file__).parent))
from birdreport_scraper import BirdReportScraper, SearchParams

# ─── 路径 ───
ROOT = Path(__file__).parent
OBS_CSV = ROOT / "output" / "pudong_观测记录.csv"
RARITY_CSV = ROOT / "上海_rarity.csv"
OUT_DAY = ROOT / "output" / "pudong_日观_鸟种.csv"
OUT_NIGHT = ROOT / "output" / "pudong_夜观_鸟种.csv"


# ─── 日/夜判断 ───
def is_night(start_str: str, end_str: str) -> bool:
    """
    观测时间跨度与 [19:00, 次日05:00] 有重叠即为夜观。
    等价于：NOT（同一天 AND start_time >= 05:00 AND end_time <= 19:00）
    """
    fmt = "%Y-%m-%d %H:%M"
    start = datetime.strptime(start_str, fmt)
    end = datetime.strptime(end_str, fmt)

    # 跨天：一定包含夜间时段
    if start.date() != end.date():
        return True

    # 同一天：判断时间段
    DAY_START = dtime(5, 0)
    DAY_END = dtime(19, 0)
    if start.time() >= DAY_START and end.time() <= DAY_END:
        return False  # 纯日间

    return True  # 夜间或黄昏/黎明段


# ─── 读取观测记录 ───
def load_observations() -> list[dict]:
    records = []
    with open(OBS_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            night = is_night(row["start_time"], row["end_time"])
            records.append({
                "serial_id": row["serial_id"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "username": row["username"],
                "taxoncount": row["taxoncount"],
                "is_night": night,
            })
    return records


# ─── 读取罕见度索引 ───
def load_rarity() -> dict:
    """返回 {taxon_id: rarity_index} 和 {name: rarity_index} 两个映射。"""
    by_id = {}
    by_name = {}
    with open(RARITY_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rid = row.get("taxon_id", "").strip()
            name = row.get("name", "").strip()
            ri = float(row.get("rarity_index", 0) or 0)
            if rid:
                by_id[rid] = ri
            if name:
                by_name[name] = ri
    return by_id, by_name


# ─── 聚合物种数据 ───
def aggregate_species(
    serial_ids: list[str],
    scraper: BirdReportScraper,
    rarity_by_id: dict,
    rarity_by_name: dict,
    label: str,
) -> dict:
    """
    对给定的 serial_ids，逐个查询物种，聚合返回：
    {taxon_id: {taxonname, latinname, taxonfamilyname, recordcount, rarity_index}}
    """
    species_map = {}
    total = len(serial_ids)

    for i, sid in enumerate(serial_ids, 1):
        print(f"  [{i}/{total}] {label} 查询 {sid}...", end=" ", flush=True)
        try:
            params = SearchParams(serial_id=sid)
            results = scraper.search(params)
            print(f"{len(results)} 种")
        except Exception as e:
            print(f"失败: {e}")
            continue

        for s in results:
            tid = s.get("taxon_id", "")
            name = s.get("taxonname", "")
            if not tid and not name:
                continue

            if tid not in species_map:
                # 罕见度：优先 taxon_id，次用 name
                ri = rarity_by_id.get(str(tid), rarity_by_name.get(name, 0.0))
                species_map[tid] = {
                    "taxon_id": tid,
                    "taxonname": name,
                    "latinname": s.get("latinname", ""),
                    "taxonordername": s.get("taxonordername", ""),
                    "taxonfamilyname": s.get("taxonfamilyname", ""),
                    "recordcount": 0,
                    "rarity_index": round(ri, 2),
                }
            species_map[tid]["recordcount"] += 1

        time.sleep(0.5)  # 避免限速

    return species_map


# ─── 写出 CSV ───
def write_species_csv(path: Path, species_map: dict):
    fieldnames = [
        "taxon_id", "taxonname", "latinname",
        "taxonordername", "taxonfamilyname",
        "recordcount", "rarity_index",
    ]
    rows = sorted(species_map.values(), key=lambda x: x["rarity_index"], reverse=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  → 已写出 {len(rows)} 种到 {path.name}")


# ─── 生成 JS 数组片段（供手动插入 HTML）───
def to_js_array(species_map: dict) -> str:
    rows = sorted(species_map.values(), key=lambda x: x["rarity_index"], reverse=True)
    items = []
    for s in rows:
        name = s["taxonname"].replace('"', '\\"')
        latin = s["latinname"].replace('"', '\\"')
        family = s["taxonfamilyname"].replace('"', '\\"')
        ri = s["rarity_index"]
        count = s["recordcount"]
        items.append(f'      ["{name}","{latin}","{family}",{ri},{count}]')
    return "[\n" + ",\n".join(items) + "\n    ]"


# ─── 主流程 ───
def main():
    print("── 读取观测记录 ──")
    records = load_observations()
    day_ids = [r["serial_id"] for r in records if not r["is_night"]]
    night_ids = [r["serial_id"] for r in records if r["is_night"]]
    print(f"  日观：{len(day_ids)} 条，夜观：{len(night_ids)} 条，合计：{len(records)} 条")

    print("\n── 读取罕见度数据 ──")
    rarity_by_id, rarity_by_name = load_rarity()
    print(f"  共 {len(rarity_by_id)} 条 taxon_id 映射，{len(rarity_by_name)} 条名称映射")

    scraper = BirdReportScraper()

    print(f"\n── 查询日观物种（{len(day_ids)} 条记录）──")
    day_species = aggregate_species(day_ids, scraper, rarity_by_id, rarity_by_name, "日观")

    print(f"\n── 查询夜观物种（{len(night_ids)} 条记录）──")
    night_species = aggregate_species(night_ids, scraper, rarity_by_id, rarity_by_name, "夜观")

    print("\n── 写出结果 ──")
    write_species_csv(OUT_DAY, day_species)
    write_species_csv(OUT_NIGHT, night_species)

    print("\n── 汇总 ──")
    print(f"  日观：{len(day_ids)} 条观测，{len(day_species)} 种")
    print(f"  夜观：{len(night_ids)} 条观测，{len(night_species)} 种")

    # 输出 JS 数组供 HTML 更新参考
    out_json = ROOT / "output" / "pudong_daynight_species.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "day": {
                "reports": len(day_ids),
                "species_count": len(day_species),
                "species": to_js_array(day_species),
            },
            "night": {
                "reports": len(night_ids),
                "species_count": len(night_species),
                "species": to_js_array(night_species),
            },
        }, f, ensure_ascii=False, indent=2)
    print(f"  → JS 数组已写出到 {out_json.name}")


if __name__ == "__main__":
    main()
