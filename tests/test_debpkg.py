"""Index APT en Python pur (serveur Windows) comparés à apt-ftparchive et dpkg-deb."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_portal as fixture
from localrepo import config, debpkg, linux


def build_deb(directory, name, version, compression, description="essai\n multi-ligne\n .\n fin"):
    root = Path(directory) / f"{name}-{version}-{compression}"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "usr/share" / name).mkdir(parents=True)
    (root / "DEBIAN/control").write_text(
        f"Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: Tests <tests@invalid>\n"
        f"Depends: libc6 (>= 2.31)\nSection: misc\nPriority: optional\nDescription: {description}\n")
    (root / "usr/share" / name / "content").write_text(version)
    dest = Path(directory) / f"{name}_{version}_all.deb"
    subprocess.run(["dpkg-deb", f"-Z{compression}", "--root-owner-group", "-b", str(root), str(dest)],
                   check=True, capture_output=True)
    return str(dest)


def stanzas(data):
    return [debpkg.parse_stanza(block) for block in data.decode().strip().split("\n\n")]


class TestDebPackages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for command in (linux.APT_FTPARCHIVE, "dpkg-deb"):
            if shutil.which(command) is None:
                raise RuntimeError("Installez apt-utils pour comparer les index APT.")

    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=fixture.TMP)
        os.makedirs(os.path.join(self.dir, "pool"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_control_fields_match_dpkg(self):
        for compression in ("gzip", "xz", "none"):
            deb = build_deb(self.dir, "lr-deb", "1:2.0-1", compression)
            fields = debpkg.deb_fields(deb)
            out = subprocess.run(["dpkg-deb", "-f", deb, "Package", "Version", "Depends"],
                                 capture_output=True, text=True, check=True).stdout
            expected = dict(line.split(": ", 1) for line in out.strip().splitlines())
            for key, value in expected.items():
                self.assertEqual(fields[key], value, (compression, key))
            os.unlink(deb)

    def test_packages_index_matches_apt_ftparchive(self):
        build_deb(os.path.join(self.dir, "pool"), "lr-a", "1.0", "xz")
        build_deb(os.path.join(self.dir, "pool"), "lr-b", "2.0+dfsg-1", "gzip")
        ours = stanzas(debpkg.packages_index(self.dir))
        theirs = stanzas(subprocess.run([linux.APT_FTPARCHIVE, "packages", "pool"], cwd=self.dir,
                                        capture_output=True, check=True).stdout)
        self.assertEqual(len(ours), 2)
        self.assertEqual(sorted(ours, key=lambda s: s["Package"]), sorted(theirs, key=lambda s: s["Package"]))

    def test_release_lists_index_digests(self):
        Path(self.dir, "Packages").write_text("Package: x\n")
        debpkg.gzip_file(os.path.join(self.dir, "Packages"))
        first = Path(self.dir, "Packages.gz").read_bytes()
        debpkg.gzip_file(os.path.join(self.dir, "Packages"))
        self.assertEqual(first, Path(self.dir, "Packages.gz").read_bytes())   # reproductible (gzip -n)
        release = debpkg.release_file(self.dir, "local-repo", "local-repo debian/trixie", "trixie", "trixie").decode()
        self.assertIn("Codename: trixie\n", release)
        import hashlib
        digest = hashlib.sha256(first).hexdigest()
        self.assertRegex(release, rf"SHA256:\n(?: .*\n)*? {digest} +{len(first)} Packages\.gz\n")

    def test_control_reader_does_not_load_the_package_payload(self):
        deb = build_deb(self.dir, "lr-bounded", "1.0", "xz")
        real_open = open

        class BoundedFile:
            def __init__(self, wrapped): self.wrapped = wrapped
            def __enter__(self): return self
            def __exit__(self, *args): self.wrapped.close()
            def __getattr__(self, name): return getattr(self.wrapped, name)
            def read(self, size=-1):
                if size < 0:
                    raise AssertionError("lecture non bornée du paquet")
                return self.wrapped.read(size)

        def bounded_open(path, *args, **kwargs):
            opened = real_open(path, *args, **kwargs)
            return BoundedFile(opened) if str(path) == deb else opened

        with mock.patch("builtins.open", side_effect=bounded_open):
            self.assertEqual(debpkg.deb_fields(deb)["Package"], "lr-bounded")

    def test_invalid_files_rejected(self):
        bad = Path(self.dir, "bad.deb")
        bad.write_bytes(b"not an archive")
        with self.assertRaises(ValueError):
            debpkg.deb_fields(str(bad))


class TestPortalWithoutDebianTools(unittest.TestCase):
    """Dépôt APT du portail tel qu'il fonctionne sur un serveur Windows : sans apt-ftparchive ni dpkg-deb."""

    def test_upload_builds_signed_indexes(self):
        if shutil.which("dpkg-deb") is None:
            self.skipTest("dpkg-deb requis pour fabriquer le paquet d'essai")
        os.makedirs(config.GNUPGHOME, mode=0o700, exist_ok=True)
        keys = subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--list-secret-keys"],
                              capture_output=True, text=True).stdout
        if "sec" not in keys:
            subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--pinentry-mode", "loopback",
                            "--passphrase", "", "--quick-generate-key", "local-repo tests <tests@invalid>",
                            "ed25519", "sign", "0"], check=True, capture_output=True)
        work = tempfile.mkdtemp(dir=fixture.TMP)
        deb = build_deb(work, "lr-windows", "3.1", "xz")
        try:
            with mock.patch.object(linux, "DEBIAN_TOOLS", False):
                shutil.rmtree(config.POOL_DIR, ignore_errors=True)
                linux.rebuild_apt()
                os.makedirs(config.POOL_DIR, exist_ok=True)
                tmp = os.path.join(config.TMP_DIR, "windows-test.deb")
                os.makedirs(config.TMP_DIR, exist_ok=True)
                shutil.copy(deb, tmp)
                self.assertEqual(linux.add_deb(tmp, linux.LEGACY), "lr-windows_3.1_all.deb")
            packages = stanzas(Path(config.APT_DIR, "Packages").read_bytes())
            self.assertEqual([(p["Package"], p["Filename"]) for p in packages],
                             [("lr-windows", "pool/lr-windows_3.1_all.deb")])
            check = subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--verify",
                                    os.path.join(config.APT_DIR, "InRelease")], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn("Packages.gz", Path(config.APT_DIR, "Release").read_text())
        finally:
            shutil.rmtree(config.POOL_DIR, ignore_errors=True)
            linux.rebuild_apt()
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
