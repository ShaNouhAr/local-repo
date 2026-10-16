"""Agent du parc sur un serveur Windows réel : installation par la commande du portail (MSI), inscription,
inventaire, action à distance, puis retrait poussé depuis loco (service et MSI désinstallés).

À lancer avec le Python embarqué de loco, en administrateur, avec LOCO_E2E_PASSWORD (machine jetable).
"""
import http.cookiejar
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import locowin

cfg = locowin.load_config()
base = f"https://127.0.0.1:{cfg['https_port']}"
password = os.environ['LOCO_E2E_PASSWORD']
context = ssl.create_default_context(cafile=str(Path(cfg['etc_dir'], 'tls', 'ca.crt')))
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                     urllib.request.HTTPSHandler(context=context))
hostname = socket.gethostname().lower()


def get(path):
    with opener.open(base + path, timeout=90) as response:
        return response.read().decode()


def post(path, values):
    with opener.open(base + path, urllib.parse.urlencode(values).encode(), timeout=90) as response:
        return response.geturl(), response.read().decode()


def csrf(page):
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def powershell(command):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
                            capture_output=True, text=True, errors='replace')
    print(result.stdout.strip())
    return result


def wait(what, check, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(5)
    sys.exit(f'Délai dépassé : {what}')


url, html = post('/login', {'csrf': csrf(get('/login')), 'username': 'admin', 'password': password})
if url.endswith('/compte'):
    changed = password + '-Changed'
    url, html = post('/compte', {'csrf': csrf(html), 'current': password, 'new': changed, 'confirm': changed})
assert url.rstrip('/') == base, f'Connexion refusée : {url}'

# Commande d'installation affichée dans « Mon parc » : amorçage en HTTP (autorité pas encore importée).
fleet = get('/parc')
key = re.search(r"/agent/windows\.ps1\?key=([A-Za-z0-9_-]+)", fleet).group(1)
http_base = f"http://127.0.0.1:{cfg['http_port']}" if cfg.get('http_port') else base
result = powershell(f"irm '{http_base}/agent/windows.ps1?key={key}' | iex")
assert result.returncode == 0 and 'Agent loco installe' in result.stdout, result.stderr
service = powershell('(Get-Service loco-client).Status').stdout.strip()
assert service == 'Running', service
print('Agent Windows : MSI vérifié et installé, service loco-client démarré')


def device_id():
    m = re.search(r'href="/parc/(\d+)" class="mono nowrap">' + re.escape(hostname) + '<', get('/parc'))
    return m.group(1) if m else None


dev = wait('inscription du poste', device_id, 300)
page = wait('premier inventaire', lambda: (lambda p: p if 'Inventaire en cours' not in p else None)(get(f'/parc/{dev}')), 900)
assert 'loco-client' in page and 'Windows' in page, page[:2000]
print(f'Poste {hostname} inscrit (n° {dev}) et inventorié')
# Les clés d'inscription ne restent pas dans le registre, lisible par les utilisateurs du poste.
leftover = powershell("(Get-ItemProperty 'HKLM:\\SOFTWARE\\loco\\Client' -ErrorAction SilentlyContinue).Key").stdout.strip()
assert not leftover, 'clé d\'inscription restée dans le registre'

url, html = post(f'/parc/{dev}/action', {'csrf': csrf(get(f'/parc/{dev}')), 'action': 'scan'})
wait('résultat de l\'inventaire demandé',
     lambda: re.search(r'<td>Inventaire</td>\s*<td[^>]*>[^<]*</td>\s*<td><span class="st (ok|err)">', get(f'/parc/{dev}')), 900)
print('Action à distance : inventaire exécuté et rapporté')

url, html = post(f'/parc/{dev}/action', {'csrf': csrf(get(f'/parc/{dev}')), 'action': 'uninstall'})
wait('retrait de l\'agent', lambda: powershell('if (Get-Service loco-client -ErrorAction SilentlyContinue) { 1 } else { 0 }').stdout.strip() == '0', 600)
assert hostname not in re.findall(r'class="mono nowrap">([^<]+)<', get('/parc')), 'poste encore dans le parc'
installed = powershell("Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' | "
                       "Where-Object DisplayName -eq 'loco-client' | Measure-Object | Select-Object -ExpandProperty Count")
assert installed.stdout.strip() == '0', 'MSI loco-client encore installé'
print('Retrait poussé depuis loco : service et MSI désinstallés, poste retiré du parc')
