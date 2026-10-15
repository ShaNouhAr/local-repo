"""Signatures OpenPGP (RFC 4880, clés v4 RSA) en Python, avec la bibliothèque cryptography.

Serveur Windows : remplace GnuPG pour la clé des dépôts, la signature des index APT (InRelease, Release.gpg)
et des métadonnées RPM (repomd.xml.asc). Les postes vérifient avec gpgv, sqv ou dnf comme d'habitude."""

import base64
import hashlib
import json
import os
import struct
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

RSA, SHA256 = 1, 8
BINARY, TEXT, POSITIVE_CERT = 0x00, 0x01, 0x13


def _mpi(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return struct.pack(">H", value.bit_length()) + raw


def _packet(tag, body):
    """Paquet au nouveau format (longueur sur 1, 2 ou 5 octets)."""
    n = len(body)
    if n < 192:
        length = bytes([n])
    elif n < 8384:
        n -= 192
        length = bytes([(n >> 8) + 192, n & 0xFF])
    else:
        length = b"\xff" + struct.pack(">I", n)
    return bytes([0xC0 | tag]) + length + body


def _subpacket(kind, data):
    return bytes([len(data) + 1, kind]) + data


def _crc24(data):
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def armor(kind, data):
    b64 = base64.b64encode(data).decode()
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    crc = base64.b64encode(_crc24(data).to_bytes(3, "big")).decode()
    return f"-----BEGIN PGP {kind}-----\n\n" + "\n".join(lines) + f"\n={crc}\n-----END PGP {kind}-----\n"


class Key:
    """Clé de signature : clé privée RSA (PEM) et date de création (elle entre dans l'empreinte)."""

    def __init__(self, private_key, created, uid):
        self.private_key = private_key
        self.created = int(created)
        self.uid = uid
        numbers = private_key.public_key().public_numbers()
        self.public_body = bytes([4]) + struct.pack(">I", self.created) + bytes([RSA]) + _mpi(numbers.n) + _mpi(numbers.e)
        self.fingerprint = hashlib.sha1(b"\x99" + struct.pack(">H", len(self.public_body)) + self.public_body).digest()
        self.key_id = self.fingerprint[-8:]

    @classmethod
    def generate(cls, uid, bits=4096):
        return cls(rsa.generate_private_key(public_exponent=65537, key_size=bits), time.time(), uid)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        key = serialization.load_pem_private_key(data["private_key"].encode(), password=None)
        return cls(key, data["created"], data["uid"])

    def save(self, path):
        pem = self.private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()).decode()
        tmp = path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"created": self.created, "uid": self.uid, "private_key": pem}, f)
        os.replace(tmp, path)

    def _signature(self, sig_type, hashed_prefix, extra_hashed=b""):
        """Paquet de signature v4 : hashed_prefix est la donnée signée (déjà mise en forme)."""
        now = int(time.time())
        hashed = (_subpacket(2, struct.pack(">I", now)) + _subpacket(33, b"\x04" + self.fingerprint) + extra_hashed)
        head = bytes([4, sig_type, RSA, SHA256]) + struct.pack(">H", len(hashed)) + hashed
        digest = hashlib.sha256(hashed_prefix + head + b"\x04\xff" + struct.pack(">I", len(head))).digest()
        signature = self.private_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))
        unhashed = _subpacket(16, self.key_id)
        body = head + struct.pack(">H", len(unhashed)) + unhashed + digest[:2] + _mpi(int.from_bytes(signature, "big"))
        return _packet(2, body)

    def public_key(self):
        """Clé publique transférable (format binaire, comme « gpg --export ») : clé, identité, auto-certification."""
        uid = self.uid.encode("utf-8")
        signed = (b"\x99" + struct.pack(">H", len(self.public_body)) + self.public_body
                  + b"\xb4" + struct.pack(">I", len(uid)) + uid)
        flags = (_subpacket(27, b"\x03")                      # certification et signature
                 + _subpacket(11, bytes([9, 8, 7, 2]))        # AES256, AES192, AES128, 3DES
                 + _subpacket(21, bytes([10, 9, 8]))          # SHA512, SHA384, SHA256
                 + _subpacket(22, bytes([2, 1]))              # zlib, zip
                 + _subpacket(30, b"\x01"))                   # détection de modification
        return _packet(6, self.public_body) + _packet(13, uid) + self._signature(POSITIVE_CERT, signed, flags)

    def detach_sign(self, data):
        """Signature détachée d'un document binaire (comme « gpg --detach-sign »)."""
        return self._signature(BINARY, data)

    def clearsign(self, text):
        """Document signé en clair (comme « gpg --clearsign ») : InRelease."""
        lines = text.replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        canonical = "\r\n".join(line.rstrip(" \t") for line in lines).encode("utf-8")
        signature = armor("SIGNATURE", self._signature(TEXT, canonical))
        body = "\n".join(("- " + line) if line.startswith("-") else line for line in lines)
        return f"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n{body}\n{signature}"
