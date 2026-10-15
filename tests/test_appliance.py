"""Appliance Alpine : réglages, mise à jour vérifiée avec retour arrière, demandes de l'agent, pages du portail."""

import contextlib
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from unittest import mock

import test_portal as fixture
import locoadmin
from loco import appliance, config


class TempAgent(unittest.TestCase):
    """Dossiers de l'agent, sources et loco.conf dans un dossier temporaire."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="lr-appliance-")
        root = self.work.name
        self.conf = os.path.join(root, "loco.conf")
        with open(self.conf, "w") as f:
            f.write('# Généré\nALLOW="10.0.0.0/8 192.168.0.0/16"\nADMIN_ALLOW="192.168.43.0/24"\nHTTP_ENABLED=1\n'
                    'UPSTREAM_PROXY=""\nWU_ENABLED=1\nRPM_ENABLED=1\nFLATPAK_ENABLED=1\nAPT_CACHE_SIZE=20g\n'
                    'WU_CACHE_SIZE=\nRPM_CACHE_SIZE=\nFLATPAK_CACHE_SIZE=\nMAX_UPLOAD=8g\nBACKUP_KEEP=3\n'
                    'RESOLVERS="1.1.1.1 9.9.9.9"\n'
                    f"SOURCE_DIR={root}/src/loco-2.0.0\n")
        agent = os.path.join(root, "agent")
        sources = os.path.join(root, "src")
        os.makedirs(os.path.join(sources, "loco-2.0.0", "linux"))
        with open(os.path.join(sources, "loco-2.0.0", "linux", "install.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        for path in (os.path.join(agent, "requests"), os.path.join(agent, "jobs")):
            os.makedirs(path)
        values = {"CONF": self.conf, "AGENT_DIR": agent, "REQUESTS": os.path.join(agent, "requests"),
                  "JOBS": os.path.join(agent, "jobs"), "STATUS": os.path.join(agent, "status.json"),
                  "LOCK": os.path.join(agent, "lock"), "HEARTBEAT": os.path.join(agent, "alive"),
                  "SOURCES": sources, "STATE": os.path.join(root, "state")}
        for name, value in values.items():
            patcher = mock.patch.object(locoadmin, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sources = sources

    def tearDown(self):
        self.work.cleanup()


class TestSettings(TempAgent):
    def test_configuration_is_read_like_the_shell(self):
        conf = locoadmin.read_conf()
        self.assertEqual(conf["ALLOW"], "10.0.0.0/8 192.168.0.0/16")
        self.assertEqual(conf["UPSTREAM_PROXY"], "")
        self.assertEqual(conf["WU_CACHE_SIZE"], "")

    def test_only_real_changes_are_kept_and_normalised(self):
        clean = locoadmin.validate({"ALLOW": "10.0.0.0/8  192.168.0.0/16", "ADMIN_ALLOW": "192.168.43.7/24",
                                    "HTTP_ENABLED": "1", "WU_CACHE_SIZE": "50g"})
        self.assertEqual(clean, {"WU_CACHE_SIZE": "50g"})    # 192.168.43.7/24 : même réseau qu'actuellement

    def test_values_that_could_reach_the_shell_are_refused(self):
        for key, value in (("UPSTREAM_PROXY", "http://$(reboot):3128"), ("UPSTREAM_PROXY", "http://a`id`:1"),
                           ("UPSTREAM_PROXY", 'http://a" b'), ("ALLOW", "10.0.0.0/8; reboot"),
                           ("RESOLVERS", "1.1.1.1 $(id)"), ("RESOLVERS", "fe80::%$(id)"),
                           ("ALLOW", "fe80::%$(id)/64"), ("ADMIN_ALLOW", "fe80::%eth0/64"), ("MAX_UPLOAD", "8t"), ("BACKUP_KEEP", "0"),
                           ("HTTP_ENABLED", "yes"), ("DATA", "/tmp"), ("WU_CACHE_SIZE", "50 g")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                locoadmin.validate({key: value})
        self.assertEqual(locoadmin.validate({"UPSTREAM_PROXY": "http://user:p%40ss@proxy.lan:3128"}),
                         {"UPSTREAM_PROXY": "http://user:p%40ss@proxy.lan:3128"})

    def test_installer_options_follow_the_settings(self):
        args = []
        for key, value in {"HTTP_ENABLED": "0", "UPSTREAM_PROXY": "", "RPM_ENABLED": "1", "BACKUP_KEEP": "5"}.items():
            args += locoadmin.SETTINGS[key][2](value)
        self.assertEqual(args, ["--no-http", "--no-proxy", "--rpm", "--backup-keep", "5"])


def _archive(version, files=None, extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (files or {f"loco-{version}/linux/install.sh": b"#!/bin/bash\n"}).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra:
            tar.addfile(extra)
    return buf.getvalue()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class TestUpdateLoco(TempAgent):
    def release(self, archive, digest=None):
        digest = digest or hashlib.sha256(archive).hexdigest()
        latest = {"version": "2.1.0", "url": "", "published": "", "notes": "",
                  "archive": "https://example.test/loco-2.1.0.tar.gz", "sums": "https://example.test/SHA256SUMS"}
        files = {latest["archive"]: archive, latest["sums"]: f"{digest}  loco-2.1.0.tar.gz\n".encode()}
        return (mock.patch.object(locoadmin, "latest_release", return_value=latest),
                mock.patch.object(locoadmin, "_get", lambda url, timeout=30: FakeResponse(files[url])),
                mock.patch.object(locoadmin, "current_version", return_value="2.0.0"))

    def run_update(self, archive, digest=None, install_codes=(0,)):
        codes = list(install_codes)
        calls = []

        def install(source, args, log):
            calls.append(os.path.basename(source))
            return codes.pop(0)
        p1, p2, p3 = self.release(archive, digest)
        with p1, p2, p3, mock.patch.object(locoadmin, "_install", install), mock.patch.object(locoadmin, "_run", return_value=0):
            ok = locoadmin.update_loco(log=lambda _t: None)
        return ok, calls

    def test_verified_archive_is_installed(self):
        ok, calls = self.run_update(_archive("2.1.0"))
        self.assertTrue(ok)
        self.assertEqual(calls, ["loco-2.1.0"])
        self.assertTrue(os.path.isfile(os.path.join(self.sources, "loco-2.1.0", "linux", "install.sh")))

    def test_wrong_checksum_is_refused_before_anything_is_installed(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.run_update(_archive("2.1.0"), digest="0" * 64)
        self.assertFalse(os.path.exists(os.path.join(self.sources, "loco-2.1.0")))

    def test_failed_installation_rolls_back_to_the_previous_sources(self):
        with open(os.path.join(self.sources, "loco-2.0.0", "linux", "install.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        ok, calls = self.run_update(_archive("2.1.0"), install_codes=(1, 0))
        self.assertFalse(ok)
        self.assertEqual(calls, ["loco-2.1.0", "loco-2.0.0"])

    def test_failed_update_restores_configuration_and_database(self):
        import sqlite3
        os.makedirs(locoadmin.STATE)
        db = os.path.join(locoadmin.STATE, "portal.db")
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE original(value)")
            conn.execute("INSERT INTO original VALUES ('conservé')")
            conn.commit()
        with open(self.conf) as f:
            original = f.read()
        calls = []

        def install(source, args, log):
            calls.append(source)
            if len(calls) == 1:
                with open(self.conf, "w") as f:
                    f.write("SOURCE_DIR=/bad\n")
                with contextlib.closing(sqlite3.connect(db)) as conn:
                    conn.execute("DROP TABLE original")
                    conn.commit()
                return 1
            with open(self.conf) as f:
                self.assertEqual(f.read(), original)
            with contextlib.closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT value FROM original").fetchone()[0], "conservé")
            return 0

        p1, p2, p3 = self.release(_archive("2.1.0"))
        with p1, p2, p3, mock.patch.object(locoadmin, "_install", install), mock.patch.object(locoadmin, "_run", return_value=0):
            self.assertFalse(locoadmin.update_loco(log=lambda _: None))
        self.assertEqual(len(calls), 2)

    def test_archives_with_links_or_escaping_paths_are_refused(self):
        link = tarfile.TarInfo("loco-2.1.0/linux/evil")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/shadow"
        for archive in (_archive("2.1.0", extra=link),
                        _archive("2.1.0", files={"loco-2.1.0/../../etc/cron.d/x": b"x"}),
                        _archive("2.1.0", files={"autre/linux/install.sh": b"x"})):
            with self.subTest(), self.assertRaisesRegex(ValueError, "archive refusée"):
                self.run_update(archive)


class TestRequests(TempAgent):
    def test_portal_request_is_revalidated_and_bounded(self):
        locoadmin.request("configure", {"WU_CACHE_SIZE": "40g"}, user="admin")
        names = os.listdir(locoadmin.REQUESTS)
        self.assertEqual(len(names), 1)
        req = locoadmin._read_request(os.path.join(locoadmin.REQUESTS, names[0]))
        self.assertEqual(req, {"action": "configure", "params": {"WU_CACHE_SIZE": "40g"}, "user": "admin"})
        with self.assertRaises(ValueError):
            locoadmin.request("configure", {"WU_CACHE_SIZE": "$(id)"})
        with self.assertRaises(ValueError):
            locoadmin.request("shell", {})

    def test_links_and_special_files_are_not_followed(self):
        secret = os.path.join(self.work.name, "secret.json")
        with open(secret, "w") as f:
            json.dump({"action": "reboot"}, f)
        link = os.path.join(locoadmin.REQUESTS, "x.json")
        os.symlink(secret, link)
        self.assertIsNone(locoadmin._read_request(link))
        fifo = os.path.join(locoadmin.REQUESTS, "y.json")
        os.mkfifo(fifo)
        self.assertIsNone(locoadmin._read_request(fifo))
        big = os.path.join(locoadmin.REQUESTS, "z.json")
        with open(big, "w") as f:
            f.write(" " * (locoadmin.MAX_REQUEST + 1))
        self.assertIsNone(locoadmin._read_request(big))

    def test_job_log_rejects_paths(self):
        self.assertEqual(locoadmin.job_log("../status"), "")


class TestPages(TempAgent):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(config, "APPLIANCE", True),
                        mock.patch.object(appliance, "locoadmin", locoadmin, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        with open(locoadmin.HEARTBEAT, "w") as f:
            f.write("vivant")
        fixture.reset_failures()
        self.b = fixture.Browser()
        self.b.login("admin", fixture.ADMIN_PW)

    def test_pages_show_versions_and_settings(self):
        with open(locoadmin.STATUS, "w") as f:
            json.dump({"checked": "2026-10-15 09:00:00", "latest": {"version": "9.0.0", "url": "", "published": ""},
                       "system_updates": ["musl-1.2.6-r1"], "errors": []}, f)
        html = self.b.c.get("/systeme/mises-a-jour").get_data(as_text=True)
        self.assertIn("Installer 9.0.0", html)
        self.assertIn("musl-1.2.6-r1", html)
        html = self.b.c.get("/systeme/configuration").get_data(as_text=True)
        self.assertIn('value="10.0.0.0/8 192.168.0.0/16"', html)

    def test_settings_are_sent_to_the_agent(self):
        form = {k: v for k, v in locoadmin.read_conf().items() if k in locoadmin.SETTINGS}
        form.update(WU_CACHE_SIZE="60g", ADMIN_ALLOW="127.0.0.0/8 192.168.43.0/24")
        r = self.b.post("/systeme/configuration", form, page="/systeme/configuration")
        self.assertEqual(r.status_code, 302)
        names = os.listdir(locoadmin.REQUESTS)
        self.assertEqual(len(names), 1)
        req = locoadmin._read_request(os.path.join(locoadmin.REQUESTS, names[0]))
        self.assertEqual(req["params"], {"WU_CACHE_SIZE": "60g", "ADMIN_ALLOW": "127.0.0.0/8 192.168.43.0/24"})

    def test_admin_cannot_lock_themself_out(self):
        form = {k: v for k, v in locoadmin.read_conf().items() if k in locoadmin.SETTINGS}
        form.update(ADMIN_ALLOW="10.99.0.0/16", csrf=self.b.token("/systeme/configuration"))
        self.b.c.post("/systeme/configuration", data=form, headers={"X-Forwarded-For": "192.168.43.5"})
        self.assertEqual(os.listdir(locoadmin.REQUESTS), [])
        form["ADMIN_ALLOW"] = "192.168.43.0/24 10.99.0.0/16"
        self.b.c.post("/systeme/configuration", data=form, headers={"X-Forwarded-For": "192.168.43.5"})
        self.assertEqual(len(os.listdir(locoadmin.REQUESTS)), 1)

    def test_new_operations_require_admin_and_csrf(self):
        for path in ("/systeme/configuration", "/systeme/mises-a-jour"):
            self.assertEqual(self.b.c.post(path, data={"action": "reboot"}).status_code, 400)
        with fixture.connect() as conn:
            conn.execute("DELETE FROM users WHERE username='appliance-reader'")
            fixture.auth.create_user(conn, "appliance-reader", fixture.ADMIN_PW, role="viewer", must_change=False)
        viewer = fixture.Browser()
        viewer.login("appliance-reader", fixture.ADMIN_PW)
        for path in ("/systeme/configuration", "/systeme/mises-a-jour", "/systeme/operations/etat"):
            self.assertEqual(viewer.c.get(path).status_code, 403)
        self.assertEqual(os.listdir(locoadmin.REQUESTS), [])

    def test_viewers_and_other_runtimes_have_no_access(self):
        with mock.patch.object(config, "APPLIANCE", False):
            self.assertEqual(self.b.c.get("/systeme/mises-a-jour").status_code, 404)
        anonymous = fixture.Browser()
        self.assertEqual(anonymous.c.get("/systeme/configuration").status_code, 302)


if __name__ == "__main__":
    unittest.main()
