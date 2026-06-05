#!/usr/bin/env python3
"""
查询昨天17:00之后到现在的各地点新增鸟种记录
- 今日(06-04)鸟种：直接查今天
- 昨天17点后报告：查06-03~06-04报告，按 start_time >= "2026-06-03 17:00:00" 过滤
"""

import io
import os
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from birdreport_scraper import BirdReportScraper, SearchParams

SINCE_DATETIME = "2026-06-03 17:00:00"
REPORT_START = "2026-06-03"
REPORT_END   = "2026-06-04"
TODAY        = "2026-06-04"

GROUPS = [
    {
        "id": "taicang", "name": "江苏太仓",
        "queries": [
            {"province": "江苏省", "city": "苏州市", "district": "太仓市", "pointname": "江滩"},
            {"province": "江苏省", "city": "苏州市", "district": "太仓市", "pointname": "阅兵"},
            {"province": "江苏省", "city": "苏州市", "district": "太仓市", "pointname": "浏河"},
        ],
    },
    {
        "id": "ningbo", "name": "浙江宁波·杭州湾",
        "queries": [
            {"province": "浙江省", "city": "宁波市", "district": "", "pointname": "杭州湾"},
        ],
    },
    {
        "id": "pudong", "name": "上海浦东",
        "queries": [
            {"province": "上海市", "city": "上海市", "district": "浦东新区", "pointname": "南汇东滩"},
            {"province": "上海市", "city": "上海市", "district": "浦东新区", "pointname": "南汇观海嘴"},
        ],
    },
    {
        "id": "fengxian", "name": "上海奉贤",
        "queries": [
            {"province": "上海市", "city": "上海市", "district": "奉贤区", "pointname": "渔人码头"},
            {"province": "上海市", "city": "上海市", "district": "奉贤区", "pointname": "碧海金沙"},
        ],
    },
    {
        "id": "jiading", "name": "上海嘉定·嘉北",
        "queries": [
            {"province": "上海市", "city": "上海市", "district": "嘉定区", "pointname": "嘉北"},
        ],
    },
]


def query_taxons_today(scraper: BirdReportScraper, group: dict) -> list[dict]:
    """查询今日(06-04)鸟种，按 taxon_id 合并。"""
    merged: dict[str, dict] = {}
    for q in group["queries"]:
        params = SearchParams(
            start_time=TODAY, end_time=TODAY,
            province=q.get("province", ""), city=q.get("city", ""),
            district=q.get("district", ""), pointname=q.get("pointname", ""),
        )
        scraper._last_url = params.to_url()
        label = q.get("pointname") or q.get("district") or "?"
        try:
            results = scraper.search(params)
            print(f"    [{label}] 今日鸟种: {len(results)} 种", file=sys.stderr)
            for item in results:
                tid = str(item.get("taxon_id", ""))
                if tid in merged:
                    merged[tid]["recordcount"] = (
                        merged[tid].get("recordcount", 0) + item.get("recordcount", 0)
                    )
                else:
                    merged[tid] = dict(item)
        except Exception as e:
            print(f"    [{label}] 今日鸟种查询失败: {e}", file=sys.stderr)
        time.sleep(1.5)
    return sorted(merged.values(), key=lambda x: x.get("recordcount", 0), reverse=True)


def query_reports_since17(scraper: BirdReportScraper, group: dict) -> list[dict]:
    """查询 06-03~06-04 的观测报告，过滤出 start_time >= 17:00 的记录。"""
    filtered: list[dict] = []
    seen_ids: set[str] = set()

    for q in group["queries"]:
        params = SearchParams(
            start_time=REPORT_START, end_time=REPORT_END,
            province=q.get("province", ""), city=q.get("city", ""),
            district=q.get("district", ""), pointname=q.get("pointname", ""),
        )
        scraper._last_url = params.to_report_url()
        label = q.get("pointname") or q.get("district") or "?"
        try:
            results = scraper.search_reports(params)
            new_cnt = 0
            for item in results:
                sid = str(item.get("serial_id", ""))
                start = item.get("start_time", "")
                if sid not in seen_ids and start >= SINCE_DATETIME:
                    seen_ids.add(sid)
                    filtered.append(item)
                    new_cnt += 1
            print(f"    [{label}] 共 {len(results)} 条 → 17点后 {new_cnt} 条", file=sys.stderr)
        except Exception as e:
            print(f"    [{label}] 报告查询失败: {e}", file=sys.stderr)
        time.sleep(1.5)

    filtered.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    return filtered


def print_group(group: dict, taxons: list[dict], reports: list[dict]):
    name = group["name"]
    print(f"\n{'─'*60}")
    print(f"【{name}】")
    print(f"{'─'*60}")

    # ── 今日鸟种 ───────────────────────────────────────────────
    print(f"\n  ▶ 今日(06-04)鸟种  共 {len(taxons)} 种")
    if taxons:
        print(f"  {'#':<3} {'中文名':<14} {'科':<10} {'拉丁学名':<34} {'次数':>4}")
        print(f"  {'─'*3} {'─'*14} {'─'*10} {'─'*34} {'─'*4}")
        for i, t in enumerate(taxons, 1):
            print(
                f"  {i:<3} {t.get('taxonname',''):<14} "
                f"{t.get('taxonfamilyname',''):<10} "
                f"{t.get('latinname',''):<34} "
                f"{t.get('recordcount', 0):>4}"
            )
    else:
        print("  （暂无记录）")

    # ── 昨天17点后的观测报告 ────────────────────────────────────
    print(f"\n  ▶ 昨天(06-03) 17:00 后 + 今日 观测报告  共 {len(reports)} 条")
    if reports:
        print(f"  {'开始时间':<18} {'观测点':<24} {'观测者':<12} {'种数':>4}")
        print(f"  {'─'*18} {'─'*24} {'─'*12} {'─'*4}")
        for r in reports:
            start = r.get("start_time", "")
            point = r.get("point_name", "")
            user  = r.get("username", "")
            cnt   = r.get("taxoncount", "")
            ts = start[:16] if len(start) >= 16 else start
            print(f"  {ts:<18} {point:<24} {user:<12} {str(cnt):>4}")
    else:
        print("  （暂无记录）")


def main():
    scraper = BirdReportScraper()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"查询新增记录（{SINCE_DATETIME} 之后）")
    print(f"当前时间: {now}")
    print(f"  • 鸟种：查今日(06-04)，全部为新增")
    print(f"  • 报告：查06-03~06-04，过滤 start_time >= 17:00")

    for group in GROUPS:
        print(f"\n► 正在查询: {group['name']}", file=sys.stderr)
        taxons  = query_taxons_today(scraper, group)
        reports = query_reports_since17(scraper, group)
        print_group(group, taxons, reports)

    print(f"\n{'='*60}")
    print("查询完成")


if __name__ == "__main__":
    main()
