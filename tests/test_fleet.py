"""Parc de postes : inscription de l'agent, identité indépendante de l'IP, voyants, actions, catalogue winget."""

import base64
import hashlib
import os
import re
import tempfile
import unittest
from unittest import mock

import test_portal as fixture
from loco import config, fleet, winget
from loco.db import transaction

WINDOWS = {"hostname": "pc-compta", "agent_version": config.VERSION, "addresses": ["192.168.43.50"],
           "os": {"family": "windows", "name": "Windows 11 Professionnel (10.0.26100)"}}
LINUX = {"hostname": "srv-web", "agent_version": config.VERSION, "addresses": ["192.168.43.60"],
         "os": {"family": "debian", "name": "Debian GNU/Linux 13 (trixie)"}}


class Agent:
    """Agent simulé : appels JSON de loco-client, depuis une adresse IP choisie."""

    def __init__(self, ident, ip, uid=None):
        self.c = fixture.loco.app.test_client()
        self.ident, self.ip = dict(ident), ip
        self.uid = uid or ("%032x" % abs(hash((ident["hostname"], ip))))[:32]
        self.token = None

    def post(self, path, data, token=None):
        headers = {"X-Forwarded-For": self.ip}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.c.post(path, json=data, headers=headers)

    def enroll(self, key=None):
        r = self.post("/agent/api/enroll", dict(self.ident, uid=self.uid, key=key or self.key()))
        if r.status_code == 200:
            self.token = r.get_json()["token"]
        return r

    def checkin(self, inventory=None):
        data = dict(self.ident)
        if inventory is not None:
            data["inventory"] = inventory
        return self.post("/agent/api/checkin", data, self.token)

    def result(self, cmd_id, state, output="", inventory=None):
        data = {"id": cmd_id, "state": state, "output": output, "exit_code": 0 if state == "success" else 1}
        if inventory is not None:
            data["inventory"] = inventory
        return self.post("/agent/api/result", data, self.token)

    @staticmethod
    def key():
        with fixture.loco.app.test_request_context():
            key = fleet.enroll_key()
        return key


def device(uid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM devices WHERE uid=?", (uid,)).fetchone()


def status_of(uid):
    with fixture.loco.app.test_request_context():
        from loco.db import db
        return fleet.status(db().execute("SELECT * FROM devices WHERE uid=?", (uid,)).fetchone())


class FleetTest(unittest.TestCase):
    def setUp(self):
        with transaction() as conn:
            conn.execute("DELETE FROM devices")
            conn.execute("DELETE FROM win_apps")
        fixture.reset_failures()
        self.admin = fixture.Browser()
        self.admin.login("admin", fixture.ADMIN_PW)


class TestEnrollment(FleetTest):
    def test_wrong_key_is_refused(self):
        a = Agent(WINDOWS, "192.168.43.50")
        self.assertEqual(a.enroll(key="mauvaise-cle").status_code, 403)
        self.assertIsNone(device(a.uid))

    def test_device_keeps_its_identity_when_its_address_changes(self):
        a = Agent(WINDOWS, "192.168.43.50")
        self.assertEqual(a.enroll().status_code, 200)
        self.assertEqual(device(a.uid)["ip"], "192.168.43.50")
        a.ip = "10.20.0.7"                      # nouveau bail DHCP, autre réseau
        r = a.checkin()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["interval"], fleet.CHECKIN)
        self.assertEqual(device(a.uid)["ip"], "10.20.0.7")
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0], 1)

    def test_tokens_are_checked_and_stored_hashed(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        self.assertNotIn(a.token.split(".", 1)[1], device(a.uid)["token_hash"])
        good = a.token
        a.token = good[:-2] + "xx"
        self.assertEqual(a.checkin().status_code, 401)
        a.token = "0" * 32 + "." + good.split(".", 1)[1]
        self.assertEqual(a.checkin().status_code, 401)
        a.token = None
        self.assertEqual(a.checkin().status_code, 401)

    def test_reinstalling_the_agent_keeps_the_device_and_rotates_the_token(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        old = a.token
        a.enroll()
        self.assertNotEqual(old, a.token)
        b = Agent(WINDOWS, "192.168.43.50", uid=a.uid)
        b.token = old
        self.assertEqual(b.checkin().status_code, 401)
        self.assertEqual(a.checkin().status_code, 200)

    def test_rotating_the_key_keeps_enrolled_devices(self):
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        old_key = Agent.key()
        self.admin.post("/parc/cle", page="/parc")
        self.assertNotEqual(Agent.key(), old_key)
        self.assertEqual(a.checkin().status_code, 200)
        self.assertEqual(Agent(LINUX, "192.168.43.61").enroll(key=old_key).status_code, 403)


class TestStatus(FleetTest):
    def test_lights_follow_updates_security_failures_and_contact(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        self.assertEqual(status_of(a.uid)[0], "grey")                   # premier inventaire attendu
        a.checkin({"updates": [], "reboot_required": False})
        self.assertEqual(status_of(a.uid), ("green", "à jour"))
        a.checkin({"updates": [{"name": "curl", "version": "8.14", "security": False}]})
        self.assertEqual(status_of(a.uid)[0], "orange")
        a.checkin({"updates": [{"name": "openssl", "version": "3.5", "security": True}]})
        color, reason = status_of(a.uid)
        self.assertEqual(color, "red")
        self.assertIn("sécurité", reason)
        a.checkin({"updates": [], "reboot_required": True})
        self.assertEqual(status_of(a.uid), ("orange", "redémarrage nécessaire"))
        with transaction() as conn:
            conn.execute("UPDATE devices SET last_seen='2020-01-01 00:00:00' WHERE uid=?", (a.uid,))
        self.assertEqual(status_of(a.uid)[0], "red")

    def test_outdated_catalog_software_turns_windows_devices_orange(self):
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, detect) "
                         "VALUES ('7-zip', '7-Zip', '26.04', 'msi', '/qn', '7z.msi', ?, 'ready', '')", ("a" * 64,))
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [], "apps": [{"name": "7-Zip 24.09 (x64 edition)", "version": "24.09.00.0"}]})
        color, reason = status_of(a.uid)
        self.assertEqual((color, reason), ("orange", "1 logiciel(s) à mettre à jour"))
        a.checkin({"updates": [], "apps": [{"name": "7-Zip 26.04 (x64 edition)", "version": "26.04.00.0"}]})
        self.assertEqual(status_of(a.uid)[0], "green")

    def test_version_comparison_and_name_matching(self):
        self.assertLess(fleet.version_key("24.09"), fleet.version_key("24.10"))
        self.assertEqual(fleet.version_key("1.2"), fleet.version_key("1.2.0.0"))
        self.assertTrue(fleet.matches("7-Zip", "7-Zip 26.04 (x64 edition)"))
        self.assertTrue(fleet.matches("Notepad++", "Notepad++ (64-bit x64)"))
        self.assertTrue(fleet.matches("Mozilla Firefox", "Mozilla Firefox (x64 fr)"))
        self.assertTrue(fleet.matches("Git", "Git"))
        self.assertFalse(fleet.matches("Git", "Git Extensions"))
        self.assertFalse(fleet.matches("VLC", "VLC media player"))
        self.assertTrue(fleet.matches("VLC*", "VLC media player"))


class TestActions(FleetTest):
    def add_app(self):
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, detect) "
                         "VALUES ('notepad', 'Notepad++', '8.9.8', 'exe', '/S', 'npp setup.exe', ?, 'ready', '')",
                         ("b" * 64,))

    def queue(self, uid, action, **form):
        dev = device(uid)
        return self.admin.post(f"/parc/{dev['id']}/action", dict(form, action=action), page=f"/parc/{dev['id']}")

    def test_update_reaches_the_device_with_catalog_files_and_reports_back(self):
        self.add_app()
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        a.checkin({"updates": [{"name": "KB5070000", "security": True}],
                   "apps": [{"name": "Notepad++ (64-bit x64)", "version": "8.6"}]})
        self.queue(a.uid, "update", reboot="1")
        self.queue(a.uid, "update", reboot="1")             # pas de doublon
        cmds = a.checkin().get_json()["commands"]
        self.assertEqual(len(cmds), 1)
        cmd = cmds[0]
        self.assertEqual((cmd["action"], cmd["reboot"]), ("update", True))
        self.assertEqual(cmd["apps"][0]["url"], "/win/files/notepad/npp%20setup.exe")
        self.assertEqual(cmd["apps"][0]["sha256"], "b" * 64)
        self.assertEqual(a.checkin().get_json()["commands"], [])            # transmise une seule fois
        a.result(cmd["id"], "running")
        a.result(cmd["id"], "success", "OK : Notepad++ 8.9.8",
                 {"updates": [], "apps": [{"name": "Notepad++ (64-bit x64)", "version": "8.9.8"}]})
        self.assertEqual(status_of(a.uid)[0], "green")
        page = self.admin.c.get(f"/parc/{device(a.uid)['id']}").get_data(as_text=True)
        self.assertIn("OK : Notepad++ 8.9.8", page)

    def test_update_all_only_targets_devices_with_pending_updates(self):
        late = Agent(LINUX, "192.168.43.60")
        late.enroll()
        late.checkin({"updates": [{"name": "curl", "version": "8.14"}]})
        fine = Agent(WINDOWS, "192.168.43.50")
        fine.enroll()
        fine.checkin({"updates": [], "apps": []})
        self.admin.post("/parc/tout-mettre-a-jour", {}, page="/parc")
        self.assertEqual([c["action"] for c in late.checkin().get_json()["commands"]], ["update"])
        self.assertEqual(fine.checkin().get_json()["commands"], [])

    def test_install_app_requires_a_ready_catalog_entry(self):
        self.add_app()
        a = Agent(WINDOWS, "192.168.43.50")
        a.enroll()
        self.assertEqual(self.queue(a.uid, "install-app", slug="absent").status_code, 400)
        self.queue(a.uid, "install-app", slug="notepad")
        cmd = a.checkin().get_json()["commands"][0]
        self.assertEqual((cmd["action"], cmd["apps"][0]["slug"]), ("install-app", "notepad"))
        linux = Agent(LINUX, "192.168.43.60")
        linux.enroll()
        self.assertEqual(self.queue(linux.uid, "install-app", slug="notepad").status_code, 400)

    def test_uninstall_detaches_the_device_only_when_pushed(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        a.ip = "192.168.43.99"
        self.assertEqual(a.checkin().status_code, 200)       # changer d'adresse ne détache pas
        self.queue(a.uid, "uninstall")
        self.assertEqual(device(a.uid)["state"], "removing")
        self.assertEqual(self.queue(a.uid, "update").status_code, 400)
        cmd = a.checkin().get_json()["commands"][0]
        self.assertEqual(cmd["action"], "uninstall")
        a.result(cmd["id"], "success", "Agent retiré du poste.")
        self.assertEqual(device(a.uid)["state"], "removed")
        self.assertEqual(a.checkin().status_code, 401)
        self.assertNotIn("srv-web", self.admin.c.get("/parc").get_data(as_text=True))

    def test_viewers_see_the_fleet_but_cannot_act(self):
        a = Agent(LINUX, "192.168.43.60")
        a.enroll()
        with transaction() as conn:
            from loco import auth
            if not conn.execute("SELECT 1 FROM users WHERE username='lecteur-parc'").fetchone():
                auth.create_user(conn, "lecteur-parc", "Lecteur-Parc-2026", "viewer", must_change=False)
        viewer = fixture.Browser()
        viewer.login("lecteur-parc", "Lecteur-Parc-2026")
        page = viewer.c.get("/parc").get_data(as_text=True)
        self.assertIn("srv-web", page)
        self.assertNotIn("key=", page)                       # pas de clé d'inscription en lecture seule
        dev = device(a.uid)
        r = viewer.post(f"/parc/{dev['id']}/action", {"action": "reboot"}, page="/parc")
        self.assertEqual(r.status_code, 403)


class TestInstallers(FleetTest):
    def test_linux_installer_needs_the_key_and_embeds_agent_and_authority(self):
        self.assertEqual(self.admin.c.get("/agent/linux.sh?key=faux").status_code, 403)
        key = Agent.key()
        sh = self.admin.c.get(f"/agent/linux.sh?key={key}").get_data(as_text=True)
        self.assertIn(f"--key '{key}'", sh)
        agent = re.search(r"<<'LOCO_CLIENT_EOF'\n(.*)\nLOCO_CLIENT_EOF", sh, re.S).group(1)
        compile(agent, "loco-client.py", "exec")
        self.assertIn(f'VERSION = "{config.VERSION}"', agent)
        self.assertEqual(self.admin.c.get("/agent/files/loco-client.py").status_code, 200)
        self.assertEqual(self.admin.c.get("/agent/files/../loco/config.py").status_code, 404)

    def test_windows_installer_checks_the_msi_and_pins_the_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            msi = os.path.join(tmp, "loco-client.msi")
            with open(msi, "wb") as f:
                f.write(b"MSI de test")
            sha = hashlib.sha256(b"MSI de test").hexdigest()
            with mock.patch.object(fleet, "agent_msi", return_value=(msi, sha)), \
                    mock.patch.object(fleet, "_ca_pem", return_value=CA_PEM):
                self.assertEqual(self.admin.c.get("/agent/windows.ps1?key=faux").status_code, 403)
                ps1 = self.admin.c.get(f"/agent/windows.ps1?key={Agent.key()}").get_data(as_text=True)
                self.assertIn("\r\n", ps1)
                self.assertTrue(ps1.isascii())
                self.assertIn(sha, ps1)
                self.assertIn(f'CA_SHA256="{CA_SHA256}"', ps1)
                self.assertIn("/agent/files/loco-client.msi", ps1)
                r = self.admin.c.get("/agent/files/loco-client.msi")
                self.assertEqual(r.get_data(), b"MSI de test")
                r.close()
                # L'agent installé reçoit l'empreinte du MSI de cette version pour se mettre à jour.
                a = Agent(WINDOWS, "192.168.43.50")
                a.enroll()
                self.assertEqual(a.checkin().get_json()["agent_msi"], {"version": config.VERSION, "sha256": sha})
        with mock.patch.object(fleet, "agent_msi", return_value=(None, "pas d'accès à GitHub")):
            ps1 = self.admin.c.get(f"/agent/windows.ps1?key={Agent.key()}").get_data(as_text=True)
            self.assertIn("pas d''accès à GitHub", ps1)


# Autorité de test (certificat auto-signé quelconque) et son empreinte SHA-256 (DER).
CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBejCCASCgAwIBAgIUDEWTWZCXVQfYC6gXQbFNA40OJSkwCgYIKoZIzj0EAwIw
EjEQMA4GA1UEAwwHbG9jby1jYTAeFw0yNjEwMTYwMDAwMDBaFw0zNjEwMTMwMDAw
MDBaMBIxEDAOBgNVBAMMB2xvY28tY2EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNC
AAQpUqNvLkC9vR3vMV1nH3eK/Kq1Fwq4hb1dS9SbLqRn2v2tE6l3ZpE4VQbCm1Eo
d4tRQYhB0e3m1v0x2s2Xo7mDo1MwUTAdBgNVHQ4EFgQU2t3jCw4i4sUhw9WvYb8z
-----END CERTIFICATE-----"""
CA_SHA256 = hashlib.sha256(base64.b64decode("".join(CA_PEM.splitlines()[1:-1]))).hexdigest()


MANIFEST = """PackageIdentifier: Example.Tool
PackageVersion: 2.5.0
PackageName: Example Tool (fr)
Publisher: "Example \\xA9 Corp"
ShortDescription: Un outil
  sur deux lignes
Tags:
- outil
- exemple
Installers:
- Architecture: x86
  InstallerType: nullsoft
  InstallerUrl: https://example.com/tool-x86.exe
  InstallerSha256: 1111111111111111111111111111111111111111111111111111111111111111
- Architecture: x64
  InstallerType: inno
  Scope: machine
  InstallerUrl: https://example.com/tool%20setup-x64.exe
  InstallerSha256: 2222222222222222222222222222222222222222222222222222222222222222
  InstallerSwitches:
    Custom: /MERGETASKS=!desktopicon
- Architecture: arm64
  InstallerType: wix
  InstallerUrl: https://example.com/tool-arm64.msi
  InstallerSha256: 3333333333333333333333333333333333333333333333333333333333333333
ManifestType: merged
"""


class TestWinget(FleetTest):
    def test_manifest_subset_is_parsed(self):
        doc = winget.parse_yaml(MANIFEST)
        self.assertEqual(doc["Publisher"], "Example © Corp")
        self.assertEqual(doc["ShortDescription"], "Un outil sur deux lignes")
        self.assertEqual(doc["Tags"], ["outil", "exemple"])
        self.assertEqual(doc["Installers"][1]["InstallerSwitches"], {"Custom": "/MERGETASKS=!desktopicon"})

    def test_installer_choice_prefers_x64_machine_and_silent_options(self):
        inst = winget.choose_installer(winget.parse_yaml(MANIFEST))
        self.assertEqual(inst["url"], "https://example.com/tool%20setup-x64.exe")
        self.assertEqual(inst["kind"], "exe")
        self.assertEqual(inst["args"], "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /ALLUSERS /MERGETASKS=!desktopicon")
        no_silent = MANIFEST.replace("InstallerType: inno", "InstallerType: exe").replace("InstallerType: nullsoft", "InstallerType: zip")
        with self.assertRaises(ValueError):
            winget.choose_installer(winget.parse_yaml(no_silent))

    def test_import_creates_a_detectable_entry_and_checks_the_installer_hash(self):
        with mock.patch.object(winget, "manifest", return_value=winget.parse_yaml(MANIFEST)), \
                mock.patch("loco.winapps.start_fetch") as fetch:
            r = self.admin.post("/windows-apps/winget/add", {"id": "Example.Tool"}, page="/windows-apps")
        self.assertEqual(r.status_code, 302)
        with transaction() as conn:
            row = conn.execute("SELECT * FROM win_apps WHERE winget_id='Example.Tool'").fetchone()
        self.assertEqual((row["name"], row["version"], row["detect"], row["kind"]),
                         ("Example Tool (fr)", "2.5.0", "Example Tool", "exe"))
        kwargs = fetch.call_args.kwargs
        self.assertEqual(kwargs["expected_sha256"], "2" * 64)
        self.assertEqual(kwargs["filename"], "tool_setup-x64.exe")


if __name__ == "__main__":
    unittest.main()
