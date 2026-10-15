# Passer de local-repo à loco

La version 2.0 renomme le projet et ses identifiants techniques. Les comptes, mots de passe, clés de
signature, certificats et paquets restent les mêmes. Une sauvegarde précède la migration du serveur.
Les archives déjà créées gardent leur nom historique et sont restaurables avec les nouvelles commandes.

## Serveur Linux

Depuis les nouvelles sources, lancer `sudo ./install.sh`. L'installateur détecte
`/etc/local-repo/local-repo.conf`, arrête les anciens services, renomme le compte système en `loco`
en conservant son UID et déplace la configuration, l'état et le programme vers `/etc/loco`,
`/var/lib/loco` et `/opt/loco`. Les dossiers par défaut deviennent `/srv/loco` et `/var/backups/loco`.
Les dossiers de données et de sauvegardes personnalisés restent à leur emplacement.

Les commandes sont `loco-user`, `loco-apt` et `loco-backup`. Adapter les tâches d'administration
externes qui appelaient les anciens noms. Les métriques portent le préfixe `loco_` et l'en-tête de
diagnostic des caches devient `X-Loco-Cache`. L'adresse et les ports du portail ne changent pas.
Les dépôts existants conservent leur identité APT signée pour éviter un refus de changement d'Origin
sur les postes ; les dépôts créés ensuite portent le nom loco.

Dans un conteneur LXC Proxmox, télécharger le nouveau `linux/lxc/proxmox-lxc.sh` puis utiliser
`--ctid N --update`. Le nom par défaut `local-repo` devient `loco` ; un nom personnalisé reste inchangé.

## Serveur Windows

Relancer `windows\install.ps1` depuis les nouvelles sources dans PowerShell administrateur.
L'installateur sauvegarde l'état, retire l'ancien service puis déplace les dossiers par défaut vers
`%ProgramFiles%\loco` et `%ProgramData%\loco`. Les chemins personnalisés hors de ces dossiers restent
inchangés. Le nouveau service est `loco`, sous le compte virtuel `NT SERVICE\loco` ; les droits sont
réappliqués. La commande d'administration devient `%ProgramFiles%\loco\loco.cmd`.

## Docker

Dans `linux/docker`, lancer **`sudo ./up.sh`** pour migrer ; ne pas lancer un deuxième projet à la main.
Python 3 doit être présent sur l'hôte. Le script reprend les réglages du conteneur existant, convertit
les variables `LR_` de `.env` en `LOCO_` et garde `.env.before-loco`. Il construit
l'image, sauvegarde et arrête l'ancien conteneur, puis copie ses volumes nommés vers ceux du projet
`loco`. Les dossiers bind personnalisés restent les mêmes. Prévoir assez d'espace pour cette copie.

Les volumes historiques restent disponibles comme sauvegarde après migration. Le script ne les
supprime jamais. En cas d'échec, il retire uniquement les volumes qu'il vient de créer et redémarre
l'ancien conteneur. Les destinations déjà présentes sont refusées sans écrasement.

## Postes

Les postes déjà branchés continuent à télécharger leurs paquets : mêmes adresses, mêmes clés et
mêmes certificats. Relancer le script `linux/setup.sh`, `rpm/setup.sh` ou `flatpak/setup.sh` du portail
pour migrer leurs noms de configuration. APT retire les anciennes sources après avoir validé les
nouvelles. RPM conserve le journal nécessaire à la restauration des dépôts du système. Flatpak
réinstalle depuis le même dépôt signé pour changer l'origine des applications vers `loco`.

Les archives anciennes et les certificats existants peuvent encore contenir le nom local-repo :
ils sont conservés pour garder leur validité et leur traçabilité. Aucune clé n'est régénérée pour
changer uniquement son libellé.
