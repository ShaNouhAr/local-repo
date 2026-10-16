"""Vue d'ensemble."""

import datetime

from flask import render_template

from . import app
from .auth import login_required, can
from .db import db
from .linux import list_debs
from .util import disk_usage, read_stats, service_active
from .wupdate import wu_stats

STATUS_LABELS = {"ready": "prêt", "downloading": "téléchargement", "error": "erreur", "pending": "en attente"}


def dashboard_data():
    stats = read_stats()
    apps = db().execute("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM win_apps WHERE status='ready'").fetchone()
    debs = list_debs()
    wu = wu_stats()
    return {
        "services": {"acng": service_active("apt-cacher-ng"), "nginx": service_active("nginx"), "portal": True},
        "acng_size": stats.get("acng", 0),
        "wucache_size": stats.get("wucache", 0),
        "flatpak_cache": stats.get("flatpakcache", 0),
        "debs": len(debs),
        "debs_size": sum(d["size"] for d in debs),
        "apps": apps["n"],
        "apps_size": apps["s"],
        "wu": {k: wu[k] for k in ("hits", "misses", "hit_bytes", "miss_bytes", "ratio")},
        "disk": disk_usage(),
        "stats_updated": stats.get("updated", ""),
    }


def recent_activity(limit=6):
    events = []
    for d in list_debs() if can("linux.view") else []:
        events.append({"when": datetime.datetime.fromtimestamp(d["mtime"]).strftime("%Y-%m-%d %H:%M"),
                       "kind": "linux", "title": d["package"], "detail": f"{d['version']} · {d['arch']}"})
    for r in db().execute("SELECT name, version, kind, status, updated_at FROM win_apps") if can("windows.view") else []:
        events.append({"when": r["updated_at"], "kind": "windows", "title": r["name"],
                       "detail": " · ".join(x for x in (r["version"], r["kind"], STATUS_LABELS.get(r["status"], "")) if x)})
    events.sort(key=lambda e: e["when"], reverse=True)
    return events[:limit]


@app.route("/")
@login_required
def index():
    return render_template("index.html", d=dashboard_data(), activity=recent_activity())


@app.route("/api/status")
@login_required
def api_status():
    return dashboard_data()
