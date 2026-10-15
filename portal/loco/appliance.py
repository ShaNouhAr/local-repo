"""Appliance Alpine : mises à jour (loco, système) et réglages, exécutés par l'agent root (locoadmin)."""

import ipaddress
import socket
import subprocess

from flask import abort, flash, g, render_template, request

from . import app, config
from .auth import admin_required, audit
from .system import _alpine_release
from .util import back

if config.APPLIANCE:
    import locoadmin

ACTION_LABELS = {"check": "Vérification des versions", "update-loco": "Mise à jour de loco",
                 "update-system": "Mise à jour du système", "configure": "Réglages", "reboot": "Redémarrage"}
SWITCHES = ("HTTP_ENABLED", "WU_ENABLED", "RPM_ENABLED", "FLATPAK_ENABLED")


def _require_appliance():
    if not config.APPLIANCE:
        abort(404)


def _submit(action, params=None):
    running = locoadmin.running_job()
    if running:
        flash(f"Opération en cours : {ACTION_LABELS.get(running['action'], running['action'])}. Réessayez à la fin.", "error")
        return False
    if not locoadmin.agent_running():
        flash("L'agent d'administration ne répond pas : sur la console, rc-service loco-agent restart.", "error")
        return False
    try:
        locoadmin.request(action, params, user=_username())
    except ValueError as e:
        flash(str(e)[:1].upper() + str(e)[1:] + ".", "error")
        return False
    return True


def _username():
    return g.user["username"] if g.get("user") else ""


def _network():
    """État du réseau, en lecture seule (modifié sur la console avec setup-loco)."""
    info = {"hostname": socket.gethostname(), "addresses": [], "gateway": "", "dns": [], "mode": ""}
    try:
        out = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, timeout=5).stdout
        info["addresses"] = [f"{f[3]} ({f[1]})" for f in (line.split() for line in out.splitlines()) if len(f) > 3]
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
        info["gateway"] = out.split()[2] if out.startswith("default via") else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    try:
        with open("/etc/resolv.conf") as f:
            info["dns"] = [line.split()[1] for line in f if line.startswith("nameserver") and len(line.split()) > 1]
    except OSError:
        pass
    try:
        with open("/etc/network/interfaces") as f:
            text = f.read()
        info["mode"] = "DHCP" if " dhcp" in text else ("adresse fixe" if " static" in text else "")
    except OSError:
        pass
    return info


@app.route("/systeme/mises-a-jour")
@admin_required
def appliance_updates():
    _require_appliance()
    status = locoadmin.read_status()
    latest = status.get("latest") or {}
    jobs = locoadmin.jobs(limit=8)
    selected = request.args.get("operation", "")
    job = next((j for j in jobs if j["id"] == selected), None) or (jobs[0] if jobs else None)
    return render_template(
        "appliance_updates.html", status=status, latest=latest, jobs=jobs, job=job,
        signature=f"{jobs[0]['id']}:{jobs[0]['state']}" if jobs else "",
        job_log=locoadmin.job_log(job["id"]) if job else "", labels=ACTION_LABELS,
        update_available=bool(latest) and locoadmin.version_key(latest["version"]) > locoadmin.version_key(config.VERSION),
        running=locoadmin.running_job(), agent=locoadmin.agent_running(), alpine_release=_alpine_release(),
    )


@app.route("/systeme/mises-a-jour/operation/<job_id>")
@admin_required
def appliance_job(job_id):
    _require_appliance()
    job = next((j for j in locoadmin.jobs(limit=40) if j["id"] == job_id), None)
    if not job:
        abort(404)
    return {"state": job.get("state", ""), "log": locoadmin.job_log(job_id)}


@app.route("/systeme/operations/etat")
@admin_required
def appliance_operations_state():
    _require_appliance()
    latest = locoadmin.jobs(limit=1)
    signature = f"{latest[0]['id']}:{latest[0]['state']}" if latest else ""
    return {"signature": signature}


@app.route("/systeme/mises-a-jour", methods=["POST"])
@admin_required
def appliance_updates_post():
    _require_appliance()
    action = request.form.get("action", "")
    if action not in ("check", "update-loco", "update-system", "reboot"):
        abort(400)
    if _submit(action):
        audit(f"systeme.{action}", ACTION_LABELS[action])
        flash(f"{ACTION_LABELS[action]} lancée." if action != "reboot" else "Redémarrage demandé.", "ok")
    return back("appliance_updates")


@app.route("/systeme/configuration")
@admin_required
def appliance_settings():
    _require_appliance()
    conf = locoadmin.read_conf()
    return render_template("appliance_settings.html", conf=conf, settings=locoadmin.SETTINGS, switches=SWITCHES,
                           network=_network(), running=locoadmin.running_job(), agent=locoadmin.agent_running(),
                           client_ip=request.remote_addr or "")


@app.route("/systeme/configuration", methods=["POST"])
@admin_required
def appliance_settings_post():
    _require_appliance()
    changes = {}
    for key in locoadmin.SETTINGS:
        if key in SWITCHES:
            changes[key] = "1" if request.form.get(key) == "1" else "0"
        elif key in request.form:
            changes[key] = request.form.get(key, "")
    try:
        clean = locoadmin.validate(changes)
    except ValueError as e:
        flash(str(e) + ".", "error")
        return back("appliance_settings")
    if not clean:
        flash("Aucun changement.", "ok")
        return back("appliance_settings")
    # Ne pas se couper soi-même l'accès à l'interface.
    if "ADMIN_ALLOW" in clean and not _allowed(request.remote_addr, clean["ADMIN_ALLOW"]):
        flash(f"Les réseaux de l'interface doivent inclure votre adresse ({request.remote_addr}).", "error")
        return back("appliance_settings")
    if _submit("configure", clean):
        audit("systeme.configuration", ", ".join(f"{k}={v}" for k, v in clean.items()))
        flash("Réglages en cours d'application : les services redémarrent quelques secondes.", "ok")
        return back("appliance_updates")
    return back("appliance_settings")


def _allowed(ip, networks):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    addr = getattr(addr, "ipv4_mapped", None) or addr
    if addr.is_loopback:
        return True
    return any(addr in ipaddress.ip_network(n, strict=False) for n in networks.split())

