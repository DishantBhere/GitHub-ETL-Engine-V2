# -*- coding: utf-8 -*-
"""Dashboard template tool. The design now lives in ONE master file:

    assets/dashboard_template.xlsx      (your hand-edited dashboard, never touched by daily runs)

  python build_template.py                 -> writes GitHub_Trending_Dashboard.xlsx from the master
  python build_template.py my_edited.xlsx  -> makes my_edited.xlsx the NEW master design, then rebuilds

Change the look: edit the xlsx in Excel, then run the 2nd command with that file.
Daily automation (update_excel.py) always starts from the master, so the style never reverts.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.environ.get("DESIGN_SRC", os.path.join(HERE, "assets", "dashboard_template.xlsx"))
OUT = os.environ.get("TEMPLATE_PATH", "GitHub_Trending_Dashboard.xlsx")


def main():
    if len(sys.argv) > 1:
        new = sys.argv[1]
        if not os.path.exists(new):
            raise SystemExit(f"File not found: {new}")
        os.makedirs(os.path.dirname(MASTER), exist_ok=True)
        if os.path.abspath(new) != os.path.abspath(MASTER):
            shutil.copyfile(new, MASTER)
        print(f"New master design saved: {MASTER}")
    if not os.path.exists(MASTER):
        raise SystemExit(f"Master design missing: {MASTER}")
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    shutil.copyfile(MASTER, OUT)
    print(f"Template saved: {OUT}  (run update_excel.py or run_all.py to fill data)")


if __name__ == "__main__":
    main()