#!/usr/bin/env python3
"""
嘉兴市近一周鸟种查询 + 与用户记录对比
输出：可加新 / 已观测 两大分类
"""

import csv
import io
import os
import sys
import time
from datetime import datetime, timedelta

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from birdreport_scraper import BirdReportScraper, SearchParams

# ── 时间范围：近7天 ──────────────────────────────────────────────
TODAY = datetime.now()
END_DATE = TODAY.strftime("%Y-%m-%d")
START_DATE = (TODAY - timedelta(days=7)).strftime("%Y-%m-%d")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
USER_LIST_PATH = os.path.join(SCRIPT_DIR, "用户1鸟种记录.csv")


def load_user_species(csv_path: str) -> set[str]:
    """读取用户鸟种记录，返回拉丁学名集合。"""
    species = set()
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Scientific Name", "").strip()
            if name:
                species.add(name)
    return species


def query_jiaxing(scraper: BirdReportScraper) -> list[dict]:
    """查询嘉兴市近一周鸟种。"""
    params = SearchParams(
        start_time=START_DATE,
        end_time=END_DATE,
        province="浙江省",
        city="嘉兴市",
    )
    scraper._last_url = params.to_url()
    print(f"查询URL: {params.to_url()}", file=sys.stderr)
    results = scraper.search(params)
    return sorted(results, key=lambda x: int(x.get("taxon_id", 0)))


def main():
    print(f"查询时间范围: {START_DATE} ~ {END_DATE}", file=sys.stderr)

    # 1. 加载用户记录
    user_species = load_user_species(USER_LIST_PATH)
    print(f"用户已记录鸟种数: {len(user_species)}", file=sys.stderr)

    # 2. 查询嘉兴
    scraper = BirdReportScraper()
    print("正在查询嘉兴市近一周鸟种...", file=sys.stderr)
    jiaxing_birds = query_jiaxing(scraper)
    print(f"嘉兴市近一周共记录: {len(jiaxing_birds)} 种", file=sys.stderr)

    # 3. 分类
    new_birds = []      # 可加新（用户没有的）
    seen_birds = []     # 已观测（用户已有的）

    for bird in jiaxing_birds:
        latin = bird.get("latinname", "").strip()
        # 用拉丁名匹配；少数情况下用户记录里带变种括号，做前缀匹配兜底
        in_user = latin in user_species or any(
            ul.startswith(latin.split()[0] + " " + latin.split()[1])
            for ul in user_species if len(latin.split()) >= 2
        ) and latin in " ".join(user_species)

        # 更准确：直接全字匹配优先，再检查是否用户list里有任一以latin开头的条目
        exact_match = latin in user_species
        if exact_match:
            seen_birds.append(bird)
        else:
            new_birds.append(bird)

    # 4. 输出
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print()
    print(f"嘉兴市近一周鸟种对比报告")
    print(f"查询时间范围: {START_DATE} ~ {END_DATE}")
    print(f"生成时间: {run_time}")
    print(f"嘉兴共记录 {len(jiaxing_birds)} 种 | 可加新 {len(new_birds)} 种 | 已观测 {len(seen_birds)} 种")
    print("=" * 70)

    # ── 可加新 ──────────────────────────────────────────────────
    print(f"\n【可加新】({len(new_birds)} 种)")
    print(f"  {'序':<4} {'中文名':<16} {'拉丁学名':<36} {'科':<12} {'次数':>4}")
    print(f"  {'-'*4} {'-'*16} {'-'*36} {'-'*12} {'-'*4}")
    for i, b in enumerate(new_birds, 1):
        print(
            f"  {i:<4} {b.get('taxonname',''):<16} "
            f"{b.get('latinname',''):<36} "
            f"{b.get('taxonfamilyname',''):<12} "
            f"{b.get('recordcount',0):>4}"
        )

    # ── 已观测 ──────────────────────────────────────────────────
    print(f"\n【已观测】({len(seen_birds)} 种)")
    print(f"  {'序':<4} {'中文名':<16} {'拉丁学名':<36} {'科':<12} {'次数':>4}")
    print(f"  {'-'*4} {'-'*16} {'-'*36} {'-'*12} {'-'*4}")
    for i, b in enumerate(seen_birds, 1):
        print(
            f"  {i:<4} {b.get('taxonname',''):<16} "
            f"{b.get('latinname',''):<36} "
            f"{b.get('taxonfamilyname',''):<12} "
            f"{b.get('recordcount',0):>4}"
        )

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
