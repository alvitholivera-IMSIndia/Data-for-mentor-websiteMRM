"""
Product configuration (PG / UG / Law).

Every product has its own Google Sheet whose FIRST tab "Enrolment File" holds one row per student.
The dashboard numbers are calculated from that tab (see enrol.py).

Things you can change WITHOUT touching code (Vercel -> Settings -> Environment Variables):
    UG_COHORTS   = BBA27,BBA28         which two cohorts are "This year" and "Next year"
    UG_COHORT_FROM = final             which column names the course group: final (Final Course Name) or combo (Year Product Combo)
    UG_ENROL_TAB = Enrolment File      the tab with the student rows
    UG_SOURCE    = pivot               go back to the old pivot tabs for that product
"""
import os
import re

PRODUCTS = {
    "PG": {
        "label": "PG", "enabled": True,
        "sheet_env": ("GSHEET_ID_PG", "GSHEET_ID"),          # GSHEET_ID = the variable you already set
        "enrol_tab": "Enrolment File", "cohorts": ["CAT26", "CAT27"], "cohort_from": "combo",
        "myplan_tab": "MyPlan Report", "sim_tab": "SimTest Report",     # only used if PG_SOURCE=pivot
    },
    "UG": {
        "label": "UG", "enabled": True,
        "sheet_env": ("GSHEET_ID_UG",),
        "enrol_tab": "Enrolment File", "cohort_from": "final",     # IPM BBA and IPM Indore share Program "BBA"
        # first two = This year / Next year; the rest are extra buttons (shown once they have students)
        "cohorts": ["BBA27", "BBA28", "IPMINDORE27", "IPMINDORE28"],
        "myplan_tab": "MyPlan Report", "sim_tab": "SimTest Report",
    },
    "LAW": {
        "label": "Law", "enabled": True,
        "sheet_env": ("GSHEET_ID_LAW",),
        "enrol_tab": "Enrolment File", "cohort_from": "combo",
        # first two = "This year" / "Next year"; the rest are extra buttons, shown once they have students
        "cohorts": ["LAW27", "LAW28", "YRLLB27", "YRLLB28"],
        "myplan_tab": "MyPlan Report", "sim_tab": "SimTest Report",
    },
}
YEAR_KEYS = ("this", "next")
# Button names: code prefix -> label.   YRLLB27 -> "3YR LLB 27", IPMINDORE27 -> "IPM Indore 27"
ALIASES = {"YRLLB": "3YR LLB", "IPMINDORE": "IPM Indore"}
# Final Course Name prefixes folded into a shorter code (IPMBBA27 is simply "BBA 27")
COURSE_ALIASES = {"IPMBBA": "BBA"}
YEAR_LABELS = {"this": "This year", "next": "Next year"}


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


def source(code):
    """'enrolment' (default: calculate from the Enrolment File) or 'pivot' (old pivot tabs)."""
    return "pivot" if (os.environ.get(f"{code}_SOURCE") or "").strip().lower() == "pivot" else "enrolment"


def enrol_tab(code):
    return (os.environ.get(f"{code}_ENROL_TAB") or PRODUCTS[code]["enrol_tab"]).strip()


def cohorts(code):
    """[this-year cohort, next-year cohort], e.g. ['CAT26', 'CAT27']."""
    env = os.environ.get(f"{code}_COHORTS")
    if env:
        vals = [x.strip().upper() for x in env.split(",") if x.strip()]
        if vals:
            return vals
    return list(PRODUCTS[code]["cohorts"])


def cohort_from(code):
    """Which column names the course group: 'final' (Final Course Name) or 'combo' (Year Product Combo)."""
    env = (os.environ.get(f"{code}_COHORT_FROM") or "").strip().lower()
    return env if env in ("final", "combo") else PRODUCTS[code].get("cohort_from", "combo")


def cohort_for(code, year_key, cl=None):
    """year_key = 'this' | 'next' | an extra cohort code. cl = the full ordered cohort list (defaults to config)."""
    cs = cl or cohorts(code)
    key = (year_key or "this").upper()
    if key in cs:
        return key
    return cs[1] if year_key == "next" and len(cs) > 1 else cs[0]


def year_key_of(cl, cohort):
    """The URL key for a cohort: this / next for the first two, the cohort code for the rest."""
    return "this" if cohort == cl[0] else ("next" if len(cl) > 1 and cohort == cl[1] else cohort)


def pretty(cohort):
    """CAT26 -> 'CAT 26'; YRLLB27 -> '3YR LLB 27'; IPMINDORE27 -> 'IPM Indore 27'."""
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", cohort or "")
    if not m:
        return cohort or ""
    return f"{ALIASES.get(m.group(1).upper(), m.group(1))} {m.group(2)}"


def heading(cohort):
    return f"Metric ({pretty(cohort)} students till date)"


def tab(code, kind):
    """Pivot tabs (only used when <CODE>_SOURCE=pivot). kind = 'myplan' or 'sim'."""
    p = PRODUCTS[code]
    env = os.environ.get(f"{code}_{kind.upper()}_TAB")
    if not env and code == "PG":
        env = os.environ.get("MYPLAN_SHEET" if kind == "myplan" else "SIM_SHEET")
    return (env or p[f"{kind}_tab"]).strip()
