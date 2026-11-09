import importlib.util
import os
import re
import secrets
from functools import wraps

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import ingest
import metrics
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

    @app.before_request
    def _init():
        if not state["ready"]:
            db.create_all()
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

    def visible_rows(u):
        rows = CenterData.query.order_by(CenterData.id).all()
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
    @app.route("/")
    @login_required()
    def dashboard(u):
        rows = visible_rows(u)
        f = {k: request.args.get(k, "") for k in ("coco", "region", "city", "center")}
        opts = {
            "coco": sorted({r.coco for r in rows if r.coco}),
            "region": sorted({r.region for r in rows}),
            "city": sorted({r.city for r in rows if not f["region"] or r.region == f["region"]}),
            "center": sorted({r.center for r in rows
                              if (not f["region"] or r.region == f["region"])
                              and (not f["city"] or r.city == f["city"])}),
        }
        for k, attr in (("coco", "coco"), ("region", "region"), ("city", "city"), ("center", "center")):
            if f[k]:
                rows = [r for r in rows if getattr(r, attr) == f[k]]

        by_region = {}
        for r in rows:
            by_region.setdefault(r.region, []).append(r)
        expand_all = request.args.get("expand") == "1"
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

        upd = db.session.get(Meta, "last_updated")
        return render_template("dashboard.html", u=u, cols=cols, metrics=metrics.METRICS, f=f, opts=opts,
                               expand_all=expand_all, updated=upd.value if upd else None)

    # ---------- admin: users ----------
    @app.route("/admin/users")
    @login_required(admin=True)
    def admin_users(u):
        return render_template("admin_users.html", u=u, users=User.query.order_by(User.email).all(),
                               regions=sorted({r.region for r in CenterData.query.all()}),
                               centers=sorted({r.center for r in CenterData.query.all()}))

    @app.route("/admin/users/save", methods=["POST"])
    @login_required(admin=True)
    def admin_user_save(u):
        uid = request.form.get("id")
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        target = db.session.get(User, int(uid)) if uid else None
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
        text = "\n".join(f"{c} = {mp.get(c, c)}" for c in cities)
        meta = {m.key: m.value for m in Meta.query.all()}
        return render_template("admin_data.html", u=u, meta=meta, n=CenterData.query.count(),
                               regionmap=text, gsheet=bool(os.environ.get("GSHEET_ID")),
                               sheets=(ingest.MYPLAN_SHEET, ingest.SIM_SHEET))

    def _run(loader, label):
        try:
            n, mismatch = ingest.store(*loader(), source=label)
            msg = f"Loaded {n} centres from {label}."
            if mismatch:
                msg += f" Only in one sheet: {', '.join(mismatch[:8])}"
            flash(msg, "ok")
        except Exception as e:  # show the real reason to the admin
            db.session.rollback()
            flash(f"Import failed: {e}", "err")

    @app.route("/admin/data/upload", methods=["POST"])
    @login_required(admin=True)
    def admin_upload(u):
        fl = request.files.get("file")
        if not fl or not fl.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("Choose the .xlsx file", "err")
        else:
            _run(lambda: ingest.rows_from_xlsx(fl.stream), f"upload: {fl.filename}")
        return redirect(url_for("admin_data"))

    @app.route("/admin/data/sync", methods=["POST"])
    @login_required(admin=True)
    def admin_sync(u):
        _run(ingest.rows_from_gsheet, "Google Sheet")
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
        n, _ = ingest.store(*ingest.rows_from_gsheet(), source="Google Sheet (cron)")
        return jsonify(ok=True, centres=n)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
