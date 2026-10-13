"""Paquets Linux : cache apt-cacher-ng et dépôt APT local signé."""

import os
import subprocess
import tempfile
import threading

from flask import flash, render_template, request
from werkzeug.utils import secure_filename

from . import app, config
from .auth import admin_required, audit, login_required
from .util import back, download, explain, read_stats, service_active

_apt_lock = threading.Lock()


def list_debs():
    """Paquets du dépôt local, lus dans l'index Packages (versions exactes, epoch compris)."""
    debs = []
    try:
        with open(os.path.join(config.APT_DIR, "Packages")) as f:
            stanzas = f.read().split("\n\n")
    except OSError:
        return debs
    for stanza in stanzas:
        fields = dict(line.split(": ", 1) for line in stanza.splitlines() if ": " in line and not line.startswith(" "))
        if "Filename" not in fields:
            continue
        name = os.path.basename(fields["Filename"])
        path = os.path.join(config.POOL_DIR, name)
        if not os.path.isfile(path):
            continue
        debs.append({"file": name, "package": fields.get("Package", ""), "version": fields.get("Version", ""),
                     "arch": fields.get("Architecture", ""), "size": os.path.getsize(path),
                     "mtime": os.path.getmtime(path)})
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
    with _apt_lock:
        os.makedirs(config.POOL_DIR, exist_ok=True)
        env = dict(os.environ, GNUPGHOME=config.GNUPGHOME)
        packages = subprocess.run(["apt-ftparchive", "packages", "pool"], cwd=config.APT_DIR,
                                  capture_output=True, check=True).stdout
        with open(os.path.join(config.APT_DIR, "Packages"), "wb") as f:
            f.write(packages)
        subprocess.run(["gzip", "-9nkf", "Packages"], cwd=config.APT_DIR, check=True)
        for old in ("Release", "InRelease", "Release.gpg"):
            try:
                os.unlink(os.path.join(config.APT_DIR, old))
            except FileNotFoundError:
                pass
        release = subprocess.run(
            ["apt-ftparchive",
             "-o", "APT::FTPArchive::Release::Origin=local-repo",
             "-o", "APT::FTPArchive::Release::Label=local-repo",
             "-o", "APT::FTPArchive::Release::Suite=stable",
             "-o", "APT::FTPArchive::Release::Codename=local",
             "release", "."], cwd=config.APT_DIR, capture_output=True, check=True).stdout
        with open(os.path.join(config.APT_DIR, "Release"), "wb") as f:
            f.write(release)
        subprocess.run(["gpg", "--batch", "--yes", "--clearsign", "-o", "InRelease", "Release"],
                       cwd=config.APT_DIR, env=env, check=True, capture_output=True)
        subprocess.run(["gpg", "--batch", "--yes", "-abs", "-o", "Release.gpg", "Release"],
                       cwd=config.APT_DIR, env=env, check=True, capture_output=True)


def add_deb(tmp_path):
    fields = deb_fields(tmp_path)
    # Convention Debian : l'epoch (« 1: ») n'apparaît pas dans le nom du fichier.
    version = fields["Version"].split(":", 1)[-1]
    name = secure_filename(f"{fields['Package']}_{version}_{fields['Architecture']}.deb")
    os.chmod(tmp_path, 0o644)
    os.replace(tmp_path, os.path.join(config.POOL_DIR, name))
    rebuild_apt()
    return name


@app.route("/linux")
@login_required
def linux():
    return render_template("linux.html", debs=list_debs(), stats=read_stats(),
                           acng=service_active("apt-cacher-ng"))


@app.route("/linux/upload", methods=["POST"])
@admin_required
def linux_upload():
    os.makedirs(config.TMP_DIR, exist_ok=True)
    added, errors = [], []
    url = request.form.get("url", "").strip()
    try:
        if url:
            tmp, _ = download(url, config.TMP_DIR)
            try:
                added.append(add_deb(tmp))
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        for f in request.files.getlist("files"):
            if not f.filename:
                continue
            fd, tmp = tempfile.mkstemp(dir=config.TMP_DIR, prefix=".up-")
            os.close(fd)
            try:
                f.save(tmp)
                added.append(add_deb(tmp))
            except (ValueError, subprocess.SubprocessError) as e:
                errors.append(f"{f.filename} : {e}")
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except Exception as e:  # noqa: BLE001 - toute erreur est remontée à l'utilisateur
        errors.append(explain(e))
    for name in added:
        audit("linux.ajout", name)
        flash(f"Paquet ajouté : {name}", "ok")
    for err in errors:
        flash(err, "error")
    if not added and not errors:
        flash("Aucun fichier ni lien fourni.", "error")
    return back("linux")


@app.route("/linux/delete", methods=["POST"])
@admin_required
def linux_delete():
    name = secure_filename(request.form.get("file", ""))
    path = os.path.join(config.POOL_DIR, name)
    if name.endswith(".deb") and os.path.isfile(path):
        os.unlink(path)
        rebuild_apt()
        audit("linux.suppression", name)
        flash(f"Paquet supprimé : {name}", "ok")
    return back("linux")
