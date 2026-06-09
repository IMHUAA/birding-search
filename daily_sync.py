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
        -- 防止同一 session 中重复插入相同鸟种；NULL 值被视为互不相同（SQLite 特性），
        -- 实际数据中 taxon_name 不会为 NULL，所以不影响去重效果。
        CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_serial_taxon
            ON observations(serial_id, taxon_name);
    """)
    conn.commit()
    return conn


# ── 已存在的 serial_id 及 taxoncount ─────────────────────────────

def existing_session_map(conn: sqlite3.Connection) -> dict[str, int]:
    """返回 {serial_id: taxoncount}，用于判断哪些 session 是新增/有更新。"""
    rows = conn.execute("SELECT serial_id, taxoncount FROM sessions").fetchall()
    return {r[0]: (r[1] or 0) for r in rows}


def existing_obs_count(conn: sqlite3.Connection, serial_id: str) -> int:
    """返回某个 session 在库中已有的 observation 条数（备用校验）。"""
    row = conn.execute(
        "SELECT COUNT(*) FROM observations WHERE serial_id = ?", (serial_id,)
    ).fetchone()
    return row[0] if row else 0


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
    """批量插入鸟种明细（遇重复 serial_id+taxon_name 自动跳过）。"""
    conn.executemany(
        """
        INSERT OR IGNORE INTO observations
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


# ── 更新 session ───────────────────────────────────────────────────

def update_session(conn: sqlite3.Connection, serial_id: str, taxoncount: int, fetched_date: str):
    conn.execute(
        """
        UPDATE sessions SET taxoncount = ?, fetched_date = ?
        WHERE serial_id = ?
        """,
        (taxoncount, fetched_date, serial_id),
    )
    conn.commit()


# ── 拉取某条 session 的鸟种明细 ────────────────────────────────────

def fetch_and_insert_observations(
    scraper: BirdReportScraper, conn: sqlite3.Connection,
    serial_id: str,
) -> int:
    """拉取指定 session 的鸟种明细并写入库（重复的自动跳过），返回实际新增条数。"""
    before = existing_obs_count(conn, serial_id)
    params = SearchParams(serial_id=serial_id)
    species = scraper.search(params)
    insert_observations(conn, serial_id, species)
    after = existing_obs_count(conn, serial_id)
    return after - before


# ── 主流程 ────────────────────────────────────────────────────────

def sync(date: str, db_path: str):
    print(f"目标日期: {date}")
    print(f"数据库: {db_path}")
    print()

    conn = init_db(db_path)
    scraper = BirdReportScraper()
    session_map = existing_session_map(conn)  # {serial_id: db_taxoncount}
    fetched_date = datetime.now().strftime("%Y-%m-%d")

    # 1. 拉取各地区 sessions，区分「新增」与「有更新」
    new_sessions: list[dict] = []
    updated_sessions: list[dict] = []
    total_api = 0

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
            total_api += len(sessions)
            new = []
            updated = []
            for s in sessions:
                sid = str(s.get("serial_id", ""))
                api_count = int(s.get("taxoncount") or 0)
                if sid not in session_map:
                    new.append(s)
                elif api_count > session_map[sid]:
                    updated.append(s)
            new_sessions.extend(new)
            updated_sessions.extend(updated)
            print(
                f"{province}: {len(sessions)} 条 sessions"
                f" — 新增 {len(new)}，有更新 {len(updated)}"
            )
        except Exception as e:
            print(f"{province}: 查询失败 — {e}", file=sys.stderr)
        time.sleep(REQUEST_SLEEP)

    if not new_sessions and not updated_sessions:
        print(f"\n共 {total_api} 条 session，无新增也无更新，退出。")
        conn.close()
        return

    # 2. 写入新增 sessions
    if new_sessions:
        insert_sessions(conn, new_sessions, fetched_date)
        print(f"\n写入 {len(new_sessions)} 条新 session")
    else:
        print(f"\n无新增 session")

    if updated_sessions:
        print(f"检测到 {len(updated_sessions)} 条 session 鸟种数有增加")

    # 3. 拉取鸟种明细（新 session + 有更新的旧 session 合并处理）
    all_to_fetch = list(new_sessions) + list(updated_sessions)
    total_new_obs = 0
    total_updated_obs = 0

    for i, session in enumerate(all_to_fetch, 1):
        sid = str(session.get("serial_id", ""))
        point = session.get("point_name", "")
        api_count = session.get("taxoncount", "?")
        is_updated = sid in session_map  # 在库中已存在 = 是更新

        tag = "更新" if is_updated else "新增"
        print(
            f"  [{i}/{len(all_to_fetch)}] [{tag}] {sid} {point} ({api_count}种) ...",
            end=" ", flush=True,
        )
        try:
            added = fetch_and_insert_observations(scraper, conn, sid)
            if is_updated:
                total_updated_obs += added
                # 同步更新 session 表的 taxoncount
                update_session(conn, sid, api_count, fetched_date)
            else:
                total_new_obs += added
            print(f"ok (+{added}种)")
        except Exception as e:
            print(f"失败: {e}", file=sys.stderr)
        time.sleep(REQUEST_SLEEP)

    conn.close()
    print(
        f"\n完成: 新增 {len(new_sessions)} 条 session（{total_new_obs} 条 observation），"
        f"更新 {len(updated_sessions)} 条 session（+{total_updated_obs} 条 observation）"
    )


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
