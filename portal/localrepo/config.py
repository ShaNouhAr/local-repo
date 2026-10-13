"""Configuration lue dans l'environnement (fixé par l'unité systemd générée par install.sh)."""

import os
import re

VERSION = "1.1.0"

DATA = os.environ.get("LR_DATA", "/srv/local-repo")
STATE = os.environ.get("LR_STATE", "/var/lib/local-repo")
ETC = os.environ.get("LR_ETC", "/etc/local-repo")
HOST = os.environ.get("LR_HOST", "localhost")
ACNG_PORT = os.environ.get("LR_ACNG_PORT", "3142")

# Adresse HTTPS (interface, et postes qui font confiance à l'autorité locale).
HTTPS_URL = os.environ.get("LR_ADMIN_URL", "https://localhost:8443").rstrip("/")
# Adresse HTTP des postes clients ; vide si le HTTP est désactivé (--no-http).
HTTP_URL = os.environ.get("LR_PUBLIC_URL", "").rstrip("/")

TLS_DIR = os.environ.get("LR_TLS_DIR", os.path.join(ETC, "tls"))
SECRETS_PATH = os.environ.get("LR_SECRETS", os.path.join(ETC, "portal.secret"))
WU_LOG = os.environ.get("LR_WU_LOG", "/var/log/nginx/local-repo-wu.log")
GNUPGHOME = os.environ.get("LR_GNUPGHOME", os.path.join(STATE, "gnupg"))
BACKUP_DIR = os.environ.get("LR_BACKUP_DIR", "/var/backups/local-repo")

APT_DIR = os.path.join(DATA, "apt")
POOL_DIR = os.path.join(APT_DIR, "pool")
APT_REPOSITORIES_DIR = os.path.join(APT_DIR, "repositories")
APT_SUITE_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
APT_DISTRIBUTIONS = {"debian": "Debian", "ubuntu": "Ubuntu"}
WIN_DIR = os.path.join(DATA, "win")
TMP_DIR = os.path.join(DATA, ".tmp")
DB_PATH = os.path.join(STATE, "portal.db")
STATS_PATH = os.path.join(STATE, "stats.json")
WU_DOMAINS_PATH = os.path.join(STATE, "wu-domains.txt")
NGINX_MAP_DIR = os.path.join(STATE, "nginx")
FLATPAK_ENABLED = os.environ.get("LR_FLATPAK", "1") == "1"
FLATPAK_REPO = os.path.join(DATA, "flatpak", "repo")
FLATHUB_URL = "https://dl.flathub.org/repo/"

DEFAULT_WU_DOMAINS = ["windowsupdate.com", "dl.delivery.mp.microsoft.com"]

# Arguments silencieux proposés par défaut selon le type de fichier.
DEFAULT_ARGS = {"msi": "/qn /norestart", "msu": "/quiet /norestart", "exe": "", "ps1": "", "file": ""}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
FLATPAK_REF_RE = re.compile(r"^(app|runtime)/[A-Za-z0-9_][A-Za-z0-9_.-]{2,254}/[a-z0-9_]{1,16}/[A-Za-z0-9_.-]{1,64}$")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
USER_AGENT = f"local-repo/{VERSION}"

# Comptes et sessions
ROLES = {"admin": "Administrateur", "viewer": "Lecture seule"}
MIN_PASSWORD = 12
SESSION_IDLE_HOURS = 8
LOGIN_WINDOW = 15 * 60          # fenêtre d'observation des échecs (s)
LOGIN_MAX_PER_USER = 5          # échecs avant blocage temporaire du compte
LOGIN_MAX_PER_IP = 20           # échecs avant blocage temporaire de l'adresse
