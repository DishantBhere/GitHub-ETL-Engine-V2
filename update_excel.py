# -*- coding: utf-8 -*-
"""Daily Excel updater: SQLite -> CSVs + data sheets inside the dashboard.

DESIGN-SAFE: every run copies the master design assets/dashboard_template.xlsx and patches it at zip level. Only the cell data of the *_Data
sheets, their table ranges and Config are rewritten. Dashboard sheet, charts,
images, picture links, fonts, colors, merged cells... are copied byte for byte.
(openpyxl load/save would drop picture hyperlinks and re-write the charts, so it
is NOT used on the workbook any more.)

Excel recalculates every KPI / chart when the file is opened (fullCalcOnLoad).
"""

import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

import pandas as pd

DB_PATH = os.environ.get("DB_PATH", "github_repos.db")
TEMPLATE_PATH = os.environ.get("TEMPLATE_PATH", "GitHub_Trending_Dashboard.xlsx")  # output workbook
# Master design (your hand-edited dashboard). Never modified by runs. Every run starts from a
# fresh copy of it, so the design can never drift back to an old style.
DESIGN_SRC = os.environ.get("DESIGN_SRC", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "dashboard_template.xlsx"))
DATA_DIR = os.environ.get("DATA_DIR", "data")
DAYS_BACK = int(os.environ.get("DAYS_BACK", 30))  # same default as pipeline.py
# DB keeps old rows. 1 = dashboard shows only the newest load (default), 0 = every row in DB
LATEST_ONLY = os.environ.get("LATEST_ONLY", "1") == "1"

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
TIER_ORDER = ["Trending", "Rising Star", "Viral", "Super Viral",
              "Iconic", "Legendary", "Hall of Fame"]
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("excel_updater")


# --------------------------------------------------------------------------- data
def read_repos():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM repos", conn)
    finally:
        conn.close()
    missing = [c for c in REPO_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"DB missing columns {missing}. Run pipeline.py first.")
    if df.empty:
        raise SystemExit("repos table is empty. Run pipeline.py first.")

    if LATEST_ONLY:
        d = pd.to_datetime(df["loaded_at"], format="%d-%m-%y", errors="coerce")
        df = df[d == d.max()]
    df = df.sort_values(["stars", "name"], ascending=[False, True]).reset_index(drop=True)
    df["rank"] = df.index + 1  # clean 1..N (old rows no longer collide)
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


# --------------------------------------------------------------------------- xml helpers
_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def cell_xml(ref, v, style):
    """One <c>. Numbers stay numbers, text becomes an inline string, None/NaN = empty."""
    if v is None or (isinstance(v, float) and (v != v or v in (float("inf"), float("-inf")))):
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    if hasattr(v, "item"):          # numpy scalar -> python
        v = v.item()
    s_attr = f' s="{style}"' if style else ""
    if isinstance(v, bool):
        return f'<c r="{ref}"{s_attr}><v>{int(v)}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"{s_attr}><v>{repr(v)}</v></c>'
    txt = escape(_BAD.sub("", str(v)))
    return f'<c r="{ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c>'


def sheet_part_map(files):
    """sheet name -> part path (xl/worksheets/sheetN.xml)"""
    wb = files["xl/workbook.xml"].decode("utf8")
    rels = files["xl/_rels/workbook.xml.rels"].decode("utf8")
    rid2t = {}
    for tag in re.findall(r"<Relationship\b[^>]*>", rels):
        rid = re.search(r'Id="([^"]+)"', tag).group(1)
        tgt = re.search(r'Target="([^"]+)"', tag).group(1)
        rid2t[rid] = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
    out = {}
    for tag in re.findall(r"<sheet\b[^>]*/>", wb):
        name = re.search(r'name="([^"]+)"', tag).group(1)
        rid = re.search(r'r:id="([^"]+)"', tag).group(1)
        out[name] = rid2t[rid]
    return out


def table_part(files, sheet_part):
    rel = os.path.dirname(sheet_part) + "/_rels/" + os.path.basename(sheet_part) + ".rels"
    if rel not in files:
        return None
    for tag in re.findall(r"<Relationship\b[^>]*>", files[rel].decode("utf8")):
        if "/table" in tag:
            t = re.search(r'Target="([^"]+)"', tag).group(1)
            if t.startswith("/"):
                return t.lstrip("/")
            return os.path.normpath(os.path.join(os.path.dirname(sheet_part), t)).replace("\\", "/")
    return None


def column_styles(sheet_xml, ncols):
    """style id (s=) per column, copied from the first data rows of the template."""
    styles = {}
    for m in re.finditer(r'<row r="(\d+)"[^>]*>(.*?)</row>', sheet_xml, re.S):
        if int(m.group(1)) < 2:
            continue
        for c in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*?)/?>', m.group(2)):
            s = re.search(r'\bs="(\d+)"', c.group(2))
            if s and c.group(1) not in styles:
                styles[c.group(1)] = s.group(1)
        if int(m.group(1)) > 8:
            break
    return styles


def patch_data_sheet(files, part, cols, df):
    xml = files[part].decode("utf8")
    styles = column_styles(xml, len(cols))
    hdr = re.search(r'<row r="1"[^>]*>.*?</row>', xml, re.S)
    if not hdr:
        raise SystemExit(f"{part}: header row missing in template")
    rows = [hdr.group(0)]
    for r, row in enumerate(df.itertuples(index=False, name=None), 2):
        cells = "".join(cell_xml(f"{col_letter(c)}{r}", v, styles.get(col_letter(c)))
                        for c, v in enumerate(row, 1))
        rows.append(f'<row r="{r}">{cells}</row>')
    last = max(len(df) + 1, 2)
    xml = re.sub(r"<sheetData>.*?</sheetData>|<sheetData\s*/>",
                 lambda _m: "<sheetData>" + "".join(rows) + "</sheetData>", xml, count=1, flags=re.S)
    xml = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:{col_letter(len(cols))}{last}"/>', xml, count=1)
    files[part] = xml.encode("utf8")
    return last


def patch_table(files, tpart, ncols, last, cols):
    t = files[tpart].decode("utf8")
    names = re.findall(r'<tableColumn\b[^>]*\bname="([^"]*)"', t)
    if names != cols:
        raise SystemExit(f"{tpart}: table columns {names} != expected {cols}. "
                         "Template and update_excel.py are out of sync.")
    ref = f"A1:{col_letter(ncols)}{last}"
    t = re.sub(r'(<table\b[^>]*?\bref=")[^"]*(")', lambda m: m.group(1) + ref + m.group(2), t, count=1)
    t = re.sub(r'(<autoFilter\b[^>]*?\bref=")[^"]*(")', lambda m: m.group(1) + ref + m.group(2), t, count=1)
    files[tpart] = t.encode("utf8")


def patch_config(files, part, values):
    xml = files[part].decode("utf8")
    for ref, v in values.items():
        new = cell_xml(ref, v, None)
        pat = re.compile(r'<c r="%s"[^>]*?(?:/>|>.*?</c>)' % ref, re.S)
        if pat.search(xml):
            xml = pat.sub(lambda _m: new, xml, count=1)
        else:
            log.warning("Config cell %s not found, skipped", ref)
    files[part] = xml.encode("utf8")


def patch_workbook(files, repos_only_data=True):
    wb = files["xl/workbook.xml"].decode("utf8")
    if "fullCalcOnLoad" not in wb:
        if re.search(r"<calcPr\b[^>]*/>", wb):
            wb = re.sub(r"<calcPr\b([^>]*?)/>", r'<calcPr\1 fullCalcOnLoad="1"/>', wb, count=1)
        else:
            wb = wb.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')
    files["xl/workbook.xml"] = wb.encode("utf8")


def update_workbook(path, frames, config, src=None):
    """Read `src` (master design, default = path itself), patch data, write result to `path`."""
    with zipfile.ZipFile(src or path) as z:
        infos = z.infolist()
        files = {i.filename: z.read(i.filename) for i in infos}
    parts = sheet_part_map(files)
    for sheet, df in frames.items():
        tname, cols, _ = SHEETS[sheet]
        part = parts[sheet]
        last = patch_data_sheet(files, part, cols, df)
        tp = table_part(files, part)
        if tp:
            patch_table(files, tp, len(cols), last, cols)
    patch_config(files, parts["Config"], config)
    patch_workbook(files)

    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for i in infos:                       # same order, same names
            out.writestr(i.filename, files[i.filename])
    try:
        shutil.move(tmp, path)
    except PermissionError:
        os.remove(tmp)
        raise SystemExit(f"Cannot write {path}. Close it in Excel, then run again.")


def main():
    repos = read_repos()
    frames = build_frames(repos)

    os.makedirs(DATA_DIR, exist_ok=True)
    for sheet, df in frames.items():
        df.to_csv(os.path.join(DATA_DIR, SHEETS[sheet][2]), index=False)
    log.info("CSVs written to %s/", DATA_DIR)

    src = DESIGN_SRC if os.path.exists(DESIGN_SRC) else TEMPLATE_PATH
    if not os.path.exists(src):
        raise SystemExit(f"Design template not found: {DESIGN_SRC} (or {TEMPLATE_PATH}).")
    config = {"B1": f"{repos['loaded_at'].iloc[0]} {repos['loaded_time'].iloc[0]}",
              "B2": DAYS_BACK, "B3": len(repos)}
    update_workbook(TEMPLATE_PATH, frames, config, src=src)
    log.info("Excel built from %s -> %s (%d repos). Design copied as is, only data changed.",
             os.path.relpath(src), TEMPLATE_PATH, len(repos))


if __name__ == "__main__":
    sys.exit(main())