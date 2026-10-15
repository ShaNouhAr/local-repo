"""Comptes, sessions, limitation des tentatives de connexion et journal d'audit."""

import datetime
import secrets
import time
from functools import wraps

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import app, config
from .db import db
from .util import back, safe_next

# Hachage factice : la vérification prend le même temps, que le compte existe ou non.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


# --------------------------------------------------------------------------- audit

def audit(action, detail="", username=None):
    """Trace une action dans la base (page Journal) et dans journald."""
    user = username if username is not None else (g.user["username"] if g.get("user") else "")
    ip = request.remote_addr or ""
    db().execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?,?,?,?,?)",
                 (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user, ip, action, detail[:500]))
    db().commit()
    current_app.logger.info("audit user=%s ip=%s action=%s %s", user or "-", ip, action, detail)


# --------------------------------------------------------------------------- comptes

def generate_password():
    """Mot de passe temporaire lisible (sans caractères ambigus)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))


def password_problem(password, username=""):
    if len(password) < config.MIN_PASSWORD:
        return f"Le mot de passe doit faire au moins {config.MIN_PASSWORD} caractères."
    if username and username.lower() in password.lower():
        return "Le mot de passe ne doit pas contenir le nom du compte."
    if len(set(password)) < 5:
        return "Le mot de passe est trop répétitif."
    return None


def create_user(conn, username, password=None, role="admin", must_change=True, password_hash=None):
    conn.execute(
        "INSERT INTO users (username, password_hash, role, must_change, created_at) VALUES (?,?,?,?,?)",
        (username.lower(), password_hash or generate_password_hash(password), role, int(must_change),
         datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))


def set_password(conn, user_id, password, must_change=False):
    conn.execute("UPDATE users SET password_hash=?, must_change=?, pw_version=pw_version+1 WHERE id=?",
                 (generate_password_hash(password), int(must_change), user_id))


def active_admins(conn):
    return conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0").fetchone()[0]


# --------------------------------------------------------------------------- session

@app.before_request
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid is None:
        return
    row = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    # Compte supprimé, désactivé ou mot de passe changé ailleurs : la session n'est plus valable.
    if row is None or row["disabled"] or row["pw_version"] != session.get("pwv"):
        session.clear()
        return
    g.user = row


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            if request.headers.get("X-Requested-With") == "fetch":
                abort(401)
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        if g.user["must_change"] and request.endpoint not in ("account", "logout"):
            if request.headers.get("X-Requested-With") == "fetch":
                abort(403)
            return redirect(url_for("account"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @login_required
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------- tentatives de connexion

def _blocked(ip, username):
    since = time.time() - config.LOGIN_WINDOW
    conn = db()
    conn.execute("DELETE FROM login_failures WHERE at < ?", (since,))
    by_ip = conn.execute("SELECT COUNT(*) FROM login_failures WHERE ip=? AND at>=?", (ip, since)).fetchone()[0]
    by_user = conn.execute("SELECT COUNT(*) FROM login_failures WHERE username=? AND at>=?",
                           (username, since)).fetchone()[0]
    return by_ip >= config.LOGIN_MAX_PER_IP or by_user >= config.LOGIN_MAX_PER_USER


def _record_failure(ip, username):
    db().execute("INSERT INTO login_failures (at, ip, username) VALUES (?,?,?)", (time.time(), ip, username))
    db().commit()


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()[:64]
        password = request.form.get("password", "")
        ip = request.remote_addr or ""
        if _blocked(ip, username):
            audit("connexion.bloquee", f"compte « {username} »", username="")
            flash("Trop de tentatives. Réessayez dans quelques minutes.", "error")
            return render_template("login.html", username=username), 429
        row = db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        valid = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password)
        if row and valid and not row["disabled"]:
            session.clear()
            session.permanent = True
            session.update(uid=row["id"], pwv=row["pw_version"], csrf=secrets.token_hex(16))
            db().execute("DELETE FROM login_failures WHERE username=?", (username,))
            db().execute("UPDATE users SET last_login=? WHERE id=?",
                         (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), row["id"]))
            db().commit()
            g.user = row
            audit("connexion")
            if row["must_change"]:
                return redirect(url_for("account"))
            return redirect(safe_next(request.args.get("next", "/")))
        _record_failure(ip, username)
        audit("connexion.echec", f"compte « {username} »", username="")
        time.sleep(0.5)
        flash("Identifiant ou mot de passe incorrect.", "error")
        return render_template("login.html", username=username), 401
    return render_template("login.html", username="")


@app.route("/logout", methods=["POST"])
def logout():
    if g.user is not None:
        audit("deconnexion")
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------- mon compte

@app.route("/compte", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not check_password_hash(g.user["password_hash"], current):
            flash("Mot de passe actuel incorrect.", "error")
        elif new != request.form.get("confirm", ""):
            flash("Les deux saisies du nouveau mot de passe diffèrent.", "error")
        elif new == current:
            flash("Le nouveau mot de passe doit être différent de l'actuel.", "error")
        elif (problem := password_problem(new, g.user["username"])):
            flash(problem, "error")
        else:
            set_password(db(), g.user["id"], new)
            db().commit()
            session["pwv"] = g.user["pw_version"] + 1
            audit("compte.mot_de_passe")
            if g.user["must_change"]:
                flash("Mot de passe enregistré. Bienvenue sur loco.", "ok")
            else:
                flash("Mot de passe changé. Les autres sessions de ce compte sont fermées.", "ok")
            return redirect(url_for("index"))
        return redirect(url_for("account"))
    # Mot de passe temporaire : page dédiée, sans la navigation, qui ne mène nulle part avant le changement.
    template = "password_setup.html" if g.user["must_change"] else "account.html"
    return render_template(template, min_password=config.MIN_PASSWORD)


# --------------------------------------------------------------------------- gestion des comptes

@app.route("/comptes")
@admin_required
def users():
    rows = db().execute("SELECT * FROM users ORDER BY username").fetchall()
    issued = session.pop("issued_password", None)
    return render_template("users.html", users=rows, roles=config.ROLES, issued=issued)


def _get_user(user_id):
    row = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/comptes/ajouter", methods=["POST"])
@admin_required
def users_add():
    username = request.form.get("username", "").strip().lower()
    role = request.form.get("role", "viewer")
    if not config.USERNAME_RE.match(username):
        flash("Identifiant invalide : 2 à 32 caractères, minuscules, chiffres, point, tiret.", "error")
    elif role not in config.ROLES:
        flash("Rôle inconnu.", "error")
    elif db().execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        flash(f"Le compte « {username} » existe déjà.", "error")
    else:
        password = generate_password()
        create_user(db(), username, password, role, must_change=True)
        db().commit()
        session["issued_password"] = {"username": username, "password": password}
        audit("comptes.ajout", f"{username} ({config.ROLES[role]})")
    return back("users")


@app.route("/comptes/<int:user_id>/reinitialiser", methods=["POST"])
@admin_required
def users_reset(user_id):
    row = _get_user(user_id)
    password = generate_password()
    set_password(db(), row["id"], password, must_change=True)
    db().commit()
    session["issued_password"] = {"username": row["username"], "password": password}
    audit("comptes.reinitialisation", row["username"])
    return back("users")


@app.route("/comptes/<int:user_id>/modifier", methods=["POST"])
@admin_required
def users_update(user_id):
    row = _get_user(user_id)
    role = request.form.get("role", row["role"])
    disabled = request.form.get("disabled") == "1"
    if role not in config.ROLES:
        abort(400)
    if row["id"] == g.user["id"] and (role != row["role"] or disabled):
        flash("Vous ne pouvez pas modifier le rôle ni désactiver votre propre compte.", "error")
        return back("users")
    losing_admin = row["role"] == "admin" and not row["disabled"] and (role != "admin" or disabled)
    if losing_admin and active_admins(db()) <= 1:
        flash("Il doit rester au moins un administrateur actif.", "error")
        return back("users")
    db().execute("UPDATE users SET role=?, disabled=?, pw_version=pw_version+? WHERE id=?",
                 (role, int(disabled), int(disabled and not row["disabled"]), row["id"]))
    db().commit()
    changes = []
    if role != row["role"]:
        changes.append(f"rôle {config.ROLES[role]}")
    if disabled != bool(row["disabled"]):
        changes.append("désactivé" if disabled else "réactivé")
    if changes:
        audit("comptes.modification", f"{row['username']} : {', '.join(changes)}")
        flash(f"Compte {row['username']} : {', '.join(changes)}.", "ok")
    return back("users")


@app.route("/comptes/<int:user_id>/supprimer", methods=["POST"])
@admin_required
def users_delete(user_id):
    row = _get_user(user_id)
    if row["id"] == g.user["id"]:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "error")
    elif row["role"] == "admin" and not row["disabled"] and active_admins(db()) <= 1:
        flash("Il doit rester au moins un administrateur actif.", "error")
    else:
        db().execute("DELETE FROM users WHERE id=?", (row["id"],))
        db().commit()
        audit("comptes.suppression", row["username"])
        flash(f"Compte {row['username']} supprimé.", "ok")
    return back("users")


# --------------------------------------------------------------------------- journal

@app.route("/journal")
@admin_required
def journal():
    rows = db().execute("SELECT * FROM audit ORDER BY id DESC LIMIT 500").fetchall()
    return render_template("journal.html", rows=rows)


def bootstrap_admin(conn, secrets_data):
    """Reprise d'une installation à mot de passe unique : crée le compte « admin » avec ce mot de passe."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 and secrets_data.get("admin_hash"):
        create_user(conn, "admin", role="admin", must_change=False, password_hash=secrets_data["admin_hash"])
