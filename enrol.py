"""
Calculates every dashboard number from the student-level "Enrolment File" tab.

One row per student  ->  totals per (cohort, centre).  Nothing here needs the pivot tabs.

Rules (all in this file so they are easy to change):
  * a row is IGNORED when its centre is "-" / blank, or "Eligible for myPlans" / "COCO / Non COCO" says Ignore
  * cohort (exam year) = "Year Product Combo" (e.g. CAT26); if that cell is broken (e.g. "BBAD)") it is
    rebuilt as Program + the 4-digit year found in the Variant name
  * below benchmark = student total  <  student benchmark  (strictly less)
  * Enrolments = number of student rows
  * per-student targets (MPE / TPE / coverage) = average of the students' benchmark
"""
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone

import products as P
from models import CenterData, Meta, RegionMap, db

MONTHS = ["apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "jan", "feb", "mar"]


def _k(h):
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cohort_of(program, variant, combo, final=None, source="combo"):
    """Course group + exam year, e.g. CAT26, BBA27, IPMINDORE27, YRLLB27.
    source='final' reads "Final Course Name" first (separates IPM BBA from IPM Indore);
    otherwise "Year Product Combo". A broken cell falls back to Program + the year in the Variant name."""
    def clean(v):
        v = re.sub(r"\s+", "", str(v or "")).upper()
        return v if re.fullmatch(r"[A-Z]+\d{2}", v) else None

    code = next((c for c in ((clean(final),) if source == "final" else ()) + (clean(combo),) if c), None)
    if not code:
        year = re.search(r"(20\d{2})", str(variant or ""))
        prog = re.sub(r"[^A-Z]", "", str(program or "").upper())
        code = f"{prog}{year.group(1)[2:]}" if year and prog else None
    if code:
        m = re.fullmatch(r"([A-Z]+)(\d{2})", code)
        code = P.COURSE_ALIASES.get(m.group(1), m.group(1)) + m.group(2)
    return code


# column name -> test on the normalised header (spaces/symbols removed, lower case)
COLUMN_TESTS = {
    "imspin": lambda k: k == "imspin",
    "program": lambda k: k.startswith("program"),
    "variant": lambda k: k.startswith("variant"),
    "combo": lambda k: k == "yearproductcombo",
    "final": lambda k: k == "finalcoursename",
    "centre": lambda k: k == "reportcitycentrefinal",
    "city": lambda k: k == "city",
    "coco": lambda k: k.startswith("coco"),
    "eligible": lambda k: k == "eligibleformyplans",
    "mp_total": lambda k: k == "totalmyplans",
    "mp_one": lambda k: k == "atleastonemyplan",
    "sim_total": lambda k: k.startswith("totalsim"),            # TotalSimCATs / TotalSimTests
    "sim_one": lambda k: k.startswith("atleast1sim"),           # AtLeast1SimCAT / AtLeast1SimTest
    "mp_bench": lambda k: k == "myplanbenchmark",
    "sim_bench": lambda k: k == "simtestbenchmark",
    "mp_cov": lambda k: k == "myplancoveragebenchmark",
    "sim_cov": lambda k: k == "simtestcoveragebenchmark",
    "mp_flag": lambda k: k == "belowmyplanbenchmark",
    "sim_flag": lambda k: k.startswith("belowsim") and k.endswith("benchmark"),
    "bt": lambda k: k == "bt",
    "mt": lambda k: k == "mt",
    "sim_month": lambda k: bool(re.fullmatch(r"(apr|may|jun|jul|aug|sep|oct|nov|dec|jan|feb|mar)[a-z]*sim[a-z]*", k)),
}
REQUIRED = {"centre": "Report City Centre Final", "mp_total": "Totalmyplans", "mp_one": "AtLeastOneMyPlan",
            "sim_total": "TotalSimCATs / TotalSimTests", "sim_one": "AtLeast1SimCAT / AtLeast1SimTest",
            "mp_bench": "myPlanBenchmark", "sim_bench": "SimTestBenchmark"}


def locate(heads):
    keys = [_k(h) for h in heads]
    col = {}
    for name, test in COLUMN_TESTS.items():
        col[name] = next((i for i, k in enumerate(keys) if test(k)), None)
    return col


def _month_column(heads, prefix):
    return next((i for i, h in enumerate(heads) if _k(h)[:3] == prefix and len(_k(h)) <= 4), None)


def compute(rows, product):
    """rows = header row + student rows. Returns (stats, report)."""
    if not rows:
        raise ValueError("The Enrolment File tab is empty")
    heads = rows[0]
    col = locate(heads)
    missing = [REQUIRED[n] for n in REQUIRED if col[n] is None]
    if col["centre"] is None or col["mp_total"] is None or col["sim_total"] is None:
        raise ValueError("Enrolment File: could not find these columns: " + ", ".join(missing) +
                         ". Headers found: " + " | ".join(str(h) for h in heads if h))

    # the latest-month columns (e.g. "Oct SimTests" and the "Oct" myPlan column)
    month = None
    if col["sim_month"] is not None:
        month = _k(heads[col["sim_month"]])[:3]
    mp_month_col = _month_column(heads, month) if month else None
    month_label = month.title() if month else ""

    stats, rep = {}, Counter()
    seen_elig, seen_coco, pairs = Counter(), Counter(), Counter()
    source = P.cohort_from(product)
    flag_bad = {"myplan": [], "sim": []}

    for r in rows[1:]:
        if not any(v not in (None, "") for v in r):
            continue
        rep["rows"] += 1
        g = lambda n: (r[col[n]] if col[n] is not None and col[n] < len(r) else None)
        centre = str(g("centre") or "").strip()
        elig = str(g("eligible") or "").strip()
        coco = str(g("coco") or "").strip()
        seen_elig[elig or "(blank)"] += 1
        seen_coco[coco or "(blank)"] += 1
        if centre in ("", "-") or "ignore" in elig.lower() or "ignore" in coco.lower():
            rep["ignored"] += 1
            continue
        cohort = cohort_of(g("program"), g("variant"), g("combo"), g("final"), source)
        pairs[f"{str(g('combo') or '-').strip()} | {str(g('final') or '-').strip()} → {cohort or '?'}"] += 1
        if not cohort:
            rep["no_cohort"] += 1
            continue

        a = stats.setdefault((cohort, centre), Counter())
        a["enrol"] += 1
        mp_t, sim_t = _num(g("mp_total")) or 0, _num(g("sim_total")) or 0
        mp_b, sim_b = _num(g("mp_bench")), _num(g("sim_bench"))
        a["myplan_total"] += mp_t
        a["myplan_atleast1"] += _num(g("mp_one")) or 0
        a["myplan_target_total"] += mp_b or 0
        a["mp_cov_sum"] += _num(g("mp_cov")) or 0
        a["sim_total"] += sim_t
        a["sim_atleast1"] += _num(g("sim_one")) or 0
        a["sim_target_total"] += sim_b or 0
        a["sim_cov_sum"] += _num(g("sim_cov")) or 0
        a["bt_total"] += _num(g("bt")) or 0
        a["mt_total"] += _num(g("mt")) or 0
        below_mp = int(mp_b is not None and mp_t < mp_b)
        below_sim = int(sim_b is not None and sim_t < sim_b)
        a["mp_below"] += below_mp
        a["sim_below"] += below_sim
        if mp_month_col is not None and mp_month_col < len(r):
            a["oct_myplans"] += _num(r[mp_month_col]) or 0
        if col["sim_month"] is not None:
            a["oct_sim"] += _num(g("sim_month")) or 0
        # cross-check against the flags already in the sheet
        for name, mine in (("mp_flag", below_mp), ("sim_flag", below_sim)):
            f = _num(g(name))
            if f is not None and int(f) != mine:
                key = "myplan" if name == "mp_flag" else "sim"
                rep["flag_mismatch_" + key] += 1
                if len(flag_bad[key]) < 5:
                    flag_bad[key].append(str(g("imspin")))
        a["_coco_" + coco] += 1
        city = str(g("city") or "").strip()
        if city and city != "-":
            a["_city_" + city] += 1

    out = {}
    for (cohort, centre), a in stats.items():
        n = a["enrol"]
        cocos = {k[6:]: v for k, v in a.items() if k.startswith("_coco_")}
        cities = {k[6:]: v for k, v in a.items() if k.startswith("_city_")}
        raw = {
            "enrol": n,
            "myplan_total": a["myplan_total"], "myplan_atleast1": a["myplan_atleast1"],
            "myplan_target_total": a["myplan_target_total"], "mpe_target": a["myplan_target_total"] / n,
            "mp_cov_target": a["mp_cov_sum"] / n / 100, "myplan_meets": n - a["mp_below"],
            "sim_total": a["sim_total"], "sim_atleast1": a["sim_atleast1"],
            "sim_target_total": a["sim_target_total"], "tpe_target": a["sim_target_total"] / n,
            "sim_cov_target": a["sim_cov_sum"] / n / 100, "sim_meets": n - a["sim_below"],
            "bt_total": a["bt_total"], "mt_total": a["mt_total"],
            "oct_myplans": a["oct_myplans"] if mp_month_col is not None else None,
            "oct_sim": a["oct_sim"] if col["sim_month"] is not None else None,
        }
        out[(cohort, centre)] = {
            "raw": raw,
            "coco": max(cocos, key=cocos.get) if cocos else "",
            "city": max(cities, key=cities.get) if cities else "",
        }
    rep_out = {
        "rows": rep["rows"], "ignored": rep["ignored"], "no_cohort": rep["no_cohort"],
        "eligible": dict(seen_elig), "coco": dict(seen_coco), "month": month_label,
        "missing": missing, "flag_mismatch_myplan": rep["flag_mismatch_myplan"],
        "flag_mismatch_sim": rep["flag_mismatch_sim"], "flag_examples": flag_bad,
        "pairs": dict(pairs.most_common(30)), "cohort_from": source,
    }
    return out, rep_out


# ---------------------------------------------------------------- reading the file
def rows_from_gsheet(product):
    import requests
    import ingest
    get, base, titles, _ = ingest._gsheet_client(product)
    real = ingest.match_tab(titles, P.enrol_tab(product))
    data = get(f"{base}/values/{requests.utils.quote(real)}", params={"valueRenderOption": "UNFORMATTED_VALUE"})
    return data.get("values", [])


def rows_from_xlsx(fileobj, product):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(fileobj.read()), data_only=True, read_only=True)
    want = P.enrol_tab(product).lower()
    ws = next((w for w in wb.worksheets if w.title.strip().lower() == want), wb.worksheets[0])
    return [list(r) for r in ws.iter_rows(values_only=True)]


# ---------------------------------------------------------------- saving
def store(product, rows, source):
    stats, rep = compute(rows, product)
    rmap = {m.city: m.region for m in RegionMap.query.all()}
    CenterData.query.filter_by(product=product).delete()
    per = {}
    for (cohort, centre), v in stats.items():
        city = v["city"] or centre.split(" - ")[0]
        db.session.add(CenterData(product=product, cohort=cohort, coco=v["coco"] or "Unknown", city=city,
                                  region=rmap.get(city, city), center=centre, raw=json.dumps(v["raw"])))
        p = per.setdefault(cohort, {"centres": 0, "students": 0})
        p["centres"] += 1
        p["students"] += v["raw"]["enrol"]
    wanted = P.base_cohorts(product)
    rep["cohorts"] = per
    rep["other_cohorts"] = sorted(c for c in per if c not in wanted)
    rep["mode"] = "enrolment"
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    for k, v in ((f"last_updated:{product}", stamp), (f"last_source:{product}", source),
                 (f"sync_detail:{product}", json.dumps(rep)), (f"month_label:{product}", rep["month"])):
        row = db.session.get(Meta, k) or Meta(key=k)
        row.value = v
        db.session.add(row)
    db.session.commit()
    shown = sum(per[c]["centres"] for c in wanted if c in per)
    parts = [f"{c}: {per[c]['centres']} centres / {per[c]['students']} students" for c in wanted if c in per]
    return shown, parts, rep
