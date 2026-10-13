"""Pilote le portail pour le test de bout en bout : python3 e2e_portal.py URL MOT_DE_PASSE FICHIER [PAGE]

PAGE : linux (paquet .deb, défaut) ou flatpak (paquet .flatpak).
Argument suivant : dépôt distribution/suite (obligatoire pour Linux)."""
import http.cookiejar, re, ssl, sys, urllib.parse, urllib.request, uuid

base, password, path = sys.argv[1:4]
page = sys.argv[4] if len(sys.argv) > 4 else "linux"
repository = sys.argv[5] if len(sys.argv) > 5 else ""
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                 urllib.request.HTTPSHandler(context=ctx))


def get(path):
    with op.open(base + path) as r:
        return r.read().decode()


def token(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


html = get("/login")
data = urllib.parse.urlencode({"csrf": token(html), "username": "admin", "password": password}).encode()
with op.open(base + "/login", data) as r:
    assert r.geturl().rstrip("/") == base, f"connexion refusée ({r.geturl()})"
html = get("/" + page + ("?" + urllib.parse.urlencode({"repository": repository}) if page == "linux" else ""))
b = uuid.uuid4().hex
body = (f'--{b}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\n{token(html)}\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="repository"\r\n\r\n{repository}\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{path.rsplit("/", 1)[-1]}"\r\n\r\n').encode()
with open(path, "rb") as f:
    body += f.read() + f"\r\n--{b}--\r\n".encode()
req = urllib.request.Request(base + f"/{page}/upload", body, {"Content-Type": f"multipart/form-data; boundary={b}"})
with op.open(req) as r:
    result = r.read().decode()
msgs = re.findall(r'data-flash="([^"]*)"', result)
assert any(m.startswith(("Paquet ajouté", "Ajouté")) for m in msgs), f"envoi refusé : {msgs}"
print(f"portail : envoi vers /{page} OK")
