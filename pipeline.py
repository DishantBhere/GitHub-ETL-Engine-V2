# -*- coding: utf-8 -*-
"""GitHub new-repos ETL: GitHub Search API -> pandas -> SQLite (single table: repos)."""

import os
import sys
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import requests

# ---------- Config ----------
DAYS_BACK = int(os.environ.get("DAYS_BACK", 30))
PER_PAGE = 100
DB_PATH = os.environ.get("DB_PATH", "github_repos.db")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")  # "UTC" for raw GitHub time

CATEGORIES = [
    (25_000, "Hall of Fame"),
    (10_000, "Legendary"),
    (5_000, "Iconic"),
    (2_500, "Super Viral"),
    (1_000, "Viral"),
    (500, "Rising Star"),
    (100, "Trending"),
    (50, "Popular"),
]


def categorize(stars):
    for threshold, name in CATEGORIES:
        if stars >= threshold:
            return name
    return "Normal"


def split_dt(series):
    """ISO UTC string -> (dd-mm-yy date, hh:mm AM/PM time) in TIMEZONE."""
    dt = pd.to_datetime(series, utc=True).dt.tz_convert(TIMEZONE)
    return dt.dt.strftime("%d-%m-%y"), dt.dt.strftime("%I:%M %p")


# ---------- Extract ----------
since = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")

url = "https://api.github.com/search/repositories"
params = {
    "q": f"created:>{since}",
    "sort": "stars",
    "order": "desc",
    "per_page": PER_PAGE,
}
headers = {"Accept": "application/vnd.github+json"}
token = os.environ.get("GITHUB_TOKEN")
if token:
    headers["Authorization"] = f"Bearer {token}"

response = requests.get(url, params=params, headers=headers, timeout=30)
response.raise_for_status()
items = response.json().get("items", [])

if not items:
    print("No repos returned. Exit.")
    sys.exit(0)

# ---------- Transform ----------
repos = []
for repo in items:
    repos.append({
        "name": repo["name"],
        "owner": repo["owner"]["login"],
        "stars": repo["stargazers_count"],
        "forks": repo["forks_count"],
        "language": repo["language"] or "Unknown",
        "description": repo["description"],
        "url": repo["html_url"],
        "created_raw": repo["created_at"],
        "pushed_raw": repo["pushed_at"],
        "open_issues": repo["open_issues_count"],
        "topics": ",".join(repo.get("topics") or []),
        "license": (repo.get("license") or {}).get("spdx_id"),
        "owner_type": repo["owner"]["type"],
        "is_fork": int(repo["fork"]),
        "is_archived": int(repo["archived"]),
    })

df = pd.DataFrame(repos)
df = df.dropna(subset=["description"]).drop_duplicates(subset="url").copy()

if df.empty:
    print("All repos lacked description. Exit.")
    sys.exit(0)

df["viral_level"] = df["stars"].apply(categorize)

now_utc = pd.Timestamp.now(tz="UTC")
age_h = (now_utc - pd.to_datetime(df["created_raw"], utc=True)).dt.total_seconds() / 3600
df["stars_per_hour"] = (df["stars"] / age_h.clip(lower=1)).round(2)
df["age_hours"] = age_h.round().astype(int)
df["age_days"] = (age_h / 24).round(1)
df["fork_ratio"] = (df["forks"] / df["stars"].clip(lower=1)).round(3)

df["created_at"], df["created_time"] = split_dt(df["created_raw"])
df["pushed_at"], df["pushed_time"] = split_dt(df["pushed_raw"])
loaded = pd.Series([now_utc.isoformat()] * len(df), index=df.index)
df["loaded_at"], df["loaded_time"] = split_dt(loaded)

df = df.sort_values("stars", ascending=False).reset_index(drop=True)
df["rank"] = df.index + 1

SCHEMA = {
    "rank": "INTEGER",
    "name": "TEXT",
    "owner": "TEXT",
    "owner_type": "TEXT",
    "stars": "INTEGER",
    "forks": "INTEGER",
    "open_issues": "INTEGER",
    "viral_level": "TEXT",
    "language": "TEXT",
    "topics": "TEXT",
    "license": "TEXT",
    "description": "TEXT",
    "url": "TEXT",
    "is_fork": "INTEGER",
    "is_archived": "INTEGER",
    "created_at": "TEXT",
    "created_time": "TEXT",
    "pushed_at": "TEXT",
    "pushed_time": "TEXT",
    "age_hours": "INTEGER",
    "age_days": "REAL",
    "stars_per_hour": "REAL",
    "fork_ratio": "REAL",
    "loaded_at": "TEXT",
    "loaded_time": "TEXT",
}
COLS = list(SCHEMA)
df = df[COLS]

# ---------- Load ----------
conn = sqlite3.connect(DB_PATH)
try:
    cur = conn.cursor()

    col_defs = ", ".join(f'"{c}" {t}' for c, t in SCHEMA.items())
    cur.execute(f"CREATE TABLE IF NOT EXISTS repos ({col_defs})")

    # migrate old DB: add missing cols
    existing = {row[1] for row in cur.execute("PRAGMA table_info(repos)")}
    for col, typ in SCHEMA.items():
        if col not in existing:
            cur.execute(f'ALTER TABLE repos ADD COLUMN "{col}" {typ}')

    df.to_sql("repos_temp", conn, if_exists="replace", index=False)

    col_list = ", ".join(f'"{c}"' for c in COLS)
    cur.execute("DELETE FROM repos WHERE url IN (SELECT url FROM repos_temp)")
    cur.execute(f"INSERT INTO repos ({col_list}) SELECT {col_list} FROM repos_temp")
    cur.execute("DROP TABLE repos_temp")
    conn.commit()
finally:
    conn.close()

print(f"Pipeline complete. {len(df)} rows loaded. Duplicates handled.")

#Made by DishantBhere Hope u liked it :)