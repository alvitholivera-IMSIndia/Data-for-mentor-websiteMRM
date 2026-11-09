"""Create or reset an admin login.

Usage:
    python create_admin.py alvith.olivera@imsindia.com 'Welcome@123'
    (or set ADMIN_EMAIL / ADMIN_PASSWORD and run without arguments)

Uses the same database as the app (DATABASE_URL / POSTGRES_URL, else local mrm.db).
"""
import os
import sys

from werkzeug.security import generate_password_hash

from app import app
from models import User, db


def main():
    email = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ADMIN_EMAIL", "")).strip().lower()
    pw = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ADMIN_PASSWORD", "")
    if not email or len(pw) < 6:
        sys.exit("Give an email and a password of at least 6 characters.")
    with app.app_context():
        db.create_all()
        u = User.query.filter_by(email=email).first()
        action = "Updated"
        if not u:
            u, action = User(email=email, name="Admin"), "Created"
            db.session.add(u)
        u.is_admin, u.active, u.regions = True, True, "*"
        u.password_hash = generate_password_hash(pw)
        db.session.commit()
        print(f"{action} admin: {email}")


if __name__ == "__main__":
    main()
