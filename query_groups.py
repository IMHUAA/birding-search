#!/usr/bin/env python3
"""
近10天鸟种记录查询 - 按5个地点分组导出到 output 文件夹

地点分组:
  1. 江苏太仓：江滩湿地公园 + 阅兵 + 浏河镇
  2. 浙江宁波：杭州湾国家湿地公园
  3. 上海浦东：南汇东滩 + 南汇观海嘴公园
  4. 上海奉贤：渔人码头 + 碧海金沙 + 小魔术林
  5. 上海嘉定：嘉北郊野公园
"""

import csv
import io
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from birdreport_scraper import BirdReportScraper, SearchParams

# ─── 时间范围：近10天 ──────────────────────────────────────────
TODAY = datetime(2026, 6, 3)
END_DATE = TODAY.strftime("%Y-%m-%d")
START_DATE = (TODAY - timedelta(days=9)).strftime("%Y-%m-%d")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# ─── 地点分组定义 ──────────────────────────────────────────────
# 每个 queries 条目会分别查询后合并，pointname 支持模糊匹配
GROUPS = [
    {
        "id": "taicang",
        "name": "江苏太仓：江滩湿地公园+阅兵+浏河镇",
        "short": "江苏太仓",
        "queries": [
            {"province": "江苏省", "city": "苏州市", "district": "太仓市", "pointname": "江滩"},
            {"province": "江苏省", "city": "苏州市", "district": "太仓市", "pointname": "阅兵"},
            {"province": "江苏省", "city": "苏州市", "district": "太仓市", "pointname": "浏河"},
        ],
    },
    {
        "id": "ningbo",
        "name": "浙江宁波：杭州湾国家湿地公园",
        "short": "浙江宁波",
        "queries": [
            {"province": "浙江省", "city": "宁波市", "district": "", "pointname": "杭州湾"},
        ],
    },
    {
        "id": "pudong",
        "name": "上海浦东：南汇东滩+南汇观海嘴公园",
        "short": "上海浦东",
        "queries": [
            {"province": "上海市", "city": "上海市", "district": "浦东新区", "pointname": "南汇东滩"},
            {"province": "上海市", "city": "上海市", "district": "浦东新区", "pointname": "南汇观海嘴"},
        ],
    },
    {
        "id": "fengxian",
        "name": "上海奉贤：渔人码头+碧海金沙+小魔术林",
        "short": "上海奉贤",
        "queries": [
            {"province": "上海市", "city": "上海市", "district": "奉贤区", "pointname": "渔人码头"},
            {"province": "上海市", "city": "上海市", "district": "奉贤区", "pointname": "碧海金沙"},
            {"province": "上海市", "city": "上海市", "district": "奉贤区", "pointname": "小魔术林"},
        ],
    },
    {
        "id": "jiading",
        "name": "上海嘉定：嘉北郊野公园",
        "short": "上海嘉定",
        "queries": [
            {"province": "上海市", "city": "上海市", "district": "嘉定区", "pointname": "嘉北"},
        ],
    },
]


# ─── 查询函数 ──────────────────────────────────────────────────

def query_taxons(scraper: BirdReportScraper, group: dict) -> list[dict]:
    """查询分组内所有子地点的鸟种，按 taxon_id 合并记录次数。"""
    merged: dict[str, dict] = {}

    for q in group["queries"]:
        params = SearchParams(
            start_time=START_DATE,
            end_time=END_DATE,
            province=q.get("province", ""),
            city=q.get("city", ""),
            district=q.get("district", ""),
            pointname=q.get("pointname", ""),
        )
        scraper._last_url = params.to_url()
        label = q.get("pointname") or q.get("district") or q.get("city", "")
        try:
            results = scraper.search(params)
            print(f"    鸟种查询「{label}」: {len(results)} 种", file=sys.stderr)
            for item in results:
                tid = str(item.get("taxon_id", ""))
                if tid in merged:
                    merged[tid]["recordcount"] = (
                        merged[tid].get("recordcount", 0) + item.get("recordcount", 0)
                    )
                else:
                    merged[tid] = dict(item)
        except Exception as e:
            print(f"    鸟种查询失败「{label}」: {e}", file=sys.stderr)
        time.sleep(1.5)

    return sorted(merged.values(), key=lambda x: x.get("recordcount", 0), reverse=True)


def query_reports(scraper: BirdReportScraper, group: dict) -> list[dict]:
    """查询分组内所有子地点的观测记录，按 serial_id 去重合并。"""
    all_reports: list[dict] = []
    seen_ids: set[str] = set()

    for q in group["queries"]:
        params = SearchParams(
            start_time=START_DATE,
            end_time=END_DATE,
            province=q.get("province", ""),
            city=q.get("city", ""),
            district=q.get("district", ""),
            pointname=q.get("pointname", ""),
        )
        scraper._last_url = params.to_report_url()
        label = q.get("pointname") or q.get("district") or q.get("city", "")
        try:
            results = scraper.search_reports(params)
            new_count = 0
            for item in results:
                sid = str(item.get("serial_id", ""))
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    all_reports.append(item)
                    new_count += 1
            print(f"    观测记录「{label}」: {len(results)} 条 (新增 {new_count} 条)", file=sys.stderr)
        except Exception as e:
            print(f"    观测记录查询失败「{label}」: {e}", file=sys.stderr)
        time.sleep(1.5)

    # 按日期倒序
    all_reports.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    return all_reports


# ─── 导出函数 ──────────────────────────────────────────────────

TAXON_FIELDS = [
    "taxon_id", "taxonname", "latinname",
    "taxonordername", "taxonfamilyname", "recordcount",
]
REPORT_FIELDS = [
    "serial_id", "start_time", "end_time",
    "point_name", "district_name", "username", "taxoncount",
]


def export_group_csv(group: dict, taxons: list[dict], reports: list[dict]) -> tuple[str, str]:
    """导出单个分组的鸟种和观测记录为两个 CSV 文件。"""
    gid = group["id"]

    # 鸟种 CSV
    taxon_path = os.path.join(OUTPUT_DIR, f"{gid}_鸟种.csv")
    with open(taxon_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=TAXON_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(taxons)

    # 观测记录 CSV
    report_path = os.path.join(OUTPUT_DIR, f"{gid}_观测记录.csv")
    with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(reports)

    return taxon_path, report_path


def build_summary_block(group: dict, taxons: list[dict], reports: list[dict]) -> str:
    """生成单个分组的汇总文本块。"""
    name = group["name"]
    species_count = len(taxons)
    report_count = len(reports)

    lines = [
        f"【{name}】",
        f"  查询时间范围  : {START_DATE} ~ {END_DATE}",
        f"  观测记录总数  : {report_count} 条",
        f"  鸟种总数      : {species_count} 种",
        "",
        f"  {'排名':<4} {'中文名':<14} {'拉丁学名':<34} {'科':<10} {'记录次数':>6}",
        f"  {'-'*4} {'-'*14} {'-'*34} {'-'*10} {'-'*6}",
    ]
    for i, item in enumerate(taxons, 1):
        lines.append(
            f"  {i:<4} {item.get('taxonname',''):<14} "
            f"{item.get('latinname',''):<34} "
            f"{item.get('taxonfamilyname',''):<10} "
            f"{item.get('recordcount', 0):>6}"
        )
    lines.append("")
    return "\n".join(lines)


# ─── 主流程 ────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    scraper = BirdReportScraper()
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    header = (
        f"近10天鸟种记录查询报告\n"
        f"查询范围: {START_DATE} ~ {END_DATE}\n"
        f"生成时间: {run_time}\n"
        f"{'='*60}\n"
    )
    print(header)

    summary_blocks: list[str] = [header]
    all_group_data: list[dict] = []

    for group in GROUPS:
        print(f"\n► 查询分组: {group['name']}", file=sys.stderr)

        taxons = query_taxons(scraper, group)
        reports = query_reports(scraper, group)

        taxon_path, report_path = export_group_csv(group, taxons, reports)
        print(f"  已导出: {os.path.basename(taxon_path)}, {os.path.basename(report_path)}", file=sys.stderr)

        block = build_summary_block(group, taxons, reports)
        summary_blocks.append(block)
        print(block)

        all_group_data.append({
            "group": group["name"],
            "short": group["short"],
            "report_count": len(reports),
            "species_count": len(taxons),
            "top_species": [
                {
                    "name": t.get("taxonname", ""),
                    "latin": t.get("latinname", ""),
                    "count": t.get("recordcount", 0),
                }
                for t in taxons[:20]
            ],
        })

    # ── 汇总对比表 ──────────────────────────────────────────────
    compare_header = (
        f"\n{'='*60}\n"
        f"  五地分组对比\n"
        f"  {'地点':<20} {'观测记录数':>8} {'鸟种数':>6}\n"
        f"  {'-'*20} {'-'*8} {'-'*6}"
    )
    compare_rows = []
    for d in all_group_data:
        compare_rows.append(f"  {d['short']:<20} {d['report_count']:>8} {d['species_count']:>6}")
    compare_block = compare_header + "\n" + "\n".join(compare_rows) + "\n"

    summary_blocks.append(compare_block)
    print(compare_block)

    # ── 写汇总 TXT ─────────────────────────────────────────────
    summary_path = os.path.join(OUTPUT_DIR, "汇总报告.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_blocks))

    # ── 写汇总 JSON（方便后续处理）──────────────────────────────
    json_path = os.path.join(OUTPUT_DIR, "汇总数据.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_group_data, f, ensure_ascii=False, indent=2)

    print(f"\n所有文件已导出到: {OUTPUT_DIR}")
    print(f"  • 汇总报告.txt")
    print(f"  • 汇总数据.json")
    for group in GROUPS:
        gid = group["id"]
        print(f"  • {gid}_鸟种.csv")
        print(f"  • {gid}_观测记录.csv")


if __name__ == "__main__":
    main()
