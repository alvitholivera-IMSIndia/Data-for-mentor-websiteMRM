import csv
import importlib.util
import io
import os
import re
import secrets
from functools import wraps

from datetime import date

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from sqlalchemy import inspect, text
from werkzeug.security import check_password_hash, generate_password_hash

import ingest
import metrics
import products as P
from models import CenterData, Meta, RegionMap, User, db


# Admin login (created / reset automatically every time the app starts)
ADMIN_EMAIL = "alvith.olivera@imsindia.com"
ADMIN_PASSWORD = "Welcome@123"


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
    uri = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if uri:
        # Neon/Vercel may give postgres://, postgresql:// or postgresql+psycopg://.
        # Force the driver that is actually installed.
        driver = "psycopg2" if importlib.util.find_spec("psycopg2") else "psycopg"
        uri = re.sub(r"^postgres(ql)?(\+\w+)?://", f"postgresql+{driver}://", uri)
        uri = uri.replace("&channel_binding=require", "").replace("channel_binding=require&", "")
    else:  # local / fallback only. Vercel's disk is temporary, so use Postgres in production.
        path = "/tmp/mrm.db" if os.environ.get("VERCEL") else os.path.join(os.path.dirname(__file__), "mrm.db")
        uri = "sqlite:///" + path
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024   # Vercel function body limit is ~4.5 MB
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    db.init_app(app)
    state = {"ready": False}

    def ensure_schema():
        """Upgrade an existing (PG-only) database in place. Safe to run on every start."""
        try:
            insp = inspect(db.engine)
            tables = set(insp.get_table_names())
            if "users" in tables and "products" not in {c["name"] for c in insp.get_columns("users")}:
                db.session.execute(text("ALTER TABLE users ADD COLUMN products VARCHAR(100) NOT NULL DEFAULT 'PG'"))
                db.session.commit()
            # one time: carry the old PG rows into the product-aware table
            if "center_data" in tables and not db.session.get(Meta, "migrated_products"):
                if db.session.execute(text("SELECT COUNT(*) FROM center_rows")).scalar() == 0:
                    db.session.execute(text(
                        "INSERT INTO center_rows (product, coco, city, region, center, raw) "
                        "SELECT 'PG', coco, city, region, center, raw FROM center_data"))
                for old, new in (("last_updated", "last_updated:PG"), ("last_source", "last_source:PG")):
                    m = db.session.get(Meta, old)
                    if m and not db.session.get(Meta, new):
                        db.session.add(Meta(key=new, value=m.value))
                db.session.add(Meta(key="migrated_products", value="1"))
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.warning("Schema upgrade skipped: %s", e)

    @app.before_request
    def _init():
        if not state["ready"]:
            db.create_all()
            ensure_schema()
            try:
                email, pw = ADMIN_EMAIL.strip().lower(), ADMIN_PASSWORD
                adm = User.query.filter_by(email=email).first() or User(email=email, name="Admin")
                adm.is_admin, adm.active, adm.regions = True, True, "*"
                adm.password_hash = generate_password_hash(pw)
                db.session.add(adm)
                db.session.commit()
            except Exception as e:   # e.g. two instances starting at once: don't crash the page
                db.session.rollback()
                app.logger.warning("Admin sync skipped: %s", e)
            state["ready"] = True

    # ---------- helpers ----------
    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return session["csrf"]

    app.jinja_env.globals["csrf"] = csrf

    @app.before_request
    def _csrf_check():
        if request.method == "POST" and request.endpoint != "cron_sync":
            if request.form.get("csrf") != session.get("csrf"):
                abort(400, "Bad CSRF token, reload the page")

    def current_user():
        uid = session.get("uid")
        u = db.session.get(User, uid) if uid else None
        return u if u and u.active else None

    def login_required(admin=False):
        def deco(f):
            @wraps(f)
            def wrapper(*a, **kw):
                u = current_user()
                if not u:
                    return redirect(url_for("login"))
                if admin and not u.is_admin:
                    abort(403)
                return f(u, *a, **kw)
            return wrapper
        return deco

    def allowed_products(u):
        enabled = P.enabled_codes()
        if u.is_admin or "*" in u.product_list:
            return enabled
        mine = set(u.product_list)
        return [c for c in enabled if c in mine]

    def active_product(u):
        """The product being viewed: ?p=UG, else the last one used, else the first allowed."""
        if "product" not in g:
            allowed = allowed_products(u)
            want = (request.args.get("p") or session.get("product") or "").upper()
            g.product = want if want in allowed else (allowed[0] if allowed else None)
            if g.product and session.get("product") != g.product:
                session["product"] = g.product
        return g.product

    @app.context_processor
    def _inject():
        u = current_user()
        if not u:
            return {}
        return dict(allowed=allowed_products(u), product=active_product(u), PRODUCTS=P.PRODUCTS)

    def last_updated(product):
        m = db.session.get(Meta, f"last_updated:{product}")
        return m.value if m else None

    def visible_rows(u, product):
        rows = CenterData.query.filter_by(product=product).order_by(CenterData.id).all() if product else []
        if u.sees_everything:
            return rows
        regs = {r.lower() for r in u.region_list}
        cs = {c.lower() for c in u.center_list}
        return [r for r in rows if r.region.lower() in regs and (not cs or r.center.lower() in cs)]

    # ---------- auth ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            u = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
            if u and u.active and check_password_hash(u.password_hash, request.form.get("password", "")):
                session.clear()
                session["uid"] = u.id
                return redirect(url_for("dashboard"))
            flash("Wrong email or password", "err")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- dashboard ----------
    FILTER_KEYS = ("coco", "region", "city", "center")

    def build_cols(u, rows, f, expand_all):
        by_region = {}
        for r in rows:
            by_region.setdefault(r.region, []).append(r)
        cols = []
        for region, rs in by_region.items():
            expand = len(rs) > 1 and (expand_all or region in metrics.EXPAND_REGIONS
                                      or len(by_region) == 1 or bool(f["city"]))
            if expand:
                for r in rs:
                    cols.append({"label": r.center, "kind": "centre", "vals": metrics.column_values([r.data])})
                cols.append({"label": f"{region} - Total", "kind": "total",
                             "vals": metrics.column_values([r.data for r in rs])})
            else:
                cols.append({"label": region, "kind": "total",
                             "vals": metrics.column_values([r.data for r in rs])})
        if len(by_region) > 1:
            use = [r for r in rows if not (u.sees_everything and r.region in metrics.EXCLUDE_FROM_TOTAL)]
            excl = u.sees_everything and any(r.region in metrics.EXCLUDE_FROM_TOTAL for r in rows)
            label = "All India (Excl " + ", ".join(metrics.EXCLUDE_FROM_TOTAL) + ")" if excl else "Total"
            cols.append({"label": label, "kind": "grand",
                         "vals": metrics.column_values([r.data for r in use])})
        return cols

    def dash_context(u):
        product = active_product(u)
        rows = visible_rows(u, product)
        f = {k: request.args.get(k, "") for k in FILTER_KEYS}
        opts = {
            "coco": sorted({r.coco for r in rows if r.coco}),
            "region": sorted({r.region for r in rows}),
            "city": sorted({r.city for r in rows if not f["region"] or r.region == f["region"]}),
            "center": sorted({r.center for r in rows
                              if (not f["region"] or r.region == f["region"])
                              and (not f["city"] or r.city == f["city"])}),
        }
        for k in FILTER_KEYS:
            if f[k]:
                rows = [r for r in rows if getattr(r, k) == f[k]]
        # The table only appears once something is selected. If this login has nothing to
        # choose between (every filter has a single option), show it straight away.
        selectable = any(len(v) > 1 for v in opts.values())
        has_sel = any(f.values()) or not selectable
        expand_all = request.args.get("expand") == "1"
        cols = build_cols(u, rows, f, expand_all) if has_sel else []
        return dict(f=f, opts=opts, has_sel=has_sel, expand_all=expand_all, cols=cols, product=product)

    def _safe(x):          # stop spreadsheet formula injection in exported text
        x = str(x)
        return "'" + x if x[:1] in ("=", "+", "-", "@") else x

    def csv_response(cols, name, product):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([P.PRODUCTS[product]["heading"]] + [_safe(c["label"]) for c in cols])
        for i, m in enumerate(metrics.METRICS):
            w.writerow([m["label"]] + [c["vals"][i] for c in cols])
        return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}-{product}-{date.today():%Y-%m-%d}.csv"})

    @app.route("/")
    @login_required()
    def dashboard(u):
        ctx = dash_context(u)
        return render_template("dashboard.html", u=u, metrics=metrics.METRICS,
                               updated=last_updated(ctx["product"]), **ctx)

    @app.route("/export.csv")
    @login_required()
    def export_csv(u):
        ctx = dash_context(u)
        if not ctx["has_sel"] or not ctx["cols"]:
            abort(400, "Select something on the dashboard first")
        return csv_response(ctx["cols"], "mrm-report", ctx["product"])

    # ---------- comparison ----------
    def compare_context(u):
        product = active_product(u)
        rows = visible_rows(u, product)
        cities = sorted({r.city for r in rows if r.city})
        sel = []
        for i in (1, 2, 3):
            c = request.args.get(f"c{i}", "")
            sel.append(c if c in cities and c not in sel else "")
        cols = []
        picked = [c for c in sel if c]
        if len(picked) >= 2:
            for c in picked:
                cols.append({"label": c, "kind": "total",
                             "vals": metrics.column_values([r.data for r in rows if r.city == c])})
        return dict(cities=cities, sel=sel, cols=cols, product=product)

    @app.route("/compare")
    @login_required()
    def compare(u):
        ctx = compare_context(u)
        return render_template("compare.html", u=u, metrics=metrics.METRICS,
                               updated=last_updated(ctx["product"]), **ctx)

    @app.route("/compare.csv")
    @login_required()
    def compare_csv(u):
        ctx = compare_context(u)
        if not ctx["cols"]:
            abort(400, "Select at least 2 cities first")
        return csv_response(ctx["cols"], "mrm-comparison", ctx["product"])

    # ---------- admin: users ----------
    @app.route("/admin/users")
    @login_required(admin=True)
    def admin_users(u):
        return render_template("admin_users.html", u=u, users=User.query.order_by(User.email).all(),
                               prods={c: P.PRODUCTS[c] for c in P.enabled_codes()},
                               regions=sorted({r.region for r in CenterData.query.all()}),
                               centers=sorted({r.center for r in CenterData.query.all()}))

    @app.route("/admin/users/save", methods=["POST"])
    @login_required(admin=True)
    def admin_user_save(u):
        uid = request.form.get("id")
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        target = db.session.get(User, int(uid)) if uid else None
        codes = [c for c in request.form.getlist("products") if c in P.enabled_codes()]
        if not codes and not request.form.get("is_admin") and not (target and target.id == u.id):
            flash("Tick at least one product (PG / UG) for a mentor", "err")
            return redirect(url_for("admin_users"))
        if not target:
            if not email or len(pw) < 6:
                flash("Email and a password of at least 6 characters are required", "err")
                return redirect(url_for("admin_users"))
            if User.query.filter_by(email=email).first():
                flash("That email already exists", "err")
                return redirect(url_for("admin_users"))
            target = User(email=email)
            db.session.add(target)
        target.name = request.form.get("name", "").strip()
        target.regions = request.form.get("regions", "").strip()
        target.centers = request.form.get("centers", "").strip()
        target.products = ",".join(codes)
        target.is_admin = bool(request.form.get("is_admin"))
        target.active = bool(request.form.get("active")) if uid else True
        if target.id == u.id:                      # never lock yourself out
            target.is_admin, target.active = True, True
        if pw:
            if len(pw) < 6:
                flash("Password must be at least 6 characters", "err")
                return redirect(url_for("admin_users"))
            target.password_hash = generate_password_hash(pw)
        db.session.commit()
        flash(f"Saved {target.email}", "ok")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:uid>/delete", methods=["POST"])
    @login_required(admin=True)
    def admin_user_delete(u, uid):
        t = db.session.get(User, uid)
        if t and t.id != u.id:
            db.session.delete(t)
            db.session.commit()
            flash(f"Deleted {t.email}", "ok")
        return redirect(url_for("admin_users"))

    # ---------- admin: data ----------
    @app.route("/admin/data")
    @login_required(admin=True)
    def admin_data(u):
        mp = {m.city: m.region for m in RegionMap.query.all()}
        cities = sorted({r.city for r in CenterData.query.all()})
        text_ = "\n".join(f"{c} = {mp.get(c, c)}" for c in cities)
        meta = {m.key: m.value for m in Meta.query.all()}
        plist = []
        for c in P.enabled_codes():
            plist.append(dict(code=c, label=P.PRODUCTS[c]["label"],
                              n=CenterData.query.filter_by(product=c).count(),
                              updated=meta.get(f"last_updated:{c}"), source=meta.get(f"last_source:{c}"),
                              gsheet=bool(P.sheet_id(c)) and bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")),
                              env=P.env_hint(c), tabs=(P.tab(c, "myplan"), P.tab(c, "sim"))))
        return render_template("admin_data.html", u=u, plist=plist, regionmap=text_)

    def _product_from_form():
        code = (request.form.get("product") or "").upper()
        return code if code in P.enabled_codes() else None

    def _run(product, loader, label):
        try:
            n, mismatch = ingest.store(product, *loader(), source=label)
            msg = f"{P.PRODUCTS[product]['label']}: loaded {n} centres from {label}."
            if mismatch:
                msg += f" Only in one sheet: {', '.join(mismatch[:8])}"
            flash(msg, "ok")
        except Exception as e:  # show the real reason to the admin
            db.session.rollback()
            flash(f"Import failed: {e}", "err")

    @app.route("/admin/data/upload", methods=["POST"])
    @login_required(admin=True)
    def admin_upload(u):
        product, fl = _product_from_form(), request.files.get("file")
        if not product:
            flash("Unknown product", "err")
        elif not fl or not fl.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("Choose the .xlsx file", "err")
        else:
            _run(product, lambda: ingest.rows_from_xlsx(fl.stream, product), f"upload: {fl.filename}")
        return redirect(url_for("admin_data"))

    @app.route("/admin/data/sync", methods=["POST"])
    @login_required(admin=True)
    def admin_sync(u):
        product = _product_from_form()
        if not product:
            flash("Unknown product", "err")
        else:
            _run(product, lambda: ingest.rows_from_gsheet(product), "Google Sheet")
        return redirect(url_for("admin_data"))

    @app.route("/admin/data/regions", methods=["POST"])
    @login_required(admin=True)
    def admin_regions(u):
        RegionMap.query.delete()
        for line in request.form.get("map", "").splitlines():
            if "=" in line:
                c, r = [x.strip() for x in line.split("=", 1)]
                if c and r and c != r:
                    db.session.add(RegionMap(city=c, region=r))
        db.session.commit()
        ingest.reapply_region_map()
        flash("Region mapping saved", "ok")
        return redirect(url_for("admin_data"))

    @app.route("/api/cron-sync")
    def cron_sync():
        secret = os.environ.get("CRON_SECRET")
        if not secret or request.headers.get("Authorization") != f"Bearer {secret}":
            abort(401)
        out = {}
        for code in P.enabled_codes():
            if not P.sheet_id(code):
                continue
            try:
                n, _ = ingest.store(code, *ingest.rows_from_gsheet(code), source="Google Sheet (cron)")
                out[code] = n
            except Exception as e:
                db.session.rollback()
                out[code] = f"error: {e}"
        return jsonify(ok=all(isinstance(v, int) for v in out.values()), centres=out)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
