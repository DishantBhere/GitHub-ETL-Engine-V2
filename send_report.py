"""Build and send the daily HTML report email.

Env: MAIL_USER, MAIL_PASS, MAIL_TO, STATUS, TRIGGER, REPO, RUN_URL
Optional: DRY_RUN=1 -> writes email_preview.html, sends nothing.
"""
import os
import smtplib
import sqlite3
import ssl
from datetime import datetime
from email.message import EmailMessage
from html import escape
from zoneinfo import ZoneInfo

DB = "github_repos.db"
XLSX = "GitHub_Trending_Dashboard.xlsx"

BG = "#0d1117"
CARD = "#161b22"
LINE = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
GREEN = "#3fb950"
BLUE = "#58a6ff"
ORANGE = "#f0883e"
RED = "#f85149"


def fmt(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def load_stats():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    q = lambda sql: con.execute(sql).fetchall()

    kpi = q(
        "SELECT COUNT(*) n, SUM(stars) s, AVG(stars_per_hour) sph, "
        "MAX(loaded_at) la, MAX(loaded_time) lt FROM repos"
    )[0]
    top = q(
        "SELECT name, owner, stars, language, viral_level, url "
        "FROM repos ORDER BY stars DESC LIMIT 5"
    )
    fast = q(
        "SELECT name, owner, stars_per_hour, stars, url "
        "FROM repos ORDER BY stars_per_hour DESC LIMIT 5"
    )
    langs = q(
        "SELECT language, COUNT(*) c, SUM(stars) s FROM repos "
        "WHERE language IS NOT NULL AND language != '' "
        "GROUP BY language ORDER BY c DESC LIMIT 5"
    )
    fresh = q("SELECT COUNT(*) c FROM repos WHERE age_days <= 7")[0]["c"]
    active = q("SELECT COUNT(*) c FROM repos WHERE pushed_at = loaded_at")[0]["c"]
    con.close()
    return kpi, top, fast, langs, fresh, active


def kpi_cell(label, value, color):
    return (
        f'<td width="25%" style="padding:4px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:{CARD};'
        f'border:1px solid {LINE};border-radius:8px;"><tr><td align="center" style="padding:14px 6px;">'
        f'<div style="font-size:22px;font-weight:700;color:{color};">{escape(str(value))}</div>'
        f'<div style="font-size:11px;color:{MUTED};padding-top:4px;text-transform:uppercase;'
        f'letter-spacing:.5px;">{escape(label)}</div></td></tr></table></td>'
    )


def section(title):
    return (
        f'<tr><td style="padding:22px 24px 8px;font-size:13px;font-weight:700;color:{TEXT};'
        f'text-transform:uppercase;letter-spacing:1px;">{escape(title)}</td></tr>'
    )


def build_html(status, trigger, repo, run_url, now):
    ok = status == "success"
    badge_color = GREEN if ok else RED
    badge_text = "SUCCESS" if ok else "FAILED"

    kpis = top = fast = langs = None
    fresh = active = 0
    try:
        kpis, top, fast, langs, fresh, active = load_stats()
    except Exception as exc:  # DB missing or broken: still send a status mail
        print("stats unavailable:", exc)

    rows = []
    rows.append(
        f'<tr><td style="padding:28px 24px 8px;">'
        f'<div style="font-size:12px;color:{MUTED};letter-spacing:2px;">GITHUB TRENDING INTELLIGENCE</div>'
        f'<div style="font-size:26px;font-weight:700;color:{TEXT};padding-top:6px;">Daily ETL Report</div>'
        f'<div style="padding-top:12px;"><span style="background:{badge_color};color:#0d1117;'
        f'font-size:11px;font-weight:700;padding:4px 10px;border-radius:12px;">{badge_text}</span>'
        f'<span style="color:{MUTED};font-size:12px;padding-left:10px;">{escape(now)} IST'
        f' &nbsp;|&nbsp; trigger: {escape(trigger)}</span></div></td></tr>'
    )

    if not ok:
        rows.append(
            f'<tr><td style="padding:16px 24px;"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:#2d1214;border:1px solid {RED};border-radius:8px;"><tr>'
            f'<td style="padding:14px 16px;color:{TEXT};font-size:14px;">'
            f'The pipeline run failed. Open the run log to see which step broke. '
            f'The dashboard was not refreshed.</td></tr></table></td></tr>'
        )

    if kpis and kpis["n"]:
        topname = top[0]["name"] if top else "n/a"
        rows.append(
            '<tr><td style="padding:12px 20px 0;"><table width="100%" cellpadding="0" cellspacing="0"><tr>'
            + kpi_cell("Repos tracked", kpis["n"], BLUE)
            + kpi_cell("Total stars", fmt(kpis["s"]), GREEN)
            + kpi_cell("Avg stars / hr", f'{kpis["sph"] or 0:.1f}', ORANGE)
            + kpi_cell("Top repo", topname[:14], TEXT)
            + "</tr></table></td></tr>"
        )
        rows.append(
            f'<tr><td style="padding:10px 24px 0;font-size:13px;color:{MUTED};">'
            f'{fresh} repos are under 7 days old. {active} were pushed to today.</td></tr>'
        )

        rows.append(section("Top 5 by stars"))
        body = ""
        for i, r in enumerate(top, 1):
            body += (
                f'<tr><td width="28" style="padding:9px 0 9px 14px;color:{MUTED};font-size:13px;">{i}</td>'
                f'<td style="padding:9px 6px;"><a href="{escape(r["url"])}" style="color:{BLUE};'
                f'text-decoration:none;font-weight:600;font-size:14px;">{escape(r["name"])}</a>'
                f'<div style="color:{MUTED};font-size:11px;">{escape(r["owner"])}'
                f' &middot; {escape(r["language"] or "n/a")}</div></td>'
                f'<td align="right" style="padding:9px 14px;color:{GREEN};font-weight:700;'
                f'font-size:14px;">{fmt(r["stars"])}</td></tr>'
                f'<tr><td colspan="3" style="border-top:1px solid {LINE};font-size:0;line-height:0;">&nbsp;</td></tr>'
            )
        rows.append(
            f'<tr><td style="padding:0 24px;"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{CARD};border:1px solid {LINE};border-radius:8px;">{body}</table></td></tr>'
        )

        rows.append(section("Fastest rising (stars per hour)"))
        body = ""
        for i, r in enumerate(fast, 1):
            body += (
                f'<tr><td width="28" style="padding:9px 0 9px 14px;color:{MUTED};font-size:13px;">{i}</td>'
                f'<td style="padding:9px 6px;"><a href="{escape(r["url"])}" style="color:{BLUE};'
                f'text-decoration:none;font-weight:600;font-size:14px;">{escape(r["name"])}</a>'
                f'<div style="color:{MUTED};font-size:11px;">{fmt(r["stars"])} stars total</div></td>'
                f'<td align="right" style="padding:9px 14px;color:{ORANGE};font-weight:700;'
                f'font-size:14px;">{r["stars_per_hour"]:.1f}/hr</td></tr>'
                f'<tr><td colspan="3" style="border-top:1px solid {LINE};font-size:0;line-height:0;">&nbsp;</td></tr>'
            )
        rows.append(
            f'<tr><td style="padding:0 24px;"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{CARD};border:1px solid {LINE};border-radius:8px;">{body}</table></td></tr>'
        )

        rows.append(section("Language mix"))
        peak = max((r["c"] for r in langs), default=1)
        body = ""
        for r in langs:
            pct = max(int(r["c"] / peak * 100), 4)
            body += (
                f'<tr><td width="95" style="padding:7px 0 7px 14px;color:{TEXT};font-size:13px;">'
                f'{escape(r["language"])}</td>'
                f'<td style="padding:7px 6px;"><table width="100%" cellpadding="0" cellspacing="0">'
                f'<tr><td width="{pct}%" style="background:{BLUE};height:10px;border-radius:5px;'
                f'font-size:0;line-height:0;">&nbsp;</td><td style="font-size:0;">&nbsp;</td></tr></table></td>'
                f'<td width="60" align="right" style="padding:7px 14px;color:{MUTED};font-size:12px;">'
                f'{r["c"]} repos</td></tr>'
            )
        rows.append(
            f'<tr><td style="padding:0 24px;"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{CARD};border:1px solid {LINE};border-radius:8px;">{body}</table></td></tr>'
        )

    rows.append(
        f'<tr><td align="center" style="padding:26px 24px 8px;">'
        f'<a href="{escape(run_url)}" style="background:{BLUE};color:#0d1117;text-decoration:none;'
        f'font-weight:700;font-size:14px;padding:12px 22px;border-radius:6px;display:inline-block;">'
        f'View run log</a>&nbsp;&nbsp;'
        f'<a href="https://github.com/{escape(repo)}" style="border:1px solid {LINE};color:{TEXT};'
        f'text-decoration:none;font-size:14px;padding:11px 20px;border-radius:6px;display:inline-block;">'
        f'Open repo</a></td></tr>'
    )
    if ok:
        rows.append(
            f'<tr><td align="center" style="padding:6px 24px 0;font-size:12px;color:{MUTED};">'
            f'Full Excel dashboard is attached.</td></tr>'
        )
    rows.append(
        f'<tr><td align="center" style="padding:22px 24px 28px;font-size:11px;color:{MUTED};">'
        f'Sent automatically by GitHub Actions &middot; {escape(repo)}</td></tr>'
    )

    return (
        f'<!DOCTYPE html><html><body style="margin:0;padding:0;background:#010409;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#010409;"><tr>'
        f'<td align="center" style="padding:20px 8px;">'
        f'<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;'
        f'background:{BG};border:1px solid {LINE};border-radius:12px;'
        f'font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;">'
        + "".join(rows)
        + "</table></td></tr></table></body></html>"
    )


def main():
    e = os.environ
    status = e.get("STATUS", "unknown")
    trigger = e.get("TRIGGER", "manual")
    repo = e.get("REPO", "")
    run_url = e.get("RUN_URL", "")
    now = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p")

    html = build_html(status, trigger, repo, run_url, now)

    if e.get("DRY_RUN") == "1":
        with open("email_preview.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("wrote email_preview.html")
        return

    user = e["MAIL_USER"].strip()
    pw = e["MAIL_PASS"].replace(" ", "").strip()
    to = e["MAIL_TO"].strip()
    print("user len:", len(user), "pass len:", len(pw), "to len:", len(to))

    m = EmailMessage()
    m["Subject"] = f"ETL {status.upper()} | GitHub Trending report | {now}"
    m["From"] = f"GitHub ETL Bot <{user}>"
    m["To"] = to
    m.set_content(
        f"ETL {status}\nTrigger: {trigger}\nRun: {run_url}\nRepo: https://github.com/{repo}\n"
    )
    m.add_alternative(html, subtype="html")

    if status == "success" and os.path.exists(XLSX):
        with open(XLSX, "rb") as f:
            m.add_attachment(
                f.read(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=XLSX,
            )

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.send_message(m)
    print("email sent")


if __name__ == "__main__":
    main()
