"""Signatures OpenPGP en Python (serveur Windows), vérifiées par gpg, gpgv et sqv (apt de Debian 13)."""

import os
import shutil
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portal"))
try:
    from localrepo import openpgp  # noqa: E402
except ImportError:   # cryptography absente : module propre au serveur Windows
    openpgp = None

TEXT = ("Origin: local-repo\nLabel: local-repo debian/trixie\nSuite: trixie\n"
        "- ligne commençant par un tiret\n-----BEGIN PGP piège\nespaces en fin de ligne   \n\n"
        "SHA256:\n 0123456789abcdef 1234 Packages\n")


class TestOpenPGP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if openpgp is None:
            raise unittest.SkipTest("bibliothèque cryptography absente (signatures du serveur Windows)")
        for command in ("gpg", "gpgv"):
            if shutil.which(command) is None:
                raise RuntimeError("Installez gnupg (gpg, gpgv) pour vérifier les signatures.")
        cls.work = tempfile.mkdtemp(prefix="lr-openpgp-")
        cls.key = openpgp.Key.generate("local-repo (tests) <localrepo@tests>", bits=3072)
        cls.path = os.path.join(cls.work, "key.json")
        cls.key.save(cls.path)
        cls.keyring = os.path.join(cls.work, "local-repo.gpg")
        Path(cls.keyring).write_bytes(openpgp.Key.load(cls.path).public_key())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def gpg(self, *args, home):
        return subprocess.run(["gpg", "--homedir", home, "--batch", *args], capture_output=True, text=True)

    def test_public_key_imports_with_valid_self_signature(self):
        home = tempfile.mkdtemp(dir=self.work)
        os.chmod(home, 0o700)
        self.assertEqual(self.gpg("--import", self.keyring, home=home).returncode, 0)
        listing = self.gpg("--with-colons", "--check-signatures", home=home).stdout
        fpr = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))
        self.assertEqual(fpr, self.key.fingerprint.hex().upper())
        self.assertRegex(listing, r"(?m)^sig:!:")          # auto-certification vérifiée
        self.assertIn("local-repo (tests) <localrepo@tests>", listing)
        armored = openpgp.armor("PUBLIC KEY BLOCK", self.key.public_key())
        other = tempfile.mkdtemp(dir=self.work)
        os.chmod(other, 0o700)
        Path(other, "k.asc").write_text(armored)
        self.assertEqual(self.gpg("--import", os.path.join(other, "k.asc"), home=other).returncode, 0)

    def test_detached_and_clear_signatures_verify(self):
        data = Path(self.work, "Release")
        data.write_text(TEXT)
        Path(self.work, "Release.gpg").write_text(openpgp.armor("SIGNATURE", self.key.detach_sign(data.read_bytes())))
        Path(self.work, "Release.sig").write_bytes(self.key.detach_sign(data.read_bytes()))
        Path(self.work, "InRelease").write_text(self.key.clearsign(TEXT))
        for sig in ("Release.gpg", "Release.sig"):
            out = subprocess.run(["gpgv", "--keyring", self.keyring, os.path.join(self.work, sig), str(data)],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
        out = subprocess.run(["gpgv", "--keyring", self.keyring, "--output", "-", os.path.join(self.work, "InRelease")],
                             capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.decode().rstrip("\n").split("\n"),
                         [line.rstrip(" ") for line in TEXT.rstrip("\n").split("\n")])
        if shutil.which("sqv"):
            sqv = subprocess.run(["sqv", f"--keyring={self.keyring}", f"--signature-file={self.work}/Release.gpg",
                                  str(data)], capture_output=True, text=True)
            self.assertEqual(sqv.returncode, 0, sqv.stderr)
            clear = subprocess.run(["sqv", f"--keyring={self.keyring}", "--cleartext",
                                    f"--output={self.work}/clear.out", f"{self.work}/InRelease"],
                                   capture_output=True, text=True)
            self.assertEqual(clear.returncode, 0, clear.stderr)

    def test_tampered_data_rejected(self):
        data = Path(self.work, "doc")
        data.write_bytes(b"repomd original")
        sig = Path(self.work, "doc.asc")
        sig.write_text(openpgp.armor("SIGNATURE", self.key.detach_sign(data.read_bytes())))
        data.write_bytes(b"repomd modifie")
        out = subprocess.run(["gpgv", "--keyring", self.keyring, str(sig), str(data)], capture_output=True)
        self.assertNotEqual(out.returncode, 0)
        inrelease = Path(self.work, "InRelease-bad")
        inrelease.write_text(self.key.clearsign(TEXT).replace("Suite: trixie", "Suite: sid"))
        out = subprocess.run(["gpgv", "--keyring", self.keyring, str(inrelease)], capture_output=True)
        self.assertNotEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
