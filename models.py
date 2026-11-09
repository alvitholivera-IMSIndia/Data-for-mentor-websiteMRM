import json
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    # comma separated. "*" = every region. Matches CenterData.region
    regions = db.Column(db.Text, default="")
    # comma separated centre names. Empty = all centres in the allowed regions
    centers = db.Column(db.Text, default="")
    active = db.Column(db.Boolean, default=True, nullable=False)

    @staticmethod
    def _split(s):
        return [x.strip() for x in (s or "").split(",") if x.strip()]

    @property
    def region_list(self):
        return self._split(self.regions)

    @property
    def center_list(self):
        return self._split(self.centers)

    @property
    def sees_everything(self):
        return self.is_admin or "*" in self.region_list


class CenterData(db.Model):
    """One row per centre. `raw` holds the raw counts coming from the pivots (JSON),
    so adding a new field later does not need a schema change."""
    __tablename__ = "center_data"
    id = db.Column(db.Integer, primary_key=True)
    coco = db.Column(db.String(60), default="")
    city = db.Column(db.String(120), default="")
    region = db.Column(db.String(120), default="", index=True)
    center = db.Column(db.String(200), unique=True, nullable=False)
    raw = db.Column(db.Text, default="{}")

    @property
    def data(self):
        return json.loads(self.raw or "{}")


class RegionMap(db.Model):
    """city (as in the pivot) -> region (what mentors are assigned to). Default region = city."""
    __tablename__ = "region_map"
    city = db.Column(db.String(120), primary_key=True)
    region = db.Column(db.String(120), nullable=False)


class Meta(db.Model):
    __tablename__ = "meta"
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text, default="")
