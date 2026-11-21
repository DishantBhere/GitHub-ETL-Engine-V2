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
LOGO = "download-Photoroom.png"

PAGE = "#DCC5A8"
IVORY = "#E6D5BD"
SOFT = "#F0E5D3"
RED = "#C8102E"
BLACK = "#0B0B0C"
MUTED = "#6F6256"
BORDER = "#CDBEA6"
DIVIDER = "#DACCB6"
TRACK = "#CDB99E"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


def fmt(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def num(n):
    return f"{int(n or 0):,}"


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


def kpi_cell(label, value, color, size=23):
    return (
        f'<td width="25%" valign="top" style="padding:0 5px;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:{SOFT};'
        f'border:1px solid {BORDER};border-top:4px solid {RED};border-radius:8px;'
        f'border-collapse:separate;height:88px;"><tr><td valign="top" style="padding:15px 12px;">'
        f'<div style="font-size:9px;color:{MUTED};text-transform:uppercase;white-space:nowrap;'
        f'letter-spacing:1px;font-weight:800;">{escape(label)}</div>'
        f'<div style="font-size:{size}px;line-height:30px;font-weight:800;color:{color};'
        f'padding-top:6px;white-space:nowrap;">{escape(str(value))}</div>'
        f'</td></tr></table></td>'
    )


def section(title):
    return (
        f'<tr><td style="padding:30px 30px 11px;"><div style="padding-bottom:8px;'
        f'border-bottom:2px solid {RED};font-size:11px;font-weight:900;color:{BLACK};'
        f'text-transform:uppercase;letter-spacing:1.5px;">{escape(title)}</div></td></tr>'
    )


def list_card(body):
    return (
        f'<tr><td style="padding:0 30px;"><table width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{SOFT};border:1px solid {BORDER};border-radius:8px;'
        f'border-collapse:separate;">{body}</table></td></tr>'
    )


def repo_row(i, last, name, url, meta, value, value_color):
    b = "" if last else f"border-bottom:1px solid {DIVIDER};"
    return (
        f'<tr><td width="36" style="padding:14px 0 14px 16px;color:{RED};font-size:13px;'
        f'font-weight:900;{b}">{i}</td>'
        f'<td style="padding:14px 8px;{b}"><a href="{escape(url)}" style="color:{RED};'
        f'text-decoration:none;font-weight:800;font-size:14px;">{escape(name)}</a>'
        f'<div style="color:{MUTED};font-size:11px;padding-top:3px;">{meta}</div></td>'
        f'<td align="right" style="padding:14px 16px;color:{value_color};font-weight:900;'
        f'font-size:14px;white-space:nowrap;{b}">{value}</td></tr>'
    )


def build_html(status, trigger, repo, run_url, now, logo_src=None):
    ok = status == "success"
    badge_bg = RED if ok else "#4B4D52"
    badge_text = "SUCCESS" if ok else "FAILED"

    kpis = top = fast = langs = None
    fresh = active = 0
    try:
        kpis, top, fast, langs, fresh, active = load_stats()
    except Exception as exc:  # DB missing or broken: still send a status mail
        print("stats unavailable:", exc)

    logo = (
        f'<td width="120" align="right" valign="middle" style="padding-left:20px;">'
        f'<img src="{escape(logo_src)}" width="120" height="120" alt="GitHub Trending Intelligence" '
        f'style="display:block;border:0;width:120px;height:120px;"></td>'
        if logo_src
        else ""
    )
    rows = []
    rows.append(
        f'<tr><td bgcolor="{BLACK}" style="background:{BLACK};padding:30px 36px 28px;'
        f'border-bottom:5px solid {RED};"><table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td valign="top">'
        f'<div style="font-size:11px;color:{RED};letter-spacing:2px;font-weight:800;">GITHUB TRENDING INTELLIGENCE</div>'
        f'<div style="font-size:30px;line-height:36px;font-weight:800;color:{IVORY};padding-top:7px;">Daily ETL Report</div>'
        f'<div style="padding-top:18px;"><span style="background:{badge_bg};color:{IVORY};'
        f'font-size:10px;font-weight:800;letter-spacing:1px;padding:6px 12px;border-radius:20px;">{badge_text}</span>'
        f'<span style="color:#BBAE9E;font-size:12px;padding-left:10px;">{escape(now)} IST</span></div></td>'
        f'{logo}</tr></table></td></tr>'
    )

    if not ok:
        rows.append(
            f'<tr><td style="padding:22px 30px 0;"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:#F6D8CF;border:1px solid #A92A0A;border-left:4px solid #A92A0A;'
            f'border-radius:5px;border-collapse:separate;"><tr>'
            f'<td style="padding:14px 16px;color:{BLACK};font-size:14px;line-height:20px;">'
            f'The pipeline run failed. Open the run log to see which step broke. '
            f'The dashboard was not refreshed.</td></tr></table></td></tr>'
        )

    if kpis and kpis["n"]:
        topname = top[0]["name"] if top else "n/a"
        rows.append(
            '<tr><td style="padding:26px 25px 0;"><table width="100%" cellpadding="0" cellspacing="0"><tr>'
            + kpi_cell("Repos tracked", kpis["n"], BLACK)
            + kpi_cell("Total stars", fmt(kpis["s"]), BLACK)
            + kpi_cell("Avg stars / hr", f'{kpis["sph"] or 0:.1f}', BLACK)
            + kpi_cell("Top repo", topname[:14], RED, 14)
            + "</tr></table></td></tr>"
        )
        rows.append(
            f'<tr><td style="padding:18px 30px 0;"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{BLACK};border-left:4px solid {RED};border-radius:5px;'
            f'border-collapse:separate;"><tr><td style="padding:13px 16px;color:{IVORY};font-size:12px;">'
            f'{fresh} repos are under 7 days old. {active} were pushed today.'
            f'</td></tr></table></td></tr>'
        )

        rows.append(section("Top 5 by stars"))
        body = "".join(
            repo_row(
                i, i == len(top), r["name"], r["url"],
                f'{escape(r["owner"])} &middot; {escape(r["language"] or "n/a")}',
                num(r["stars"]), BLACK,
            )
            for i, r in enumerate(top, 1)
        )
        rows.append(list_card(body))

        rows.append(section("Fastest rising \u00b7 Stars per hour"))
        body = "".join(
            repo_row(
                i, i == len(fast), r["name"], r["url"],
                f'{num(r["stars"])} stars total',
                f'{r["stars_per_hour"]:.1f}/hr', RED,
            )
            for i, r in enumerate(fast, 1)
        )
        rows.append(list_card(body))

        rows.append(section("Language mix"))
        peak = max((r["c"] for r in langs), default=1)
        body = ""
        for r in langs:
            pct = max(int(r["c"] / peak * 100), 4)
            body += (
                f'<tr><td width="95" style="padding:12px 0 12px 16px;color:{BLACK};font-size:12px;'
                f'font-weight:700;">{escape(r["language"])}</td>'
                f'<td style="padding:12px 8px;"><table width="100%" cellpadding="0" cellspacing="0" '
                f'style="background:{TRACK};border-radius:10px;border-collapse:separate;">'
                f'<tr><td width="{pct}%" bgcolor="{RED}" style="background:{RED};height:9px;'
                f'border-radius:10px;font-size:0;line-height:0;">&nbsp;</td>'
                f'<td style="font-size:0;line-height:0;">&nbsp;</td></tr></table></td>'
                f'<td width="64" align="right" style="padding:12px 16px 12px 0;color:{MUTED};'
                f'font-size:11px;font-weight:600;white-space:nowrap;">{r["c"]} repos</td></tr>'
            )
        rows.append(list_card(body))

    rows.append(
        f'<tr><td align="center" style="padding:34px 30px 24px;">'
        f'<a href="{escape(run_url)}" style="background:{RED};border:2px solid {RED};color:{IVORY};'
        f'text-decoration:none;font-weight:800;font-size:13px;padding:12px 24px;border-radius:7px;'
        f'display:inline-block;">View run log</a>&nbsp;&nbsp;'
        f'<a href="https://github.com/{escape(repo)}" style="background:{BLACK};border:2px solid {BLACK};'
        f'color:{IVORY};text-decoration:none;font-weight:800;font-size:13px;padding:12px 24px;'
        f'border-radius:7px;display:inline-block;">Open repo</a></td></tr>'
    )
    attached = "Full Excel dashboard is attached.<br><br>" if ok else ""
    rows.append(
        f'<tr><td align="center" bgcolor="{BLACK}" style="background:{BLACK};padding:25px 30px 30px;'
        f'border-top:4px solid {RED};color:#AFA294;font-size:10px;line-height:15px;">'
        f'{attached}Sent automatically by <strong style="color:{IVORY};">GitHub Actions</strong> &middot; '
        f'<a href="https://github.com/DishantBhere" style="color:{RED};text-decoration:none;'
        f'font-weight:800;">Dishant Bhere</a></td></tr>'
    )

    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<meta name="color-scheme" content="light only"><title>GitHub Trending Intelligence</title></head>'
        f'<body style="margin:0;padding:0;background:{PAGE};">'
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE};"><tr>'
        f'<td align="center" style="padding:32px 12px;">'
        f'<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;'
        f'background:{IVORY};border:2px solid {BLACK};border-radius:14px;border-collapse:separate;'
        f'overflow:hidden;font-family:{FONT};color:{BLACK};">'
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

    has_logo = os.path.exists(LOGO)
    dry = e.get("DRY_RUN") == "1"
    logo_src = (LOGO if dry else "cid:logo") if has_logo else None
    html = build_html(status, trigger, repo, run_url, now, logo_src)

    if dry:
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
    if has_logo:
        with open(LOGO, "rb") as f:
            m.get_payload()[1].add_related(
                f.read(), maintype="image", subtype="png", cid="<logo>"
            )

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