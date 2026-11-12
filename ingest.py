"""
Reads the two pivot sheets of a product (myPlan + SimTest tabs, see products.py) from either
an uploaded .xlsx or directly from Google Sheets, and stores one row per centre per product.
Columns are found by header *keywords*, so reordering columns / renaming slightly is fine.
"""
import io
import json
import os
import re
from datetime import datetime, timezone

import openpyxl

import products as P
from models import db, CenterData, RegionMap, Meta

# field -> list of keyword sets; a header matches when it contains ALL words of one set
MYPLAN_RULES = {
    "enrol": [["enrol"]],
    "myplan_target_total": [["target", "myplan"]],
    "myplan_total": [["total", "myplan"]],
    "myplan_atleast1": [["least"]],
    "mpe_target": [["mpe", "target"]],
    "mp_cov_target": [["coverage", "target"]],
    "myplan_meets": [["bench"]],   # PG header = students meeting benchmark; UG header = "# Below ..." (handled below)
}
SIM_RULES = {
    "enrol": [["enrol"]],
    "sim_target_total": [["target", "simcat"]],
    "sim_total": [["total", "simcat"]],
    "sim_atleast1": [["least"]],
    "tpe_target": [["tpe", "target"]],
    "sim_cov_target": [["coverage", "target"]],
    "sim_meets": [["bench"]],
}


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def _to_num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def parse_pivot(rows, rules):
    """rows: list of lists (a sheet). Returns {center: {coco, city, raw{...}}}"""
    hdr_i = next((i for i, r in enumerate(rows[:15])
                  if any("enrol" in _norm(c) for c in r)), None)
    if hdr_i is None:
        raise ValueError("Could not find a header row containing 'Enrolments'")
    heads = [_norm(c) for c in rows[hdr_i]]

    def find(pred):
        return next((i for i, h in enumerate(heads) if h and pred(h)), None)

    c_center = find(lambda h: "centre" in h or "center" in h)
    c_city = find(lambda h: h == "city")
    c_coco = find(lambda h: "coco" in h)
    if c_center is None:
        raise ValueError("Could not find the centre column (header with 'Centre')")

    cols = {}
    for field, kwsets in rules.items():
        for i, h in enumerate(heads):
            if h and any(all(k in h for k in ks) for ks in kwsets) and i not in cols.values():
                cols[field] = i
                break

    # If the benchmark column is headed "... Below ... Benchmark" it counts students BELOW the
    # benchmark; we store "meets" everywhere, so convert it (meets = enrolments - below).
    below_fields = {f for f, i in cols.items() if f.endswith("_meets") and "below" in heads[i]}

    out, coco, city = {}, "", ""
    for r in rows[hdr_i + 1:]:
        r = list(r) + [None] * (len(heads) - len(r))
        if c_coco is not None and r[c_coco]:
            coco = str(r[c_coco]).strip()
        if c_city is not None and r[c_city]:
            city = str(r[c_city]).strip()
        center = str(r[c_center]).strip() if r[c_center] else ""
        if not center or "total" in center.lower():
            continue                         # pivot subtotal / grand-total rows
        raw = {f: _to_num(r[i]) for f, i in cols.items()}
        if raw.get("enrol") is None:
            continue
        for f in below_fields:
            if raw.get(f) is not None:
                raw[f] = raw["enrol"] - raw[f]
        out[center] = {"coco": coco, "city": city, "raw": raw}
    return out


def _find_sheet(wb, name):
    for ws in wb.worksheets:
        if ws.title.strip().lower() == name.lower():
            return ws
    raise ValueError(f"Sheet '{name}' not found. Sheets present: {[w.title for w in wb.worksheets]}")


def rows_from_xlsx(fileobj, product):
    wb = openpyxl.load_workbook(io.BytesIO(fileobj.read()), data_only=True, read_only=True)
    mp = [list(r) for r in _find_sheet(wb, P.tab(product, "myplan")).iter_rows(values_only=True)]
    sm = [list(r) for r in _find_sheet(wb, P.tab(product, "sim")).iter_rows(values_only=True)]
    return mp, sm


def match_tab(titles, name):
    """Case/space-insensitive tab lookup. Returns the real title."""
    for t in titles:
        if t.strip().lower() == name.strip().lower():
            return t
    raise ValueError(f"Tab '{name}' not found. Tabs present: {titles}")


def rows_from_gsheet(product):
    """Direct Google Sheet read through a service account (share the sheet with its email)."""
    import requests
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    label = P.PRODUCTS[product]["label"]
    sheet_id = P.sheet_id(product)
    sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheet_id:
        raise ValueError(f"{label}: no Google Sheet configured (set {P.env_hint(product)} in Vercel)")
    if not sa:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")
    info = json.loads(sa)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    creds.refresh(Request())
    hdr = {"Authorization": f"Bearer {creds.token}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"

    def get(url, **kw):
        r = requests.get(url, headers=hdr, timeout=30, **kw)
        if r.status_code in (400, 403, 404):
            raise ValueError(f"{label}: Google refused the request (HTTP {r.status_code}). Check that the sheet ID is right and "
                             f"that the sheet is shared (Viewer) with {info.get('client_email', 'the service account')}. "
                             f"An Excel file stored in Drive must first be saved as a Google Sheet.")
        r.raise_for_status()
        return r.json()

    titles = [x["properties"]["title"] for x in get(base, params={"fields": "sheets.properties.title"})["sheets"]]

    def grab(name):
        real = match_tab(titles, name)
        data = get(f"{base}/values/{requests.utils.quote(real)}", params={"valueRenderOption": "UNFORMATTED_VALUE"})
        return data.get("values", [])

    return grab(P.tab(product, "myplan")), grab(P.tab(product, "sim"))


def store(product, myplan_rows, sim_rows, source):
    mp = parse_pivot(myplan_rows, MYPLAN_RULES)
    sm = parse_pivot(sim_rows, SIM_RULES)
    rmap = {m.city: m.region for m in RegionMap.query.all()}

    centers = list(dict.fromkeys(list(mp) + list(sm)))
    CenterData.query.filter_by(product=product).delete()
    for c in centers:
        raw, coco, city = {}, "", ""
        for src in (mp.get(c), sm.get(c)):
            if src:
                raw.update({k: v for k, v in src["raw"].items() if k != "enrol" or "enrol" not in raw})
                coco = coco or src["coco"]
                city = city or src["city"]
        city = city or c.split(" - ")[0]
        db.session.add(CenterData(product=product, coco=coco or "Unknown", city=city, region=rmap.get(city, city),
                                  center=c, raw=json.dumps(raw)))
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    for k, v in ((f"last_updated:{product}", stamp), (f"last_source:{product}", source)):
        row = db.session.get(Meta, k) or Meta(key=k)
        row.value = v
        db.session.add(row)
    db.session.commit()
    return len(centers), sorted(set(mp) ^ set(sm))   # count, centres present in only one sheet


def reapply_region_map():
    rmap = {m.city: m.region for m in RegionMap.query.all()}
    for row in CenterData.query.all():
        row.region = rmap.get(row.city, row.city)
    db.session.commit()
