"""
The dashboard FORMAT lives here. Each entry in METRICS is one row of the format file.
To add a new row in future: add one dict to METRICS (and, if it needs a new raw column,
one rule in ingest.py). Nothing else changes.
"""

# raw fields that are simply added up when centres are combined
SUM_FIELDS = [
    "enrol",
    "myplan_target_total", "myplan_total", "myplan_atleast1", "myplan_meets",
    "sim_target_total", "sim_total", "sim_atleast1", "sim_meets",
    "bt_total", "mt_total",
]
# raw fields that are per-student targets -> combined as enrolment-weighted average
WAVG_FIELDS = ["mpe_target", "mp_cov_target", "tpe_target", "sim_cov_target"]

DASH = "–"

# Regions kept as separate centre columns by default (the format file shows Bangalore by centre)
EXPAND_REGIONS = ["Bangalore"]
# Left out of the grand-total column (format file: "All India (Excl Bangalore)")
EXCLUDE_FROM_TOTAL = ["Bangalore"]


def _div(a, b):
    if a is None or not b:
        return None
    return a / b


def _num(x, d=2):
    return DASH if x is None else f"{x:.{d}f}"


def _pair(actual, target, d=2):
    """'2.76 (2.15)' -> actual (target). Same look as the format file."""
    if actual is None:
        return DASH
    if target is None:
        return f"{actual:.{d}f}"
    return f"{actual:.{d}f} ({target:.{d}f})"


def _pct(x):
    return DASH if x is None else f"{int(x * 100 + 0.5)}%"


def _below(a, meets_key):
    e, m = a.get("enrol"), a.get(meets_key)
    if not e or m is None:
        return None
    return (e - m) / e


METRICS = [
    {"label": "Enrolments", "bold": True,
     "fn": lambda a: DASH if a.get("enrol") is None else f"{a['enrol']:.0f}"},
    {"label": "myPlan / student", "bold": True,
     "fn": lambda a: _pair(_div(a.get("myplan_total"), a.get("enrol")), a.get("mpe_target"))},
    {"label": "myPlan Coverage", "bold": True,
     "fn": lambda a: _pair(_div(a.get("myplan_atleast1"), a.get("enrol")), a.get("mp_cov_target"))},
    {"label": "% student below Benchmark (myPlan)",
     "fn": lambda a: _pct(_below(a, "myplan_meets"))},
    {"label": "Benchmarking tests / student",
     "fn": lambda a: _num(_div(a.get("bt_total"), a.get("enrol")))},
    {"label": "Module tests / student",
     "fn": lambda a: _num(_div(a.get("mt_total"), a.get("enrol")))},
    {"label": "SimTests / student",
     "fn": lambda a: _pair(_div(a.get("sim_total"), a.get("enrol")), a.get("tpe_target"))},
    {"label": "SimTest Coverage",
     "fn": lambda a: _pair(_div(a.get("sim_atleast1"), a.get("enrol")), a.get("sim_cov_target"))},
    {"label": "% student below Benchmark (SimTest)",
     "fn": lambda a: _pct(_below(a, "sim_meets"))},
]


def aggregate(raws):
    """Combine the raw dicts of several centres into one."""
    out = {}
    for f in SUM_FIELDS:
        vals = [r.get(f) for r in raws if r.get(f) is not None]
        out[f] = sum(vals) if vals else None
    for f in WAVG_FIELDS:
        pairs = [(r[f], r.get("enrol") or 0) for r in raws if r.get(f) is not None]
        den = sum(w for _, w in pairs)
        out[f] = sum(v * w for v, w in pairs) / den if den else None
    return out


def column_values(raws):
    a = aggregate(raws)
    return [m["fn"](a) for m in METRICS]
