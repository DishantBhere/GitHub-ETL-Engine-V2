# -*- coding: utf-8 -*-
"""Daily Excel updater: SQLite -> CSVs + data sheets inside the dashboard template.

The Dashboard sheet is NEVER touched. Only the *_Data sheets + Config are rewritten.
Your hand edits to Dashboard (colors, logo, charts, text) survive.
"""

import logging
import os
import sqlite3
import sys

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

DB_PATH = os.environ.get("DB_PATH", "github_repos.db")
TEMPLATE_PATH = os.environ.get("TEMPLATE_PATH", "GitHub_Trending_Dashboard.xlsx")
DATA_DIR = os.environ.get("DATA_DIR", "data")
DAYS_BACK = int(os.environ.get("DAYS_BACK", 30))  # same default as pipeline.py

REPO_COLS = [
    "rank", "name", "owner", "owner_type", "stars", "forks", "open_issues",
    "viral_level", "language", "topics", "license", "description", "url",
    "is_fork", "is_archived", "created_at", "created_time", "pushed_at",
    "pushed_time", "age_hours", "age_days", "stars_per_hour", "fork_ratio",
    "loaded_at", "loaded_time",
]
LANG_COLS = ["language", "repos", "total_stars", "avg_stars", "avg_stars_per_hour"]
CAT_COLS = ["viral_level", "repos"]
VEL_COLS = ["name", "stars_per_hour", "stars", "age_days", "language"]
TOPIC_COLS = ["topic", "repos", "total_stars"]
ACT_COLS = ["last_push", "repos"]
TIER_ORDER = ["Normal", "Popular", "Trending", "Rising Star", "Viral",
              "Super Viral", "Iconic", "Legendary", "Hall of Fame"]
ACT_ORDER = ["Today", "1-2 days", "3-7 days", "8+ days"]

# sheet name -> (table name, columns, csv name)
SHEETS = {
    "Repo_Data": ("tblRepos", REPO_COLS, "repos.csv"),
    "Language_Data": ("tblLanguages", LANG_COLS, "languages.csv"),
    "Category_Data": ("tblCategories", CAT_COLS, "categories.csv"),
    "Velocity_Data": ("tblVelocity", VEL_COLS, "velocity.csv"),
    "Topic_Data": ("tblTopics", TOPIC_COLS, "topics.csv"),
    "Activity_Data": ("tblActivity", ACT_COLS, "activity.csv"),
}
NUM_FMT = {
    "stars": "#,##0", "forks": "#,##0", "total_stars": "#,##0", "avg_stars": "#,##0",
    "stars_per_hour": "0.00", "avg_stars_per_hour": "0.00",
    "fork_ratio": "0.000", "age_days": "0.0",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("excel_updater")


def to_py(v):
    if v is None or pd.isna(v):
        return None
    return v.item() if hasattr(v, "item") else v


def read_repos():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM repos ORDER BY rank", conn)
    finally:
        conn.close()
    missing = [c for c in REPO_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"DB missing columns {missing}. Run pipeline.py first.")
    if df.empty:
        raise SystemExit("repos table is empty. Run pipeline.py first.")
    return df[REPO_COLS]


def build_frames(repos):
    langs = (repos.groupby("language")
             .agg(repos=("url", "count"), total_stars=("stars", "sum"),
                  avg_stars=("stars", "mean"), avg_stars_per_hour=("stars_per_hour", "mean"))
             .reset_index()
             .sort_values(["repos", "total_stars"], ascending=False)
             .round({"avg_stars": 0, "avg_stars_per_hour": 2}))
    cats = (repos["viral_level"].value_counts()
            .reindex(TIER_ORDER, fill_value=0)
            .rename_axis("viral_level").reset_index(name="repos"))
    vel = repos.nlargest(10, "stars_per_hour")[VEL_COLS]

    t = repos[["topics", "stars"]].copy()
    t["topic"] = t["topics"].fillna("").str.split(",")
    t = t.explode("topic")
    t["topic"] = t["topic"].str.strip()
    t = t[t["topic"] != ""]
    topics = (t.groupby("topic").agg(repos=("stars", "count"), total_stars=("stars", "sum"))
              .reset_index().sort_values(["repos", "total_stars"], ascending=False).head(10))

    loaded = pd.to_datetime(repos["loaded_at"], format="%d-%m-%y")
    pushed = pd.to_datetime(repos["pushed_at"], format="%d-%m-%y")
    days = (loaded - pushed).dt.days
    bucket = pd.cut(days, bins=[-10_000, 0, 2, 7, 10_000], labels=ACT_ORDER)
    act = (bucket.value_counts().reindex(ACT_ORDER, fill_value=0)
           .rename_axis("last_push").reset_index(name="repos"))

    return {"Repo_Data": repos, "Language_Data": langs[LANG_COLS],
            "Category_Data": cats[CAT_COLS], "Velocity_Data": vel,
            "Topic_Data": topics[TOPIC_COLS], "Activity_Data": act[ACT_COLS]}


def write_sheet(wb, sheet, df):
    table_name, cols, _ = SHEETS[sheet]
    ws = wb[sheet]
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)  # clear old data, keep header + table + widths
    for c, col in enumerate(cols, 1):
        ws.cell(1, c, col)
    for r, row in enumerate(df.itertuples(index=False, name=None), 2):
        for c, v in enumerate(row, 1):
            cell = ws.cell(r, c, to_py(v))
            fmt = NUM_FMT.get(cols[c - 1])
            if fmt:
                cell.number_format = fmt
    tbl = ws.tables[table_name]
    last = max(len(df) + 1, 2)  # Excel table needs header + >=1 row
    tbl.ref = f"A1:{get_column_letter(len(cols))}{last}"
    if tbl.autoFilter is not None:
        tbl.autoFilter.ref = tbl.ref


def main():
    repos = read_repos()
    frames = build_frames(repos)

    os.makedirs(DATA_DIR, exist_ok=True)
    for sheet, df in frames.items():
        df.to_csv(os.path.join(DATA_DIR, SHEETS[sheet][2]), index=False)
    log.info("CSVs written to %s/", DATA_DIR)

    if not os.path.exists(TEMPLATE_PATH):
        raise SystemExit(f"Template not found: {TEMPLATE_PATH}. Run build_template.py once.")
    wb = load_workbook(TEMPLATE_PATH)
    for sheet, df in frames.items():
        write_sheet(wb, sheet, df)

    cfg = wb["Config"]
    cfg["B1"] = f"{repos['loaded_at'].iloc[0]} {repos['loaded_time'].iloc[0]}"
    cfg["B2"] = DAYS_BACK
    cfg["B3"] = len(repos)
    try:
        wb.save(TEMPLATE_PATH)
    except PermissionError:
        raise SystemExit(f"Cannot write {TEMPLATE_PATH}. Close it in Excel, then run again.")
    log.info("Excel updated: %s (%d repos)", TEMPLATE_PATH, len(repos))


if __name__ == "__main__":
    sys.exit(main())
