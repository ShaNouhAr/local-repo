#!/usr/bin/env python3
"""loco-client @VERSION@ : agent loco des postes Linux (Debian, Ubuntu, Rocky Linux, AlmaLinux, RHEL).

Lancé toutes les deux minutes par loco-client.timer (root). Contacte le serveur loco avec son identifiant et son
jeton (indépendants de l'adresse IP du poste), envoie l'inventaire des mises à jour et exécute les actions
demandées depuis le portail : inventaire, mise à jour, redémarrage, retrait de l'agent. Aucune commande libre.
  loco-client run                         contact du serveur (service)
  loco-client enroll --server URL --key CLÉ
  loco-client status
  loco-client remove                      retrait local (le poste reste à oublier dans le portail)
Bibliothèque standard de Python uniquement ; autorité du serveur épinglée (/etc/loco-client/ca.crt).
"""

import datetime
import fcntl
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

VERSION = "@VERSION@"
ETC = "/etc/loco-client"
CONFIG = os.path.join(ETC, "client.json")
CA = os.path.join(ETC, "ca.crt")
STATE = "/var/lib/loco-client/state.json"
LOCK = "/run/loco-client.lock"
PROGRAM = "/usr/local/lib/loco-client/loco-client.py"
UNITS = ("/etc/systemd/system/loco-client.service", "/etc/systemd/system/loco-client.timer")
OUTPUT_LIMIT = 60000
ENV = dict(os.environ, LC_ALL="C", LANG="C", DEBIAN_FRONTEND="noninteractive")


def log(text):
    print(f"loco-client : {text}", flush=True)


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- serveur

def context():
    ctx = ssl.create_default_context(cafile=CA if os.path.isfile(CA) else None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def call(server, path, data, token=None, timeout=60):
    body = json.dumps(data).encode()
    headers = {"Content-Type": "application/json", "User-Agent": f"loco-client/{VERSION}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(server.rstrip("/") + path, data=body, headers=headers, method="POST")
    # Le serveur loco est sur le réseau local : pas de proxy, même si le poste en utilise un.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context()))
    with opener.open(req, timeout=timeout) as r:
        return json.load(r)


def download(server, path, timeout=120):
    req = urllib.request.Request(server.rstrip("/") + path, headers={"User-Agent": f"loco-client/{VERSION}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context()))
    with opener.open(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------------------- poste

def os_release():
    info = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                info[k] = v.strip('"')
    except OSError:
        pass
    return info


def family():
    if shutil.which("apt-get"):
        return "debian"
    if shutil.which("dnf") or shutil.which("yum"):
        return "rhel"
    return ""


def addresses():
    out = []
    try:
        text = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True,
                              timeout=10).stdout
        for line in text.splitlines():
            fields = line.split()
            if len(fields) > 3 and fields[2] in ("inet", "inet6"):
                out.append(fields[3].split("/")[0])
    except (OSError, subprocess.SubprocessError):
        pass
    return out[:16]


def identity():
    rel = os_release()
    return {"hostname": socket.gethostname(), "agent_version": VERSION, "addresses": addresses(),
            "os": {"family": family(), "name": rel.get("PRETTY_NAME") or platform.platform()}}


def run(cmd, timeout=7200):
    """(code, sortie) ; la sortie est tronquée au début pour garder la fin, la plus utile."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                           env=ENV, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, f"{cmd[0]} introuvable\n"
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") if isinstance(e.stdout, str) else "délai dépassé\n"


def booted_since(timestamp):
    try:
        with open("/proc/uptime") as f:
            return time.time() - float(f.read().split()[0]) > timestamp
    except (OSError, ValueError, IndexError):
        return False


def flatpak_updates():
    if not shutil.which("flatpak"):
        return 0
    code, out = run(["flatpak", "remote-ls", "--updates", "--system", "--columns=application"], timeout=600)
    return len([line for line in out.splitlines() if line.strip()]) if code == 0 else 0


def reboot_required():
    if os.path.exists("/var/run/reboot-required"):
        return True
    for cmd in (["needs-restarting", "-r"], ["dnf", "needs-restarting", "-r"]):
        if shutil.which(cmd[0]):
            code, _ = run(cmd, timeout=300)
            if code in (0, 1):
                return code == 1
    return False


def scan():
    """Mises à jour disponibles (nom, version, sécurité), Flatpak et redémarrage nécessaire."""
    inv = {"updates": [], "flatpak": 0, "reboot_required": False, "error": ""}
    fam = family()
    if fam == "debian":
        code, out = run(["apt-get", "update", "-q"], timeout=1800)
        if code != 0:
            inv["error"] = "apt-get update : " + out.strip().splitlines()[-1][:300] if out.strip() else "apt-get update"
        code, out = run(["apt-get", "-s", "-q", "-o", "Debug::NoLocking=1", "upgrade", "--with-new-pkgs"])
        for line in out.splitlines():
            m = re.match(r"^Inst (\S+) (?:\[[^\]]*\] )?\((\S+) ([^)]*)\)", line)
            if m:
                inv["updates"].append({"name": m.group(1), "version": m.group(2),
                                       "security": "-security" in m.group(3) or "Security" in m.group(3)})
    elif fam == "rhel":
        tool = "dnf" if shutil.which("dnf") else "yum"
        code, out = run([tool, "-q", "--refresh", "check-update"], timeout=1800)
        if code not in (0, 100):
            inv["error"] = f"{tool} check-update : code {code}"
        security = set()
        scode, sout = run([tool, "-q", "updateinfo", "list", "--security", "--available"], timeout=600)
        if scode == 0:
            for line in sout.splitlines():
                fields = line.split()
                if len(fields) >= 3:
                    security.add(re.sub(r"-[^-]+-[^-]+$", "", fields[-1]))
        for line in out.splitlines():
            fields = line.split()
            if len(fields) == 3 and "." in fields[0] and not line.startswith(" "):
                name = fields[0].rsplit(".", 1)[0]
                inv["updates"].append({"name": name, "version": fields[1], "security": name in security})
            if line.startswith("Obsoleting"):
                break
    else:
        inv["error"] = "gestionnaire de paquets non reconnu"
    inv["flatpak"] = flatpak_updates()
    inv["reboot_required"] = reboot_required()
    return inv


def update():
    fam = family()
    logs = []
    if fam == "debian":
        code, out = run(["apt-get", "update", "-q"], timeout=1800)
        logs.append(out)
        if code == 0:
            code, out = run(["apt-get", "-y", "-q", "-o", "Dpkg::Options::=--force-confdef",
                             "-o", "Dpkg::Options::=--force-confold", "upgrade", "--with-new-pkgs"])
            logs.append(out)
    elif fam == "rhel":
        code, out = run(["dnf" if shutil.which("dnf") else "yum", "-y", "upgrade"])
        logs.append(out)
    else:
        code, logs = 1, ["gestionnaire de paquets non reconnu"]
    if shutil.which("flatpak"):
        fcode, out = run(["flatpak", "update", "-y", "--noninteractive", "--system"], timeout=3600)
        logs.append(out)
        code = code or fcode
    return code, "\n".join(logs)


# --------------------------------------------------------------------------- service

def report(conf, cmd_id, state, output="", code=None, inventory=None):
    data = {"id": cmd_id, "state": state, "output": output[-OUTPUT_LIMIT:], "exit_code": code}
    if inventory is not None:
        data["inventory"] = inventory
    try:
        call(conf["server"], "/agent/api/result", data, conf["token"])
    except (OSError, ValueError) as e:
        log(f"résultat non transmis : {e}")


def self_update(conf, wanted):
    """Nouvelle version de loco sur le serveur : l'agent se met à jour (code servi par le serveur épinglé)."""
    def key(v):
        return tuple(int(x) for x in re.findall(r"\d+", v or ""))
    if not wanted or key(wanted) <= key(VERSION):
        return
    try:
        code = download(conf["server"], "/agent/files/loco-client.py").decode()
        compile(code, PROGRAM, "exec")
    except (OSError, ValueError, SyntaxError) as e:
        log(f"mise à jour de l'agent impossible : {e}")
        return
    if f'VERSION = "{wanted}"' not in code:
        return
    tmp = PROGRAM + ".new"
    with open(tmp, "w") as f:
        f.write(code)
    os.chmod(tmp, 0o755)
    os.replace(tmp, PROGRAM)
    log(f"agent mis à jour : {VERSION} -> {wanted}")


def remove_local():
    subprocess.run(["systemctl", "disable", "--now", "loco-client.timer"], capture_output=True)
    for path in UNITS:
        if os.path.exists(path):
            os.unlink(path)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    for path in (ETC, os.path.dirname(STATE), os.path.dirname(PROGRAM)):
        shutil.rmtree(path, ignore_errors=True)
    for link in ("/usr/local/sbin/loco-client",):
        if os.path.islink(link):
            os.unlink(link)


def service():
    conf = load(CONFIG)
    if not conf.get("token"):
        log("poste non inscrit : relancez la commande d'installation de l'agent.")
        return 1
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    state = load(STATE)
    data = identity()
    last = state.get("last_scan", 0)
    # Inventaire refait après un redémarrage : celui d'avant ne reflète plus les mises à jour installées.
    if time.time() - last > state.get("scan_hours", 6) * 3600 or state.get("rescan") or booted_since(last):
        data["inventory"] = scan()
        state.update(last_scan=time.time(), rescan=False)
    try:
        answer = call(conf["server"], "/agent/api/checkin", data, conf["token"])
    except urllib.error.HTTPError as e:
        if e.code == 401:
            log("jeton refusé par le serveur (poste retiré du parc ?) : réinstallez l'agent pour le réinscrire.")
        else:
            log(f"serveur : HTTP {e.code}")
        save(STATE, state)
        return 1
    except (OSError, ValueError) as e:
        log(f"serveur injoignable : {e}")
        # Inventaire non transmis : à refaire au prochain contact.
        if "inventory" in data:
            state["rescan"] = True
        save(STATE, state)
        return 1
    state["scan_hours"] = answer.get("scan_hours", 6)
    for cmd in answer.get("commands", []):
        action = cmd.get("action")
        log(f"action {cmd.get('id')} : {action}")
        report(conf, cmd["id"], "running")
        if action == "scan":
            inv = scan()
            state["last_scan"] = time.time()
            report(conf, cmd["id"], "success" if not inv["error"] else "failed", inv["error"] or "Inventaire transmis.",
                   0, inv)
        elif action == "update":
            code, out = update()
            inv = scan()
            state["last_scan"] = time.time()
            reboot = cmd.get("reboot") and inv["reboot_required"] and code == 0
            report(conf, cmd["id"], "success" if code == 0 else "failed",
                   out + ("\nRedémarrage dans une minute." if reboot else ""), code, inv)
            if reboot:
                save(STATE, state)
                subprocess.run(["shutdown", "-r", "+1", "loco : redémarrage après mise à jour"], capture_output=True)
                return 0
        elif action == "reboot":
            report(conf, cmd["id"], "success", "Redémarrage dans une minute.", 0)
            save(STATE, state)
            subprocess.run(["shutdown", "-r", "+1", "loco : redémarrage demandé"], capture_output=True)
            return 0
        elif action == "uninstall":
            report(conf, cmd["id"], "success", "Agent retiré du poste.", 0)
            remove_local()
            log("agent retiré à la demande du serveur.")
            return 0
        else:
            report(conf, cmd["id"], "failed", f"Action non prise en charge sur Linux : {action}", 1)
    save(STATE, state)
    self_update(conf, answer.get("agent_version"))
    return 0


def enroll(server, key):
    os.makedirs(ETC, mode=0o700, exist_ok=True)
    conf = load(CONFIG)
    uid = conf.get("uid") or uuid.uuid4().hex
    data = identity()
    if not data["os"]["family"]:
        log("système non pris en charge (APT ou DNF requis).")
        return 1
    data.update(key=key, uid=uid)
    try:
        answer = call(server, "/agent/api/enroll", data)
    except urllib.error.HTTPError as e:
        log("clé d'inscription refusée." if e.code == 403 else f"inscription refusée : HTTP {e.code}")
        return 1
    except (OSError, ValueError) as e:
        log(f"serveur injoignable : {e}")
        return 1
    save(CONFIG, {"server": server.rstrip("/"), "uid": uid, "token": answer["token"],
                  "enrolled": datetime.datetime.now().isoformat(timespec="seconds")})
    save(STATE, {"rescan": True})
    log(f"poste inscrit auprès de {server}.")
    return 0


def status_cmd():
    conf = load(CONFIG)
    state = load(STATE)
    print(f"Agent      : loco-client {VERSION}")
    print(f"Serveur    : {conf.get('server', 'non inscrit')}")
    print(f"Identifiant: {conf.get('uid', '-')}")
    scanned = state.get("last_scan")
    print(f"Inventaire : {datetime.datetime.fromtimestamp(scanned).isoformat(timespec='seconds') if scanned else 'jamais'}")
    return 0


def main(argv):
    if os.geteuid() != 0:
        print("loco-client : lancez cette commande en root.", file=sys.stderr)
        return 1
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        return service()
    if cmd == "enroll" and len(argv) == 5 and argv[1] == "--server" and argv[3] == "--key":
        return enroll(argv[2], argv[4])
    if cmd == "status":
        return status_cmd()
    if cmd == "remove":
        conf = load(CONFIG)
        remove_local()
        print("Agent loco retiré de ce poste." + (" Retirez-le aussi de la liste du parc dans le portail."
                                                    if conf.get("token") else ""))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
