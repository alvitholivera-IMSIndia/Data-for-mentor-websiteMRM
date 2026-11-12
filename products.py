"""
Product configuration (PG / UG / Law).

To change a tab name WITHOUT touching code, set an environment variable in Vercel, e.g.
    UG_SIM_TAB = SimTest Report        UG_MYPLAN_TAB = Checking
Law needs GSHEET_ID_LAW in Vercel (and the sheet shared with the service account).
"""
import os
import re

PRODUCTS = {
    "PG": {
        "label": "PG", "enabled": True,
        "sheet_env": ("GSHEET_ID_PG", "GSHEET_ID"),          # GSHEET_ID = the variable you already set
        "myplan_tab": "MyPlan Report", "sim_tab": "CheckingSim",
        "heading": "Metric (CAT 26 students till date)",
    },
    "UG": {
        "label": "UG", "enabled": True,
        "sheet_env": ("GSHEET_ID_UG",),
        "myplan_tab": "MyPlan Report", "sim_tab": "SimTest Report",
        "heading": "Metric (UG students till date)",
    },
    "LAW": {
        "label": "Law", "enabled": True,
        "sheet_env": ("GSHEET_ID_LAW",),
        "myplan_tab": "MyPlan Report", "sim_tab": "SimTest Report",
        "heading": "Metric (Law students till date)",
    },
}


def enabled_codes():
    return [c for c, p in PRODUCTS.items() if p["enabled"]]


def sheet_id(code):
    """Sheet ID from the env var. Accepts the bare ID or a pasted full Google Sheet link."""
    for name in PRODUCTS[code]["sheet_env"]:
        v = (os.environ.get(name) or "").strip()
        if v:
            m = re.search(r"/d/([A-Za-z0-9_-]+)", v)
            return m.group(1) if m else v
    return ""


def env_hint(code):
    return PRODUCTS[code]["sheet_env"][0]


def tab(code, kind):
    """kind = 'myplan' or 'sim'."""
    p = PRODUCTS[code]
    env = os.environ.get(f"{code}_{kind.upper()}_TAB")
    if not env and code == "PG":      # keep the old variable names working for PG
        env = os.environ.get("MYPLAN_SHEET" if kind == "myplan" else "SIM_SHEET")
    return (env or p[f"{kind}_tab"]).strip()
