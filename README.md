# local-repo

Dépôt local pour un réseau domestique ou de PME, installé en une commande sur Debian / Ubuntu, **sans Docker** :

- **Paquets Linux** : cache [apt-cacher-ng](https://www.unix-ag.uni-kl.de/~bloch/acng/) pour Debian/Ubuntu, plus un dépôt APT local signé pour vos propres `.deb`.
- **Mises à jour Windows** : cache nginx des téléchargements Windows Update (même principe que [lancache](https://lancache.net)).
- **Logiciels Windows** : catalogue d'installeurs (`exe`, `msi`, `msu`, `ps1`) installables en silence par une commande PowerShell.
- **Portail web** : une page centrale sobre pour tout ajouter, suivre et copier les commandes.

## Installation

```bash
git clone https://github.com/ShaNouhAr/local-repo.git
cd local-repo
sudo ./install.sh
```

À la fin, le script affiche l'adresse du portail (`http://<ip>:8088`) et le mot de passe généré
(aussi gardé dans `/etc/local-repo/admin-password`).

Options utiles :

| Option | Effet |
|---|---|
| `--port 8088` | port du portail |
| `--host 192.168.1.10` | adresse annoncée aux clients (détectée sinon) |
| `--data /srv/local-repo` | dossier des données et des caches |
| `--wu-cache-size 100g` | taille max du cache Windows Update (défaut : moitié de l'espace libre, 200g max) |
| `--password MDP` | définit / change le mot de passe du portail |
| `--no-wu` | sans cache Windows Update (laisse les ports 80 et 443 libres) |

Le script est relançable : il sert aussi à mettre à jour (`git pull && sudo ./install.sh`) ou à changer une option.
Les réglages sont gardés dans `/etc/local-repo/local-repo.conf`.

### Désinstallation

```bash
sudo /opt/local-repo/remove.sh           # garde les données (logiciels, .deb, caches, clé APT)
sudo /opt/local-repo/remove.sh --purge   # efface tout
```

Seuls les paquets installés par `install.sh` sont retirés ; ceux qui étaient déjà présents restent.

## Utilisation

### Postes Linux (Debian / Ubuntu)

```bash
curl -fsSL http://<ip>:8088/linux/setup.sh | sudo sh
```

Le poste passe par le cache apt-cacher-ng et voit le dépôt local. Variantes :
`| sudo sh -s -- --no-proxy` (dépôt local seul), `| sudo sh -s -- --remove` (annuler).

Les `.deb` ajoutés depuis le portail (fichier ou URL) s'installent ensuite avec `sudo apt install <paquet>`.
Les dépôts HTTPS (Docker, etc.) restent en accès direct ; seuls les dépôts HTTP passent par le cache.

### Logiciels Windows

Dans le portail, **Logiciels Windows** → ajouter un logiciel par URL (téléchargé par le serveur) ou par envoi de fichier.
Cochez ensuite un ou plusieurs logiciels et copiez la commande, à lancer dans un PowerShell **administrateur** :

```powershell
irm 'http://<ip>:8088/win/i/7-zip,vlc' | iex
```

Les guillemets sont nécessaires : sans eux, PowerShell lit la virgule comme un tableau.
Le script télécharge depuis le serveur, vérifie le SHA256 et installe en silence :

| Type | Commande lancée | Arguments par défaut |
|---|---|---|
| msi | `msiexec /i fichier.msi <args>` | `/qn /norestart` |
| msu | `wusa fichier.msu <args>` | `/quiet /norestart` |
| exe | `fichier.exe <args>` | à renseigner (`/S`, `/silent`, `/quiet`… selon l'éditeur) |
| ps1 | `powershell -File fichier.ps1 <args>` | — |
| fichier | copie dans `C:\Users\Public\Downloads` | — |

**Actualiser** retélécharge depuis l'URL source : pratique avec les liens « dernière version ».

### Windows Update

Le cache répond sur le port 80 à la place des serveurs Microsoft. Pour que les postes l'utilisent,
le DNS du réseau doit renvoyer l'IP du serveur pour les domaines Windows Update
(par défaut `windowsupdate.com` et `dl.delivery.mp.microsoft.com`, avec leurs sous-domaines).

Avec un DNS Active Directory : page **Windows Update** du portail → télécharger le script
`local-repo-dns.ps1`, puis sur le contrôleur de domaine :

```powershell
.\local-repo-dns.ps1           # crée les zones
.\local-repo-dns.ps1 -Remove   # retour arrière
```

Avec un autre DNS (Pi-hole, AdGuard, dnsmasq, box…), créez l'équivalent : `windowsupdate.com` et
`*.windowsupdate.com` → IP du serveur, idem pour `dl.delivery.mp.microsoft.com`.

Le trafic HTTPS vers ces domaines est relayé tel quel (port 443, sans déchiffrement) ; seuls les
téléchargements HTTP sont mis en cache, ce qui couvre l'essentiel du volume des mises à jour.
Le premier poste télécharge depuis Internet, les suivants sont servis en local.

## Fonctionnement

| Élément | Où | Port |
|---|---|---|
| Portail (Flask + gunicorn, service `local-repo-portal`) | `/opt/local-repo` | 8089 (local) |
| nginx : portail, `/apt/`, `/win/files/` | `/etc/nginx/sites-available/local-repo-portal.conf` | 8088 |
| nginx : cache Windows Update | `/etc/nginx/sites-available/local-repo-wucache.conf` | 80 |
| nginx : relais HTTPS (SNI) | `/etc/nginx/modules-enabled/99-local-repo-stream.conf` | 443 |
| apt-cacher-ng | `/var/cache/apt-cacher-ng` | 3142 |
| Données (dépôt APT, logiciels, cache WU) | `/srv/local-repo` | — |
| Base, clé GPG du dépôt, statistiques | `/var/lib/local-repo` | — |

Tout vient des dépôts officiels Debian (`nginx`, `apt-cacher-ng`, `python3-flask`, `gunicorn`, `gnupg`) :
les mises à jour de sécurité arrivent avec `apt upgrade`.

Journaux : `journalctl -u local-repo-portal`, `/var/log/nginx/local-repo-*.log`.

## Sécurité

- Pensé pour un **réseau local de confiance** : n'exposez pas ces ports sur Internet.
  Le cache Windows Update (80/443) relaie toute requête qu'il reçoit, comme lancache.
- Le portail d'administration est protégé par mot de passe (session + jeton CSRF).
  Les téléchargements (`/apt/`, `/win/files/`, scripts d'installation) sont publics sur le réseau.
- Le dépôt APT local est signé par une clé générée à l'installation.
- Le portail tourne sous un utilisateur dédié (`localrepo`) avec un service systemd restreint.

## Licence

MIT
