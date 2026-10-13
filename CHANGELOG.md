# Journal des versions

## 1.2.0 — 2026-10-13

- **Famille Red Hat** : cache des miroirs Rocky Linux, AlmaLinux et EPEL (paquets et métadonnées à nom
  unique gardés 6 mois, certificats des miroirs vérifiés) ; dépôts RPM locaux par version majeure
  (`el8`, `el9`, `el10`, ajout d'une future version depuis le portail), métadonnées signées, refus des
  paquets d'une autre version ou d'un contenu différent pour un même nom/version.
- Script `rpm/setup.sh` : détection du poste, dépôt local, dépôts officiels et EPEL via le cache avec leurs
  clés GPG, `--epel`, `--trust-ca`, `--no-cache`, `--no-local`, `--remove` (rétablissement exact des dépôts).
- RHEL : dépôt local et EPEL via ce serveur ; dépôts Red Hat (abonnement) en accès direct.
- Options `--no-rpm` et `--rpm-cache-size` ; sauvegarde, statistiques, `/metrics` et désinstallation
  couvrent les dépôts RPM.
- Tests réels (`rpmbuild`, `createrepo_c`, signatures) et validation de postes Rocky Linux / AlmaLinux 8, 9
  et 10 dans des VM, ajoutée à la CI.

## 1.1.0 — 2026-10-13

- Dépôts APT isolés par distribution et nom de version, création depuis le portail ou `local-repo-apt`.
- Détection automatique des postes Debian / Ubuntu, sources deb822 ; options `--cache-only`,
  `--distribution`, `--suite`, `--legacy`. Une version absente ne remplace pas la configuration du poste.
- Dépôt historique conservé, copie explicite de paquets entre dépôts ; publication sans remplacement
  d'un paquet/version par un contenu différent ; métadonnées signées préparées avant publication.
- Sauvegarde et restauration de tous les dépôts, migration du registre des versions.
- Corrections de compatibilité : HTTPS APT en accès direct, connexions sortantes du cache conservées,
  nettoyage du cache des postes lors d'un changement de dépôt ; prise en charge du canal de contrôle
  des nouvelles versions de Gunicorn et délais bornés pour vérifier le démarrage.
- Tests d'isolation et de signatures, test de migration depuis 1.0.0 ; matrice de VM Debian 12/13 et
  Ubuntu 22.04/24.04/26.04, images officielles vérifiées, contrôles hebdomadaires actifs dans la CI.
- Navigation Linux / Windows avec icônes et menus au survol, au clavier et au toucher ; icônes du compte.

## 1.0.0 — 2026-10-13

Première version destinée à un usage en entreprise.

### Ajouté
- **Flatpak** : cache Flathub (objets immuables gardés 6 mois, signatures Flathub vérifiées par les postes)
  et dépôt Flatpak local signé, alimenté par envoi de paquets `.flatpak` ; script `flatpak/setup.sh`.
- **Comptes nommés** (administrateur, lecture seule), mot de passe temporaire à changer, blocage après échecs,
  expiration des sessions, journal d'audit, gestion en ligne de commande (`local-repo-user`).
- **HTTPS partout** : postes clients en HTTPS par défaut, scripts suivant le protocole utilisé,
  `setup.sh --trust-ca` pour l'amorçage, `--no-http` pour fermer le port HTTP.
- **Réseaux autorisés** (`--allow`, `--admin-allow`) appliqués par nginx, apt-cacher-ng et systemd.
- **Proxy d'entreprise** (`--proxy`) pour apt-cacher-ng et les téléchargements du portail.
- **Sauvegardes** quotidiennes avec rotation, restauration sur le même serveur ou un nouveau
  (`local-repo-backup`).
- **Supervision** : `/metrics` (Prometheus) et `/healthz`.
- Page **Administration** (comptes, journal, système, sauvegardes).
- Tests unitaires, test de bout en bout (`tests/e2e.sh`) et intégration continue GitHub.
- Guide d'exploitation (`docs/EXPLOITATION.md`) et politique de sécurité (`SECURITY.md`).

### Modifié
- Politique de contenu stricte (CSP) : plus aucun script ni style en ligne.
- Unité systemd du portail confinée ; mots de passe d'apt-cacher-ng pour sa maintenance.
- Portail réorganisé en paquet Python ; base SQLite en WAL avec migrations versionnées.
- Interface : navigation regroupée, sélecteur de thème dans *Mon compte*.

### Sécurité
- Le compte unique des versions précédentes devient le compte `admin` (même mot de passe) ;
  le fichier `/etc/local-repo/admin-password` est supprimé.

## 0.3 — 2026-10-13
- Interface en HTTPS (autorité locale), refonte visuelle sobre (IBM Plex, thèmes clair et OLED).

## 0.2 — 2026-10-13
- Cache Windows Update limité aux domaines de la liste, fichiers immuables seuls en cache,
  corrections issues des tests (Debian 12/13, Ubuntu 24.04, Windows 11).

## 0.1 — 2026-10-12
- Première version : apt-cacher-ng et dépôt APT local, cache Windows Update, catalogue de logiciels Windows,
  portail web, `install.sh` et `remove.sh`.
