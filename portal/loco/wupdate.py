"""Windows Update : statistiques du cache, domaines redirigés, script DNS."""

import json
import os
import re
import urllib.parse

from flask import Response, flash, render_template, request

from . import app, config
from .auth import admin_required, audit, login_required
from .util import back, read_stats, service_active

LOG_RE = re.compile(r'^(\S+) \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d+) (\d+) (\S+) "([^"]*)"$')
_CACHE = {}


def read_wu_domains():
    try:
        with open(config.WU_DOMAINS_PATH) as f:
            domains = [d.strip() for d in f if d.strip() and not d.startswith("#")]
            return domains or config.DEFAULT_WU_DOMAINS
    except OSError:
        return config.DEFAULT_WU_DOMAINS


def wu_stats(max_bytes=20 * 1024 * 1024, recent=0):
    """Fin du journal du cache : compteurs HIT/MISS et dernières requêtes (mis en cache tant que le fichier ne change pas)."""
    try:
        st = os.stat(config.WU_LOG)
        key = (st.st_ino, st.st_size, st.st_mtime_ns, recent)
    except OSError:
        key = None
    if key and _CACHE.get("key") == key:
        return json.loads(_CACHE["value"])
    res = _parse_log(max_bytes, recent)
    if key:
        _CACHE.update(key=key, value=json.dumps(res))
    return res


def _parse_log(max_bytes, recent):
    res = {"hit_bytes": 0, "miss_bytes": 0, "hits": 0, "misses": 0, "ok": False, "recent": []}
    try:
        with open(config.WU_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
        res["ok"] = True
    except OSError:
        lines = []
    entries = []
    for line in lines:
        # format : ip [date] "requête" statut octets cache "hôte"
        m = LOG_RE.match(line)
        if not m:
            continue
        ip, date, _method, path, status, size, cache, host = m.groups()
        size = int(size)
        if cache == "HIT":
            res["hits"] += 1
            res["hit_bytes"] += size
        elif cache in ("MISS", "EXPIRED", "UPDATING", "STALE", "BYPASS"):
            res["misses"] += 1
            res["miss_bytes"] += size
        if recent:
            entries.append((ip, date, path, status, size, cache, host))
    total = res["hit_bytes"] + res["miss_bytes"]
    res["ratio"] = round(100 * res["hit_bytes"] / total) if total else 0
    for ip, date, path, status, size, cache, host in reversed(entries[-recent:] if recent else []):
        name = urllib.parse.unquote(path.split("?")[0].rsplit("/", 1)[-1]) or "/"
        res["recent"].append({"client": ip, "time": date.split(":", 1)[-1].split(" ")[0], "host": host,
                              "file": name, "status": status, "size": size, "cache": cache})
    return res


def write_wu_maps(domains=None):
    """Écrit la liste des domaines autorisés pour nginx (loco-nginx.path recharge nginx)."""
    domains = domains or read_wu_domains()
    os.makedirs(config.NGINX_MAP_DIR, exist_ok=True)
    for name, value in (("wu-stream.map", "$ssl_preread_server_name:443"), ("wu-http.map", "1")):
        body = "".join(f".{d} {value};\n" for d in domains if config.DOMAIN_RE.match(d))
        tmp = os.path.join(config.NGINX_MAP_DIR, f".{name}.tmp")
        with open(tmp, "w") as f:
            f.write("# Généré par le portail loco, ne pas modifier.\n" + body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(config.NGINX_MAP_DIR, name))


@app.route("/windows-update")
@login_required
def windows_update():
    return render_template("windows_update.html", domains=read_wu_domains(), wu=wu_stats(recent=12),
                           stats=read_stats(), nginx=service_active("nginx"))


@app.route("/api/wu")
@login_required
def api_wu():
    wu = wu_stats(recent=12)
    wu["cache_size"] = read_stats().get("wucache", 0)
    return wu


@app.route("/windows-update/domains", methods=["POST"])
@admin_required
def windows_update_domains():
    domains, bad = [], []
    for d in re.split(r"[\s,]+", request.form.get("domains", "").lower()):
        d = d.strip().lstrip("*.").rstrip(".")
        if d:
            (domains if config.DOMAIN_RE.match(d) else bad).append(d)
    if bad:
        flash("Domaines ignorés (invalides) : " + ", ".join(bad), "error")
    if domains:
        domains = list(dict.fromkeys(domains))
        before = read_wu_domains()
        with open(config.WU_DOMAINS_PATH, "w") as f:
            f.write("\n".join(domains) + "\n")
        write_wu_maps(domains)
        if domains != before:
            audit("windows_update.domaines", ", ".join(domains))
        flash("Domaines enregistrés et appliqués au cache.", "ok")
    return back("windows_update")


@app.route("/windows-update/dns.ps1")
@login_required
def windows_update_dns():
    script = render_template("dns.ps1", domains=read_wu_domains(), host=config.HOST)
    return Response(script.replace("\n", "\r\n"), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=loco-dns.ps1"})
