# Sécurité

## Signaler une vulnérabilité

Ouvrez un avis privé sur GitHub (onglet *Security › Report a vulnerability*) plutôt qu'une issue publique.
Merci d'indiquer la version (`local-repo` en bas de la page de connexion), la distribution et les étapes
pour reproduire.

## Modèle de menace

local-repo est conçu pour un **réseau d'entreprise interne**. Il n'est pas prévu pour être exposé sur Internet.

| Élément | Mesures |
|---|---|
| Interface d'administration | HTTPS uniquement (TLS 1.2+), comptes nommés avec rôles, mots de passe hachés (scrypt), politique de longueur, changement imposé des mots de passe temporaires, blocage après échecs (par compte et par adresse), sessions expirant après 8 h d'inactivité et invalidées au changement de mot de passe, jeton CSRF, cookie `Secure`/`HttpOnly`/`SameSite=Strict`, en-têtes CSP stricte (aucun script ni style en ligne), `X-Frame-Options: DENY`, `nosniff`, réseaux autorisés dédiés (`--admin-allow`), journal d'audit |
| Dépôts APT locaux | Index signés GPG (RSA 4096), clé générée sur le serveur ; paquets et index isolés par distribution/version ; noms de dépôts validés ; un même paquet/version ne peut pas être remplacé par un contenu différent |
| Dépôt Flatpak local | Commits et résumé signés GPG ; Flathub reste vérifié avec sa propre clé |
| Logiciels Windows | SHA-256 de chaque installeur vérifié par le script avant exécution ; commandes recommandées en HTTPS |
| Cache Windows Update | Ne relaie que les domaines de sa liste (HTTP et SNI) ; HTTPS relayé sans déchiffrement ; seuls les fichiers immuables sont mis en cache |
| apt-cacher-ng | Clients filtrés (tcpwrappers), pages de maintenance protégées par mot de passe |
| Portail (processus) | Utilisateur dédié sans shell, unité systemd confinée (système en lecture seule, aucune capacité, appels système filtrés, `systemd-analyze security` ≈ 1,5) ; nginx seul exposé |
| Secrets | Aucun mot de passe en clair sur disque ; clés privées en 0600 ; sauvegardes en 0600 |

## Limites connues

- Le port HTTP d'amorçage (8088) sert scripts et installeurs sans chiffrement : un attaquant sur le réseau
  pourrait modifier un script. Déployez l'autorité de certification puis utilisez `--no-http`.
- `setup.sh --trust-ca` fait confiance à l'autorité reçue au premier contact (comparer l'empreinte affichée).
- Pas d'authentification annuaire (LDAP/AD) ni de double authentification à ce jour.
- Les postes accèdent aux dépôts sans authentification ; la restriction se fait par réseau (`--allow`).
- L'isolation APT évite de mélanger les dépôts ; elle ne prouve pas qu'un `.deb` est compatible avec
  sa destination. L'administrateur doit valider les dépendances et l'origine des paquets avant l'import
  ou la copie. Le dépôt historique et `setup.sh --legacy` restent partagés pour la transition.
