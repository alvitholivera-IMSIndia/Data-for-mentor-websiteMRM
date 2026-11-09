# MRM Report – mentor dashboard (Flask + Postgres, deploy on Vercel)

## Run locally
    pip install -r requirements.txt
    cp .env.example .env   # or just export ADMIN_EMAIL / ADMIN_PASSWORD
    python make_demo_workbook.py          # optional demo data
    ADMIN_EMAIL=you@ims.com ADMIN_PASSWORD=secret1 python app.py
Open http://localhost:5000 -> log in as admin -> Data -> upload the workbook -> Users -> add mentors.

## Deploy on Vercel
1. Push this folder to GitHub, import it in Vercel (Flask is detected from `app.py`).
2. Add a Postgres database (Vercel Marketplace -> Neon). It sets `DATABASE_URL` / `POSTGRES_URL`.
   Do NOT rely on SQLite on Vercel: its disk is wiped between requests.
3. Environment variables: `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` (first admin is created automatically).
4. Optional Google Sheet sync: create a Google service account, enable the Sheets API, share the
   MRM sheet with the service account email (Viewer), set `GSHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON`.
   For auto-refresh add a Vercel Cron hitting `/api/cron-sync` and set `CRON_SECRET`.

## How it works
- `models.py`   users (email, hashed password, region(s), centre(s)), centre data (raw counts as JSON), city->region map.
- `ingest.py`   reads sheets `Checking` (myPlan) and `CheckingSim` (SimTest); columns are found by header keywords.
- `metrics.py`  THE FORMAT. One entry per row of the format file; add a row here to add a metric.
- Mentors only get rows whose region (and centre, if set) is assigned to them; enforced on the server.
