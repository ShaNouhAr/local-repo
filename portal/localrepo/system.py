"""Page Système, contrôle de santé et métriques Prometheus."""

import datetime
import os
import subprocess
import sys

from flask import Response, render_template

from . import app, config
from .auth import admin_required
from .db import db
from .linux import list_debs
from .util import disk_usage, read_stats, service_active
from .wupdate import wu_stats

UNITS = [
    ("local-repo-portal", "Interface et scripts clients"),
    ("nginx", "HTTPS, fichiers, cache Windows Update"),
    ("apt-cacher-ng", "Cache des paquets Linux"),
    ("local-repo-stats.timer", "Taille des caches, toutes les 15 min"),
    ("local-repo-nginx.path", "Recharge nginx quand la liste des domaines change"),
    ("local-repo-backup.timer", "Sauvegarde quotidienne"),
]


def cert_info(path):
    """Résumé d'un certificat X.509 (via openssl)."""
    try:
        out = subprocess.run(["openssl", "x509", "-in", path, "-noout", "-subject", "-issuer", "-startdate",
                              "-enddate", "-fingerprint", "-sha256", "-ext", "subjectAltName"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    info = {"san": []}
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("subject="):
            info["subject"] = line.split("=", 1)[1].strip()
        elif line.startswith("issuer="):
            info["issuer"] = line.split("=", 1)[1].strip()
        elif line.startswith("notBefore="):
            info["start"] = _cert_date(line.split("=", 1)[1])
        elif line.startswith("notAfter="):
            info["end"] = _cert_date(line.split("=", 1)[1])
        elif "Fingerprint=" in line:
            info["sha256"] = line.split("=", 1)[1]
        elif line.startswith(("DNS:", "IP Address:")):
            info["san"] = [x.strip().replace("IP Address:", "IP:") for x in line.split(",")]
    return info


def _cert_date(text):
    try:
        d = datetime.datetime.strptime(text.strip(), "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return {"text": text.strip(), "days": None}
    return {"text": d.strftime("%d/%m/%Y"), "days": (d - datetime.datetime.utcnow()).days}


def package_version(name):
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Version}", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def list_backups():
    try:
        names = sorted((n for n in os.listdir(config.BACKUP_DIR) if n.endswith(".tar.gz")), reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        st = os.stat(os.path.join(config.BACKUP_DIR, n))
        out.append({"name": n, "size": st.st_size,
                    "when": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "mtime": st.st_mtime})
    return out


@app.route("/systeme")
@admin_required
def system():
    return render_template(
        "system.html",
        services=[(unit, desc, service_active(unit)) for unit, desc in UNITS],
        cert=cert_info(os.path.join(config.TLS_DIR, "server.crt")),
        ca=cert_info(os.path.join(config.TLS_DIR, "ca.crt")),
        versions={"local-repo": config.VERSION, "nginx": package_version("nginx"),
                  "apt-cacher-ng": package_version("apt-cacher-ng"), "Python": sys.version.split()[0],
                  "Flask": package_version("python3-flask")},
        disk=disk_usage(),
        stats=read_stats(),
        backups=list_backups(),
        backup_dir=config.BACKUP_DIR,
        paths={"Données": config.DATA, "État et base": config.STATE, "Configuration": config.ETC,
               "Certificats": config.TLS_DIR, "Sauvegardes": config.BACKUP_DIR},
    )


@app.route("/healthz")
def healthz():
    db().execute("SELECT 1").fetchone()
    return "ok"


@app.route("/metrics")
def metrics():
    """Format texte Prometheus. Accès limité par nginx aux réseaux d'administration."""
    stats = read_stats()
    wu = wu_stats()
    disk = disk_usage()
    lines = []

    def metric(name, help_text, kind, samples):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            label = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else ""
            lines.append(f"{name}{label} {value}")

    metric("local_repo_info", "Version de local-repo.", "gauge", [({"version": config.VERSION}, 1)])
    metric("local_repo_service_up", "Unité systemd active (1) ou non (0).", "gauge",
           [({"unit": u}, int(service_active(u))) for u, _ in UNITS])
    metric("local_repo_cache_bytes", "Taille des caches sur disque.", "gauge",
           [({"cache": "apt"}, stats.get("acng", 0)), ({"cache": "windows_update"}, stats.get("wucache", 0)),
            ({"cache": "flathub"}, stats.get("flatpakcache", 0)), ({"cache": "rpm"}, stats.get("rpmcache", 0))])
    metric("local_repo_wu_recent_bytes", "Octets Windows Update récents servis depuis le cache ou Internet.", "gauge",
           [({"source": "cache"}, wu["hit_bytes"]), ({"source": "internet"}, wu["miss_bytes"])])
    metric("local_repo_deb_packages", "Paquets du dépôt APT local.", "gauge", [({}, len(list_debs()))])
    if config.RPM_ENABLED:
        from .rpmrepo import list_rpms
        metric("local_repo_rpm_packages", "Paquets des dépôts RPM locaux.", "gauge", [({}, len(list_rpms()))])
    rows = db().execute("SELECT status, COUNT(*) AS n FROM win_apps GROUP BY status").fetchall()
    metric("local_repo_windows_apps", "Logiciels du catalogue Windows par état.", "gauge",
           [({"status": r["status"]}, r["n"]) for r in rows] or [({"status": "ready"}, 0)])
    metric("local_repo_disk_bytes", "Disque des données.", "gauge",
           [({"kind": "total"}, disk["total"]), ({"kind": "free"}, disk["free"])])
    cert = cert_info(os.path.join(config.TLS_DIR, "server.crt"))
    if cert and cert.get("end", {}).get("days") is not None:
        metric("local_repo_tls_cert_expiry_days", "Jours avant expiration du certificat HTTPS.", "gauge",
               [({}, cert["end"]["days"])])
    backups = list_backups()
    metric("local_repo_backup_last_timestamp_seconds", "Date de la dernière sauvegarde (0 si aucune).", "gauge",
           [({}, int(backups[0]["mtime"]) if backups else 0)])
    return Response("\n".join(lines) + "\n", mimetype="text/plain; version=0.0.4")
