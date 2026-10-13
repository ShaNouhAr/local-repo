"""local-repo : portail web du dépôt local (paquets Linux, Windows Update, logiciels Windows)."""

import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

DATA = os.environ.get("LR_DATA", "/srv/local-repo")
STATE = os.environ.get("LR_STATE", "/var/lib/local-repo")
PUBLIC_URL = os.environ.get("LR_PUBLIC_URL", "http://localhost:8088").rstrip("/")
HOST = os.environ.get("LR_HOST", "localhost")
ACNG_PORT = os.environ.get("LR_ACNG_PORT", "3142")
WU_LOG = os.environ.get("LR_WU_LOG", "/var/log/nginx/local-repo-wu.log")
GNUPGHOME = os.environ.get("LR_GNUPGHOME", os.path.join(STATE, "gnupg"))
SECRETS_PATH = os.environ.get("LR_SECRETS", "/etc/local-repo/portal.secret")

APT_DIR = os.path.join(DATA, "apt")
POOL_DIR = os.path.join(APT_DIR, "pool")
WIN_DIR = os.path.join(DATA, "win")
TMP_DIR = os.path.join(DATA, ".tmp")
DB_PATH = os.path.join(STATE, "portal.db")
STATS_PATH = os.path.join(STATE, "stats.json")
WU_DOMAINS_PATH = os.path.join(STATE, "wu-domains.txt")
NGINX_MAP_DIR = os.path.join(STATE, "nginx")

DEFAULT_WU_DOMAINS = [
    "windowsupdate.com",
    "dl.delivery.mp.microsoft.com",
]

# Arguments silencieux proposés par défaut selon le type de fichier.
DEFAULT_ARGS = {"msi": "/qn /norestart", "msu": "/quiet /norestart", "exe": "", "ps1": "", "file": ""}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
USER_AGENT = "Mozilla/5.0 (local-repo)"



def load_secrets():
    try:
        with open(SECRETS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


SECRETS = load_secrets()

app = Flask(__name__)
app.secret_key = SECRETS.get("secret_key") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=7))

apt_lock = threading.Lock()


# --------------------------------------------------------------------------- utilitaires

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    os.makedirs(STATE, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS win_apps (
                id INTEGER PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'exe',
                args TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )""")
        # Un téléchargement interrompu par un redémarrage ne reprendra jamais.
        conn.execute("UPDATE win_apps SET status='error', error='Interrompu (redémarrage du service)' "
                     "WHERE status='downloading'")
    # Fichiers temporaires laissés par un arrêt brutal (téléchargements, envois).
    for root in (WIN_DIR, TMP_DIR, POOL_DIR):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.startswith((".dl-", ".up-")):
                    try:
                        os.unlink(os.path.join(dirpath, name))
                    except OSError:
                        pass


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


def date_fr(value):
    """« 2026-10-12 20:57 » -> « 12/10 20:57 »."""
    m = re.match(r"\d{4}-(\d{2})-(\d{2}) (\d{2}:\d{2})", value or "")
    return f"{m.group(2)}/{m.group(1)} {m.group(3)}" if m else (value or "")


def explain(exc):
    """Message d'erreur lisible pour un téléchargement raté."""
    import urllib.error
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


app.jinja_env.filters["human"] = human
app.jinja_env.filters["date_fr"] = date_fr
app.jinja_env.filters["hue"] = lambda text: int(hashlib.md5(text.encode()).hexdigest()[:4], 16) % 360

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
ASSET_V = str(int(max(os.path.getmtime(os.path.join(STATIC_DIR, f)) for f in ("style.css", "app.js"))))


def slugify(text):
    text = text.lower()
    text = (text.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
            .replace("ç", "c").replace("ô", "o").replace("î", "i").replace("ù", "u"))
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


def service_active(name):
    try:
        out = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def read_stats():
    try:
        with open(STATS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


PROGRESS = {}  # slug -> [octets reçus, taille totale]
CANCELLED = set()  # slugs supprimés pendant leur téléchargement


class Cancelled(Exception):
    pass


def download(url, dest_dir, progress_key=None):
    """Télécharge url dans dest_dir. Retourne (chemin, nom de fichier)."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("Le lien doit commencer par http:// ou https://.")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        name = ""
        disp = resp.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disp)
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


# --------------------------------------------------------------------------- authentification

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


@app.before_request
def csrf_protect():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST":
        token = request.form.get("csrf", "")
        if not hmac.compare_digest(token, session["csrf"]):
            abort(400, "Jeton CSRF invalide, rechargez la page.")


@app.context_processor
def inject_globals():
    nav = {}
    if session.get("auth"):
        nav = {"debs": len(list_debs()),
               "apps": db().execute("SELECT COUNT(*) FROM win_apps").fetchone()[0]}
    return {"csrf": session.get("csrf", ""), "public_url": PUBLIC_URL, "host": HOST,
            "acng_port": ACNG_PORT, "nav": nav, "asset_v": ASSET_V}


def safe_next(target):
    """Chemin local uniquement (pas de //hôte, /\\hôte ni schéma) : évite les redirections ouvertes."""
    target = target or "/"
    parsed = urllib.parse.urlparse(target.replace("\\", "/"))
    if (not target.startswith("/") or parsed.scheme or parsed.netloc or target.startswith("//")
            or re.search(r"[\x00-\x1f\x7f]", target)):
        return "/"
    return target


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pwd_hash = SECRETS.get("admin_hash", "")
        if pwd_hash and check_password_hash(pwd_hash, request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["auth"] = True
            return redirect(safe_next(request.args.get("next", "/")))
        time.sleep(1)  # freine les essais en rafale
        flash("Mot de passe incorrect.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    return "ok"


# --------------------------------------------------------------------------- tableau de bord

def dashboard_data():
    stats = read_stats()
    apps = db().execute("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM win_apps WHERE status='ready'").fetchone()
    debs = list_debs()
    disk = shutil.disk_usage(DATA)
    wu = wu_stats()
    return {
        "services": {"acng": service_active("apt-cacher-ng"), "nginx": service_active("nginx"),
                     "portal": True},
        "acng_size": stats.get("acng", 0),
        "wucache_size": stats.get("wucache", 0),
        "debs": len(debs),
        "debs_size": sum(d["size"] for d in debs),
        "apps": apps["n"],
        "apps_size": apps["s"],
        "wu": {k: wu[k] for k in ("hits", "misses", "hit_bytes", "miss_bytes", "ratio")},
        "disk": {"total": disk.total, "used": disk.total - disk.free, "free": disk.free,
                 "pct": round(100 * (disk.total - disk.free) / disk.total)},
        "stats_updated": stats.get("updated", ""),
    }


def recent_activity(limit=6):
    events = []
    for d in list_debs():
        events.append({"when": datetime.datetime.fromtimestamp(d["mtime"]).strftime("%Y-%m-%d %H:%M"),
                       "kind": "linux", "title": d["package"], "detail": f"{d['version']} · {d['arch']}"})
    for r in db().execute("SELECT name, version, kind, status, updated_at FROM win_apps"):
        events.append({"when": r["updated_at"], "kind": "windows", "title": r["name"],
                       "detail": " · ".join(x for x in (r["version"], r["kind"], STATUS_LABELS.get(r["status"], "")) if x)})
    events.sort(key=lambda e: e["when"], reverse=True)
    return events[:limit]


STATUS_LABELS = {"ready": "prêt", "downloading": "téléchargement", "error": "erreur", "pending": "en attente"}


@app.route("/")
@login_required
def index():
    return render_template("index.html", d=dashboard_data(), activity=recent_activity())


@app.route("/api/status")
@login_required
def api_status():
    return dashboard_data()


# --------------------------------------------------------------------------- paquets Linux

def list_debs():
    """Paquets du dépôt local, lus dans l'index Packages (versions exactes, epoch compris)."""
    debs = []
    try:
        with open(os.path.join(APT_DIR, "Packages")) as f:
            stanzas = f.read().split("\n\n")
    except OSError:
        return debs
    for stanza in stanzas:
        fields = dict(line.split(": ", 1) for line in stanza.splitlines() if ": " in line and not line.startswith(" "))
        if "Filename" not in fields:
            continue
        name = os.path.basename(fields["Filename"])
        path = os.path.join(POOL_DIR, name)
        if not os.path.isfile(path):
            continue
        debs.append({
            "file": name,
            "package": fields.get("Package", ""),
            "version": fields.get("Version", ""),
            "arch": fields.get("Architecture", ""),
            "size": os.path.getsize(path),
            "mtime": os.path.getmtime(path),
        })
    return sorted(debs, key=lambda d: (d["package"], d["version"]))


def deb_fields(path):
    out = subprocess.run(["dpkg-deb", "-f", path, "Package", "Version", "Architecture"],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise ValueError("ce fichier n'est pas un paquet .deb valide")
    fields = dict(line.split(": ", 1) for line in out.stdout.strip().splitlines() if ": " in line)
    if not all(k in fields for k in ("Package", "Version", "Architecture")):
        raise ValueError("champs Package/Version/Architecture manquants")
    return fields


def rebuild_apt():
    """Régénère l'index du dépôt APT local (dépôt « plat ») et le signe."""
    with apt_lock:
        os.makedirs(POOL_DIR, exist_ok=True)
        env = dict(os.environ, GNUPGHOME=GNUPGHOME)
        packages = subprocess.run(["apt-ftparchive", "packages", "pool"], cwd=APT_DIR,
                                  capture_output=True, check=True).stdout
        with open(os.path.join(APT_DIR, "Packages"), "wb") as f:
            f.write(packages)
        subprocess.run(["gzip", "-9nkf", "Packages"], cwd=APT_DIR, check=True)
        for old in ("Release", "InRelease", "Release.gpg"):
            try:
                os.unlink(os.path.join(APT_DIR, old))
            except FileNotFoundError:
                pass
        release = subprocess.run(
            ["apt-ftparchive",
             "-o", "APT::FTPArchive::Release::Origin=local-repo",
             "-o", "APT::FTPArchive::Release::Label=local-repo",
             "-o", "APT::FTPArchive::Release::Suite=stable",
             "-o", "APT::FTPArchive::Release::Codename=local",
             "release", "."], cwd=APT_DIR, capture_output=True, check=True).stdout
        with open(os.path.join(APT_DIR, "Release"), "wb") as f:
            f.write(release)
        subprocess.run(["gpg", "--batch", "--yes", "--clearsign", "-o", "InRelease", "Release"],
                       cwd=APT_DIR, env=env, check=True, capture_output=True)
        subprocess.run(["gpg", "--batch", "--yes", "-abs", "-o", "Release.gpg", "Release"],
                       cwd=APT_DIR, env=env, check=True, capture_output=True)


def add_deb(tmp_path):
    fields = deb_fields(tmp_path)
    # Convention Debian : l'epoch (« 1: ») n'apparaît pas dans le nom du fichier.
    version = fields["Version"].split(":", 1)[-1]
    name = secure_filename(f"{fields['Package']}_{version}_{fields['Architecture']}.deb")
    os.chmod(tmp_path, 0o644)
    os.replace(tmp_path, os.path.join(POOL_DIR, name))
    rebuild_apt()
    return name


@app.route("/linux")
@login_required
def linux():
    stats = read_stats()
    return render_template("linux.html", debs=list_debs(), stats=stats,
                           acng=service_active("apt-cacher-ng"))


@app.route("/linux/upload", methods=["POST"])
@login_required
def linux_upload():
    os.makedirs(TMP_DIR, exist_ok=True)
    added, errors = [], []
    url = request.form.get("url", "").strip()
    try:
        if url:
            tmp, _ = download(url, TMP_DIR)
            try:
                added.append(add_deb(tmp))
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        for f in request.files.getlist("files"):
            if not f.filename:
                continue
            fd, tmp = tempfile.mkstemp(dir=TMP_DIR, prefix=".up-")
            os.close(fd)
            try:
                f.save(tmp)
                added.append(add_deb(tmp))
            except (ValueError, subprocess.SubprocessError) as e:
                errors.append(f"{f.filename} : {e}")
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except Exception as e:  # noqa: BLE001 - on remonte toute erreur à l'utilisateur
        errors.append(explain(e))
    for name in added:
        flash(f"Paquet ajouté : {name}", "ok")
    for err in errors:
        flash(err, "error")
    if not added and not errors:
        flash("Aucun fichier ni URL fourni.", "error")
    return back("linux")


@app.route("/linux/delete", methods=["POST"])
@login_required
def linux_delete():
    name = secure_filename(request.form.get("file", ""))
    path = os.path.join(POOL_DIR, name)
    if name.endswith(".deb") and os.path.isfile(path):
        os.unlink(path)
        rebuild_apt()
        flash(f"Paquet supprimé : {name}", "ok")
    return back("linux")


@app.route("/linux/setup.sh")
def linux_setup():
    script = render_template("setup.sh", public_url=PUBLIC_URL, host=HOST, acng_port=ACNG_PORT)
    return Response(script, mimetype="text/x-shellscript")


# --------------------------------------------------------------------------- Windows Update

def read_wu_domains():
    try:
        with open(WU_DOMAINS_PATH) as f:
            domains = [d.strip() for d in f if d.strip() and not d.startswith("#")]
            return domains or DEFAULT_WU_DOMAINS
    except OSError:
        return DEFAULT_WU_DOMAINS


LOG_RE = re.compile(r'^(\S+) \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d+) (\d+) (\S+) "([^"]*)"$')


_WU_CACHE = {}


def wu_stats(max_bytes=20 * 1024 * 1024, recent=0):
    """Lit la fin du journal du cache Windows Update : compteurs HIT/MISS et dernières requêtes."""
    try:
        st = os.stat(WU_LOG)
        key = (st.st_ino, st.st_size, st.st_mtime_ns, recent)
    except OSError:
        key = None
    if key and _WU_CACHE.get("key") == key:
        return json.loads(_WU_CACHE["value"])
    res = _wu_stats(max_bytes, recent)
    if key:
        _WU_CACHE.update(key=key, value=json.dumps(res))
    return res


def _wu_stats(max_bytes, recent):
    res = {"hit_bytes": 0, "miss_bytes": 0, "hits": 0, "misses": 0, "ok": False, "recent": []}
    try:
        with open(WU_LOG, "rb") as f:
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


def write_wu_maps(domains=None):
    """Écrit la liste des domaines autorisés pour nginx (le service local-repo-nginx recharge nginx)."""
    domains = domains or read_wu_domains()
    os.makedirs(NGINX_MAP_DIR, exist_ok=True)
    for name, value in (("wu-stream.map", "$ssl_preread_server_name:443"), ("wu-http.map", "1")):
        body = "".join(f".{d} {value};\n" for d in domains if DOMAIN_RE.match(d))
        tmp = os.path.join(NGINX_MAP_DIR, f".{name}.tmp")
        with open(tmp, "w") as f:
            f.write("# Généré par le portail local-repo, ne pas modifier.\n" + body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(NGINX_MAP_DIR, name))


@app.route("/windows-update/domains", methods=["POST"])
@login_required
def windows_update_domains():
    domains, bad = [], []
    for d in re.split(r"[\s,]+", request.form.get("domains", "").lower()):
        d = d.strip().lstrip("*.").rstrip(".")
        if not d:
            continue
        (domains if DOMAIN_RE.match(d) else bad).append(d)
    if bad:
        flash("Domaines ignorés (invalides) : " + ", ".join(bad), "error")
    if domains:
        domains = list(dict.fromkeys(domains))
        with open(WU_DOMAINS_PATH, "w") as f:
            f.write("\n".join(domains) + "\n")
        write_wu_maps(domains)
        flash("Domaines enregistrés et appliqués au cache.", "ok")
    return back("windows_update")


@app.route("/windows-update/dns.ps1")
@login_required
def windows_update_dns():
    script = render_template("dns.ps1", domains=read_wu_domains(), host=HOST)
    return Response(script.replace("\n", "\r\n"), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=local-repo-dns.ps1"})


# --------------------------------------------------------------------------- logiciels Windows

def app_dir(slug):
    return os.path.join(WIN_DIR, slug)


def store_file(row_id, slug, tmp, filename):
    """Place le fichier téléchargé/envoyé dans le catalogue et met à jour la fiche."""
    dest_dir = app_dir(slug)
    os.makedirs(dest_dir, exist_ok=True)
    digest = sha256_of(tmp)
    size = os.path.getsize(tmp)
    os.chmod(tmp, 0o644)
    dest = os.path.join(dest_dir, filename)
    os.replace(tmp, dest)
    for old in os.listdir(dest_dir):
        if old != filename and not old.startswith("."):
            os.unlink(os.path.join(dest_dir, old))
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("UPDATE win_apps SET filename=?, size=?, sha256=?, status='ready', error='', updated_at=? "
                     "WHERE id=?", (filename, size, digest, now(), row_id))
        # Type choisi « automatique » : on le déduit du vrai nom de fichier, avec ses arguments par défaut.
        row = conn.execute("SELECT kind, args FROM win_apps WHERE id=?", (row_id,)).fetchone()
        if row and row[0] == "auto":
            kind = kind_for(filename)
            conn.execute("UPDATE win_apps SET kind=?, args=? WHERE id=?",
                         (kind, row[1] or DEFAULT_ARGS[kind], row_id))


def fetch_app(row_id, slug, url):
    """Téléchargement en arrière-plan d'un logiciel depuis son URL."""
    tmp = None
    try:
        os.makedirs(app_dir(slug), exist_ok=True)
        try:
            tmp, filename = download(url, app_dir(slug), progress_key=slug)
        except Cancelled:
            shutil.rmtree(app_dir(slug), ignore_errors=True)
            return
        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            still_there = conn.execute("SELECT 1 FROM win_apps WHERE id=?", (row_id,)).fetchone()
        if not still_there:  # supprimé pendant le téléchargement
            os.unlink(tmp)
            tmp = None
            shutil.rmtree(app_dir(slug), ignore_errors=True)
            return
        store_file(row_id, slug, tmp, filename)
        tmp = None
    except Exception as e:  # noqa: BLE001
        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            conn.execute("UPDATE win_apps SET status='error', error=?, updated_at=? WHERE id=?",
                         (explain(e), now(), row_id))
    finally:
        PROGRESS.pop(slug, None)
        CANCELLED.discard(slug)
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def start_fetch(row):
    db().execute("UPDATE win_apps SET status='downloading', error='' WHERE id=?", (row["id"],))
    db().commit()
    threading.Thread(target=fetch_app, args=(row["id"], row["slug"], row["source_url"]), daemon=True).start()


@app.route("/windows-apps")
@login_required
def windows_apps():
    rows = db().execute("SELECT * FROM win_apps ORDER BY name COLLATE NOCASE").fetchall()
    busy = any(r["status"] == "downloading" for r in rows)
    return render_template("windows_apps.html", apps=rows, busy=busy, default_args=DEFAULT_ARGS)


@app.route("/api/apps")
@login_required
def api_apps():
    out = []
    for r in db().execute("SELECT * FROM win_apps ORDER BY name COLLATE NOCASE"):
        item = {k: r[k] for k in ("slug", "name", "version", "kind", "size", "status", "error", "updated_at")}
        done, total = PROGRESS.get(r["slug"], (0, 0))
        item["progress"] = {"done": done, "total": total}
        out.append(item)
    return {"apps": out}


@app.route("/windows-apps/add", methods=["POST"])
@login_required
def windows_apps_add():
    name = request.form.get("name", "").strip()
    slug = slugify(request.form.get("slug", "").strip() or name)
    url = request.form.get("url", "").strip()
    upload = request.files.get("file")
    has_upload = bool(upload and upload.filename)
    if not name or not SLUG_RE.match(slug):
        flash("Nom invalide.", "error")
        return back("windows_apps")
    if not url and not has_upload:
        flash("Indiquez une URL ou envoyez un fichier.", "error")
        return back("windows_apps")
    if url and not url.startswith(("http://", "https://")):
        flash("L'URL doit commencer par http:// ou https://.", "error")
        return back("windows_apps")
    if db().execute("SELECT 1 FROM win_apps WHERE slug=?", (slug,)).fetchone():
        flash(f"L'identifiant « {slug} » existe déjà.", "error")
        return back("windows_apps")

    filename = secure_filename(upload.filename) if has_upload else ""
    kind = request.form.get("kind") or "auto"
    if kind not in DEFAULT_ARGS and kind != "auto":
        kind = "auto"
    args = request.form.get("args", "").strip() or DEFAULT_ARGS.get(kind, "")
    cur = db().execute(
        "INSERT INTO win_apps (slug, name, version, kind, args, source_url, status, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (slug, name, request.form.get("version", "").strip(), kind, args, url, "pending", now()))
    db().commit()
    row = db().execute("SELECT * FROM win_apps WHERE id=?", (cur.lastrowid,)).fetchone()

    if has_upload:
        os.makedirs(app_dir(slug), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=app_dir(slug), prefix=".up-")
        os.close(fd)
        try:
            upload.save(tmp)
            store_file(row["id"], slug, tmp, filename or "installer.bin")
            flash(f"{name} ajouté au catalogue.", "ok")
        except OSError as e:
            db().execute("UPDATE win_apps SET status='error', error=? WHERE id=?", (f"Envoi impossible : {e}", row["id"]))
            db().commit()
            flash(f"Envoi de {name} impossible : {e}", "error")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    else:
        start_fetch(row)
        flash(f"Téléchargement de {name} lancé.", "ok")
    return back("windows_apps")


def get_app(slug):
    row = db().execute("SELECT * FROM win_apps WHERE slug=?", (slug,)).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/windows-apps/<slug>/edit", methods=["POST"])
@login_required
def windows_apps_edit(slug):
    row = get_app(slug)
    kind = request.form.get("kind", row["kind"])
    if kind not in DEFAULT_ARGS and kind != "auto":
        kind = row["kind"]
    url = request.form.get("url", "").strip()
    if url and not url.startswith(("http://", "https://")):
        flash("L'URL doit commencer par http:// ou https://.", "error")
        return back("windows_apps")
    db().execute("UPDATE win_apps SET name=?, version=?, kind=?, args=?, source_url=?, updated_at=? WHERE id=?",
                 (request.form.get("name", row["name"]).strip() or row["name"],
                  request.form.get("version", "").strip(), kind,
                  request.form.get("args", "").strip(), url, now(), row["id"]))
    db().commit()
    upload = request.files.get("file")
    if upload and upload.filename:
        os.makedirs(app_dir(slug), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=app_dir(slug), prefix=".up-")
        os.close(fd)
        try:
            upload.save(tmp)
            store_file(row["id"], slug, tmp, secure_filename(upload.filename) or "installer.bin")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    flash(f"{row['name']} mis à jour.", "ok")
    return back("windows_apps")


@app.route("/windows-apps/<slug>/refresh", methods=["POST"])
@login_required
def windows_apps_refresh(slug):
    row = get_app(slug)
    if not row["source_url"]:
        flash("Pas d'URL source : renvoyez le fichier depuis « Modifier ».", "error")
    elif row["status"] != "downloading":
        start_fetch(row)
        flash(f"Nouveau téléchargement de {row['name']} lancé.", "ok")
    return back("windows_apps")


@app.route("/windows-apps/<slug>/delete", methods=["POST"])
@login_required
def windows_apps_delete(slug):
    row = get_app(slug)
    if row["status"] == "downloading":
        CANCELLED.add(slug)
    shutil.rmtree(app_dir(slug), ignore_errors=True)
    db().execute("DELETE FROM win_apps WHERE id=?", (row["id"],))
    db().commit()
    flash(f"{row['name']} supprimé.", "ok")
    return back("windows_apps")


@app.route("/win/i/<slugs>")
def windows_install_script(slugs):
    """Script PowerShell d'installation : irm 'http://hôte:port/win/i/app1,app2' | iex"""
    wanted = [s for s in slugs.lower().split(",") if s]
    if not wanted or not all(SLUG_RE.match(s) for s in wanted):
        abort(404)
    marks = ",".join("?" * len(wanted))
    rows = {r["slug"]: r for r in db().execute(
        f"SELECT * FROM win_apps WHERE status='ready' AND slug IN ({marks})", wanted)}
    missing = [s for s in wanted if s not in rows]
    apps = [rows[s] for s in wanted if s in rows]
    script = render_template("install.ps1", apps=apps, missing=missing, base=PUBLIC_URL,
                             q=lambda s: "'" + (s or "").replace("'", "''") + "'",
                             quote=urllib.parse.quote)
    return Response(script.replace("\n", "\r\n"), mimetype="text/plain")


# --------------------------------------------------------------------------- point d'entrée

init_db()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "rebuild-apt":
        rebuild_apt()
        print("Index APT régénéré.")
    elif len(sys.argv) > 1 and sys.argv[1] == "write-maps":
        write_wu_maps()
    else:
        app.run(host="127.0.0.1", port=8089, debug=True)
