"""Clé de signature des dépôts sur un serveur Windows (sans GnuPG) : voir openpgp.py.
Sous Linux, la clé reste dans le trousseau GnuPG ($STATE/gnupg)."""

import os
import socket

from . import config

KEY_PATH = os.path.join(config.STATE, "signing-key.json")


def ensure_key():
    """Crée la clé (RSA 4096) au premier lancement ; renvoie True si elle vient d'être créée."""
    if os.path.exists(KEY_PATH):
        return False
    from .openpgp import Key
    host = socket.gethostname()
    Key.generate(f"loco ({host}) <loco@{host}>").save(KEY_PATH)
    return True


def key():
    from .openpgp import Key
    return Key.load(KEY_PATH)


def public_key(armored=False):
    from .openpgp import armor
    data = key().public_key()
    return armor("PUBLIC KEY BLOCK", data).encode() if armored else data


def sign_release(directory):
    """InRelease (signé en clair) et Release.gpg (signature détachée) à côté de Release."""
    from .openpgp import armor
    k = key()
    with open(os.path.join(directory, "Release"), "rb") as f:
        release = f.read()
    with open(os.path.join(directory, "InRelease"), "w", encoding="utf-8", newline="\n") as f:
        f.write(k.clearsign(release.decode("utf-8")))
    with open(os.path.join(directory, "Release.gpg"), "w", encoding="ascii", newline="\n") as f:
        f.write(armor("SIGNATURE", k.detach_sign(release)))


def detach_sign_armored(path, output):
    from .openpgp import armor
    with open(path, "rb") as f:
        signature = key().detach_sign(f.read())
    with open(output, "w", encoding="ascii", newline="\n") as f:
        f.write(armor("SIGNATURE", signature))
