"""Fonctions utilitaires sans état."""

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from flask import redirect, request, url_for
from werkzeug.utils import secure_filename

from . import config


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def human(n):
    n = float(n or 0)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            if unit == "o":
                return f"{n:.0f} {unit}"
            return (f"{n:.2f}" if n < 10 else f"{n:.1f}").replace(".", ",") + f" {unit}"
        n /= 1024
    return ""


def date_fr(value):
    """« 2026-10-12 20:57 » -> « 12/10 20:57 »."""
    m = re.match(r"\d{4}-(\d{2})-(\d{2}) (\d{2}:\d{2})", value or "")
    return f"{m.group(2)}/{m.group(1)} {m.group(3)}" if m else (value or "")


def explain(exc):
    """Message d'erreur lisible pour un téléchargement raté."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"Le serveur a répondu HTTP {exc.code} ({exc.reason})."
    if isinstance(exc, urllib.error.URLError):
        reason = str(exc.reason)
        if "Name or service not known" in reason or "Temporary failure in name resolution" in reason:
            return "Adresse introuvable : vérifiez le lien."
        if "timed out" in reason:
            return "Le serveur n'a pas répondu à temps."
        if "CERTIFICATE" in reason.upper():
            return "Certificat HTTPS invalide sur le site source."
        return f"Connexion impossible : {reason}"
    if isinstance(exc, TimeoutError):
        return "Le serveur n'a pas répondu à temps."
    return str(exc)[:300] or exc.__class__.__name__


def slugify(text):
    text = text.lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"), ("à", "a"), ("â", "a"), ("ç", "c"),
                 ("ô", "o"), ("î", "i"), ("ï", "i"), ("ù", "u"), ("û", "u")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:63]


def kind_for(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext if ext in ("exe", "msi", "msu", "ps1") else "file"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Conteneur : processus lancés par supervisord, reconnus à leur ligne de commande.
CONTAINER_PROCESSES = {"nginx": "nginx: master", "apt-cacher-ng": "/usr/sbin/apt-cacher-ng",
                       "local-repo-cron": "local-repo-cron"}


def process_running(marker):
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if marker in cmdline:
            return True
    return False


def service_active(name):
    if config.CONTAINER:
        if name == "local-repo-portal":
            return True
        return name in CONTAINER_PROCESSES and process_running(CONTAINER_PROCESSES[name])
    try:
        out = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def read_stats():
    try:
        with open(config.STATS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def disk_usage():
    d = shutil.disk_usage(config.DATA)
    return {"total": d.total, "used": d.total - d.free, "free": d.free,
            "pct": round(100 * (d.total - d.free) / d.total) if d.total else 0}


class Cancelled(Exception):
    """Téléchargement interrompu (logiciel supprimé entre-temps)."""


PROGRESS = {}      # clé -> [octets reçus, taille totale]
CANCELLED = set()  # clés dont le téléchargement doit s'arrêter


def download(url, dest_dir, progress_key=None):
    """Télécharge url dans dest_dir. Retourne (chemin temporaire, nom de fichier)."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("Le lien doit commencer par http:// ou https://.")
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        name = ""
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", resp.headers.get("Content-Disposition", ""))
        if m:
            name = urllib.parse.unquote(m.group(1))
        if not name:
            name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(resp.geturl()).path))
        name = secure_filename(name) or "download.bin"
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".dl-")
        try:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    if progress_key in CANCELLED:
                        raise Cancelled()
                    out.write(chunk)
                    done += len(chunk)
                    if progress_key:
                        PROGRESS[progress_key] = [done, total]
        except BaseException:
            os.unlink(tmp)
            raise
    return tmp, name


def back(endpoint, **values):
    """Retour après un formulaire : redirection, ou JSON si la page a envoyé le formulaire elle-même."""
    url = url_for(endpoint, **values)
    if request.headers.get("X-Requested-With") == "fetch":
        return {"redirect": url}
    return redirect(url)


def safe_next(target):
    """Chemin local uniquement (pas de //hôte, /\\hôte, schéma ni caractère de contrôle)."""
    target = target or "/"
    parsed = urllib.parse.urlparse(target.replace("\\", "/"))
    if (not target.startswith("/") or parsed.scheme or parsed.netloc or target.startswith("//")
            or re.search(r"[\x00-\x1f\x7f]", target)):
        return "/"
    return target


def cleanup_temp_files():
    """Supprime les fichiers temporaires laissés par un arrêt brutal (téléchargements, envois)."""
    for root in (config.WIN_DIR, config.TMP_DIR, config.POOL_DIR):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.startswith((".dl-", ".up-")):
                    try:
                        os.unlink(os.path.join(dirpath, name))
                    except OSError:
                        pass
