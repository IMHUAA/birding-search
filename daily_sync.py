#!/usr/bin/env python3
"""
江浙沪观鸟数据库每日同步
==========================
每天抓取观鸟记录中心的江浙沪观测记录（地点+时间+鸟种），
存入本地 SQLite 数据库，供其他项目直接读取查询。

用法:
  python daily_sync.py                  # 抓取昨天的数据
  python daily_sync.py --date 2026-06-04  # 补抓指定日期
  python daily_sync.py --db /path/to/birding.db  # 指定数据库路径

表结构:
  sessions     — 每次出行观测（地点/时间/观测者）
  observations — 每次观测中记录的鸟种（鸟种与地点的对应关系）

依赖: pip install pycryptodome requests
"""

import argparse
import io
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from birdreport_scraper import BirdReportScraper, SearchParams

# ── 配置 ─────────────────────────────────────────────────────────
DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "birding.db")

REGIONS = [
    {"province": "浙江省", "city": ""},
    {"province": "江苏省", "city": ""},
    {"province": "上海市", "city": "上海市"},
]

REQUEST_SLEEP = 0.8  # 两次请求之间的间隔（秒），避免被限速


# ── 数据库初始化 ──────────────────────────────────────────────────

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            serial_id    TEXT PRIMARY KEY,
            province     TEXT,
            city         TEXT,
            district     TEXT,
            point_name   TEXT,
            username     TEXT,
            userid       INTEGER,
            start_time   TEXT,
            end_time     TEXT,
            taxoncount   INTEGER,
            state        INTEGER,
            fetched_date TEXT
        );

        CREATE TABLE IF NOT EXISTS observations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            serial_id    TEXT NOT NULL REFERENCES sessions(serial_id),
            taxon_id     INTEGER,
            taxon_name   TEXT,
            latin_name   TEXT,
            english_name TEXT,
            family_name  TEXT,
            order_name   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_obs_taxon  ON observations(taxon_name);
        CREATE INDEX IF NOT EXISTS idx_obs_serial ON observations(serial_id);
        CREATE INDEX IF NOT EXISTS idx_ses_point  ON sessions(point_name);
        CREATE INDEX IF NOT EXISTS idx_ses_date   ON sessions(start_time);
        CREATE INDEX IF NOT EXISTS idx_ses_province ON sessions(province);
    """)
    conn.commit()
    return conn


# ── 已存在的 serial_id 集合 ───────────────────────────────────────

def existing_serial_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT serial_id FROM sessions").fetchall()
    return {r[0] for r in rows}


# ── 写入 sessions ─────────────────────────────────────────────────

def insert_sessions(conn: sqlite3.Connection, sessions: list[dict], fetched_date: str):
    conn.executemany(
        """
        INSERT OR IGNORE INTO sessions
            (serial_id, province, city, district, point_name,
             username, userid, start_time, end_time, taxoncount, state, fetched_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                s.get("serial_id", ""),
                s.get("province_name", ""),
                s.get("city_name", ""),
                s.get("district_name", ""),
                s.get("point_name", ""),
                s.get("username", ""),
                s.get("userid"),
                s.get("start_time", ""),
                s.get("end_time", ""),
                s.get("taxoncount"),
                s.get("state"),
                fetched_date,
            )
            for s in sessions
        ],
    )
    conn.commit()


# ── 写入 observations ─────────────────────────────────────────────

def insert_observations(conn: sqlite3.Connection, serial_id: str, species: list[dict]):
    conn.executemany(
        """
        INSERT INTO observations
            (serial_id, taxon_id, taxon_name, latin_name, english_name, family_name, order_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                serial_id,
                s.get("taxon_id"),
                s.get("taxonname", ""),
                s.get("latinname", ""),
                s.get("englishname", ""),
                s.get("taxonfamilyname", ""),
                s.get("taxonordername", ""),
            )
            for s in species
        ],
    )
    conn.commit()


# ── 主流程 ────────────────────────────────────────────────────────

def sync(date: str, db_path: str):
    print(f"目标日期: {date}")
    print(f"数据库: {db_path}")
    print()

    conn = init_db(db_path)
    scraper = BirdReportScraper()
    existing = existing_serial_ids(conn)
    fetched_date = datetime.now().strftime("%Y-%m-%d")

    # 1. 拉取各地区 sessions
    new_sessions: list[dict] = []
    for region in REGIONS:
        province = region["province"]
        city = region.get("city", "")
        params = SearchParams(
            province=province,
            city=city,
            start_time=date,
            end_time=date,
        )
        scraper._last_url = params.to_report_url()
        try:
            sessions = scraper.search_reports(params)
            # 过滤已存在的
            new = [s for s in sessions if str(s.get("serial_id", "")) not in existing]
            print(f"{province}: {len(sessions)} 条 sessions，其中 {len(new)} 条新增")
            new_sessions.extend(new)
        except Exception as e:
            print(f"{province}: 查询失败 — {e}", file=sys.stderr)
        time.sleep(REQUEST_SLEEP)

    if not new_sessions:
        print("\n没有新数据，退出。")
        conn.close()
        return

    # 2. 写入 sessions
    insert_sessions(conn, new_sessions, fetched_date)
    print(f"\n写入 {len(new_sessions)} 条 sessions")

    # 3. 逐条拉取鸟种明细
    total_obs = 0
    for i, session in enumerate(new_sessions, 1):
        sid = str(session.get("serial_id", ""))
        point = session.get("point_name", "")
        count = session.get("taxoncount", "?")
        print(f"  [{i}/{len(new_sessions)}] {sid} {point} ({count}种) ...", end=" ", flush=True)
        try:
            params = SearchParams(serial_id=sid)
            species = scraper.search(params)
            insert_observations(conn, sid, species)
            total_obs += len(species)
            print(f"ok ({len(species)}种)")
        except Exception as e:
            print(f"失败: {e}", file=sys.stderr)
        time.sleep(REQUEST_SLEEP)

    conn.close()
    print(f"\n完成: 新增 {len(new_sessions)} 条 sessions，{total_obs} 条 observations")


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="江浙沪观鸟数据库每日同步")
    parser.add_argument(
        "--date",
        default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="抓取日期 YYYY-MM-DD（默认昨天）",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"SQLite 数据库路径（默认 {DEFAULT_DB}）",
    )
    args = parser.parse_args()
    sync(args.date, args.db)


if __name__ == "__main__":
    main()
