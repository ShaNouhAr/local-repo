"""Dépôts RPM réels (rpmbuild, createrepo_c, gpg) dans des dossiers temporaires."""

import gzip
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_portal as fixture
from loco import config, rpmrepo
from loco.db import transaction

SPEC = """Name: {name}
Version: 1.0
Release: 1.{dist}
Summary: test loco
License: MIT
BuildArch: noarch
%description
test loco
%install
mkdir -p %{{buildroot}}/usr/share/{name}
echo '{content}' > %{{buildroot}}/usr/share/{name}/content
%files
/usr/share/{name}/content
"""


class TestRpmRepositories(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for command in ("createrepo_c", "rpmbuild", "rpm", "gpg"):
            if shutil.which(command) is None:
                raise RuntimeError("Installez createrepo-c, rpm et gnupg pour les tests RPM.")
        os.makedirs(config.GNUPGHOME, mode=0o700, exist_ok=True)
        keys = subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--list-secret-keys"],
                              capture_output=True, text=True).stdout
        if "sec" not in keys:
            subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--pinentry-mode", "loopback",
                            "--passphrase", "", "--quick-generate-key", "loco tests <tests@invalid>",
                            "ed25519", "sign", "0"], check=True, capture_output=True)

    def setUp(self):
        self.reset()
        fixture.reset_failures()

    def tearDown(self):
        self.reset()

    def reset(self):
        with transaction() as c:
            c.execute("DELETE FROM rpm_repositories")
        shutil.rmtree(config.RPM_DIR, ignore_errors=True)

    def build_rpm(self, dist="el9", content="un", name="lr-rpm-test"):
        top = Path(tempfile.mkdtemp(dir=fixture.TMP))
        spec = top / "test.spec"
        spec.write_text(SPEC.format(name=name, dist=dist, content=content))
        subprocess.run(["rpmbuild", "--define", f"_topdir {top}", "--define", "_build_id_links none",
                        "-bb", str(spec)], check=True, capture_output=True)
        return str(next((top / "RPMS" / "noarch").glob("*.rpm")))

    def admin(self):
        b = fixture.Browser()
        self.assertEqual(b.login("admin", fixture.ADMIN_PW).status_code, 302)
        return b

    def upload(self, browser, repository, path):
        with open(path, "rb") as f:
            return browser.c.post("/rpm/upload", data={"csrf": browser.token("/rpm"), "repository": repository,
                                                      "files": (f, os.path.basename(path))},
                                  content_type="multipart/form-data")

    def test_upload_signs_metadata(self):
        rpmrepo.create_repository("el9")
        b = self.admin()
        self.upload(b, "el9", self.build_rpm("el9"))
        packages = rpmrepo.list_rpms("el9")
        self.assertEqual([(p["name"], p["release"]) for p in packages], [("lr-rpm-test", "1.el9")])
        repodata = Path(config.RPM_DIR) / "el9" / "repodata"
        check = subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--verify",
                                str(repodata / "repomd.xml.asc"), str(repodata / "repomd.xml")],
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertTrue((Path(config.RPM_DIR) / "el9" / "Packages" / "lr-rpm-test-1.0-1.el9.noarch.rpm").is_file())

    def test_wrong_version_and_conflicting_content_rejected(self):
        rpmrepo.create_repository("el9")
        with self.assertRaisesRegex(ValueError, "el8"):
            rpmrepo.add_rpm(self.build_rpm("el8"), "el9")
        rpmrepo.add_rpm(self.build_rpm("el9", "premier"), "el9")
        with self.assertRaisesRegex(ValueError, "contenu différent"):
            rpmrepo.add_rpm(self.build_rpm("el9", "autre"), "el9")
        self.assertEqual(len(rpmrepo.list_rpms("el9")), 1)

    def test_repositories_are_isolated_and_delete_reindexes(self):
        rpmrepo.create_repository("el8")
        rpmrepo.create_repository("el9")
        rpmrepo.add_rpm(self.build_rpm("el9"), "el9")
        self.assertEqual(rpmrepo.list_rpms("el8"), [])
        b = self.admin()
        b.c.post("/rpm/delete", data={"csrf": b.token("/rpm?repository=el9"), "repository": "el9",
                                      "file": "lr-rpm-test-1.0-1.el9.noarch.rpm"})
        self.assertEqual(rpmrepo.list_rpms("el9"), [])
        primary = next((Path(config.RPM_DIR) / "el9" / "repodata").glob("*-primary.xml.gz"))
        self.assertNotIn(b"lr-rpm-test", gzip.decompress(primary.read_bytes()))

    def test_viewer_cannot_change_repositories(self):
        rpmrepo.create_repository("el9")
        with transaction() as c:
            from loco import auth
            auth.create_user(c, "lecteur-rpm", "Lecture-Seule-2026x", "viewer", must_change=False)
        try:
            v = fixture.Browser()
            v.login("lecteur-rpm", "Lecture-Seule-2026x")
            self.assertEqual(v.c.get("/rpm").status_code, 200)
            self.assertEqual(self.upload(v, "el9", self.build_rpm("el9")).status_code, 403)
            self.assertEqual(v.post("/rpm/repositories", {"major": "11"}, page="/rpm").status_code, 403)
        finally:
            with transaction() as c:
                c.execute("DELETE FROM users WHERE username='lecteur-rpm'")

    def test_invalid_repository_names(self):
        for name in ("el0", "el", "el9/../x", "rhel9", "el100"):
            with self.assertRaises(ValueError, msg=name):
                rpmrepo.repository_path(name)

    def test_client_script_follows_scheme(self):
        c = fixture.loco.app.test_client()
        http = c.get("/rpm/setup.sh", base_url="http://repo.test:8088").get_data(as_text=True)
        self.assertIn('BASE="http://repo.test:8088"', http)
        self.assertIn('HTTPS_BASE="https://repo.test:8443"', http)
        self.assertIn("rpmcache/epel", http)


if __name__ == "__main__":
    unittest.main()
