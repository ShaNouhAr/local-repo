"""Métadonnées RPM en Python pur (serveur Windows) comparées à rpm et createrepo_c."""

import gzip
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import test_portal as fixture
from localrepo import config, rpmpkg, rpmrepo
from localrepo.db import transaction

SPEC = """Name: {name}
Version: 2.1
Release: 3.el9
Epoch: {epoch}
Summary: essai <local-repo> & co
License: MIT
URL: https://example.invalid/{name}
BuildArch: noarch
Provides: {name}-capability = 2.1-3
Requires: bash
Requires(pre): coreutils
Recommends: {name}-docs
Conflicts: {name}-old < 2
Obsoletes: {name}-legacy <= 1.0
%description
Description sur
plusieurs lignes.
%install
mkdir -p %{{buildroot}}/usr/bin %{{buildroot}}/etc/{name} %{{buildroot}}/usr/share/{name}
echo 'echo ok' > %{{buildroot}}/usr/bin/{name}
echo 'conf' > %{{buildroot}}/etc/{name}/{name}.conf
echo 'data' > %{{buildroot}}/usr/share/{name}/data
%files
%attr(0755,root,root) /usr/bin/{name}
%dir /etc/{name}
%config(noreplace) /etc/{name}/{name}.conf
%ghost /var/log/{name}.log
/usr/share/{name}
%changelog
* Mon Oct 12 2026 Tests <tests@invalid> - 2:2.1-3
- Version d'essai
"""

NS = {"c": "http://linux.duke.edu/metadata/common", "rpm": "http://linux.duke.edu/metadata/rpm",
      "f": "http://linux.duke.edu/metadata/filelists", "r": "http://linux.duke.edu/metadata/repo"}


def build_rpm(directory, name, epoch=2):
    top = Path(tempfile.mkdtemp(dir=directory))
    spec = top / "test.spec"
    spec.write_text(SPEC.format(name=name, epoch=epoch))
    subprocess.run(["rpmbuild", "--define", f"_topdir {top}", "--define", "_build_id_links none", "-bb", str(spec)],
                   check=True, capture_output=True)
    return str(next((top / "RPMS" / "noarch").glob("*.rpm")))


def metadata(outputdir, kind):
    repomd = ET.parse(os.path.join(outputdir, "repodata", "repomd.xml"))
    href = repomd.find(f"r:data[@type='{kind}']/r:location", NS).get("href")
    with gzip.open(os.path.join(outputdir, href)) as f:
        return ET.parse(f).getroot()


def summary(primary, filelists):
    out = {}
    for pkg in primary.findall("c:package", NS):
        fmt = pkg.find("c:format", NS)
        deps = {}
        for kind in ("provides", "requires", "conflicts", "obsoletes", "recommends"):
            node = fmt.find(f"rpm:{kind}", NS)
            deps[kind] = sorted(tuple(sorted(e.attrib.items())) for e in node) if node is not None else []
        out[pkg.findtext("c:name", namespaces=NS)] = {
            "version": pkg.find("c:version", NS).attrib, "arch": pkg.findtext("c:arch", namespaces=NS),
            "checksum": pkg.findtext("c:checksum", namespaces=NS), "summary": pkg.findtext("c:summary", namespaces=NS),
            "description": pkg.findtext("c:description", namespaces=NS), "url": pkg.findtext("c:url", namespaces=NS),
            "size": {k: v for k, v in pkg.find("c:size", NS).attrib.items()},
            "location": pkg.find("c:location", NS).get("href"),
            "header": fmt.find("rpm:header-range", NS).attrib, "sourcerpm": fmt.findtext("rpm:sourcerpm", namespaces=NS),
            "license": fmt.findtext("rpm:license", namespaces=NS),
            "primary_files": sorted((f.text, f.get("type", "")) for f in fmt.findall("c:file", NS)),
            "deps": deps,
        }
    for pkg in filelists.findall("f:package", NS):
        out[pkg.get("name")]["files"] = sorted((f.text, f.get("type", "")) for f in pkg.findall("f:file", NS))
    return out


class TestRpmMetadata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for command in ("rpmbuild", "createrepo_c", "rpm"):
            if shutil.which(command) is None:
                raise RuntimeError("Installez rpm et createrepo-c pour comparer les métadonnées RPM.")

    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=fixture.TMP)
        self.repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(self.repo, "Packages"))
        for name, epoch in (("lr-meta", 2), ("lr-other", 0)):
            shutil.copy(build_rpm(self.dir, name, epoch), os.path.join(self.repo, "Packages"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_fields_match_rpm(self):
        for path in Path(self.repo, "Packages").glob("*.rpm"):
            out = subprocess.run(["rpm", "-qp", "--nosignature", "--nodigest", "--qf",
                                  "%{NAME}\\n%{VERSION}\\n%{RELEASE}\\n%{ARCH}\\n", str(path)],
                                 capture_output=True, text=True, check=True).stdout.split("\n")
            fields = rpmpkg.rpm_fields(str(path))
            self.assertEqual([fields[k] for k in ("name", "version", "release", "arch")], out[:4])

    def test_repodata_matches_createrepo_c(self):
        ours, theirs = os.path.join(self.dir, "ours"), os.path.join(self.dir, "theirs")
        rpmpkg.createrepo(self.repo, ours)
        os.makedirs(theirs)
        subprocess.run(["createrepo_c", "--quiet", "--no-database", "--general-compress-type=gz", "--checksum=sha256",
                        "--unique-md-filenames", "--outputdir", theirs, self.repo], check=True, capture_output=True)
        a = summary(metadata(ours, "primary"), metadata(ours, "filelists"))
        b = summary(metadata(theirs, "primary"), metadata(theirs, "filelists"))
        self.assertEqual(sorted(a), ["lr-meta", "lr-other"])
        for name in a:
            for key in b[name]:
                self.assertEqual(a[name][key], b[name][key], f"{name} : {key}")
        other = metadata(ours, "other")
        self.assertIn("Version d'essai", "".join(other.itertext()))

    def test_header_reader_does_not_load_the_package_payload(self):
        rpm = build_rpm(self.dir, "lr-bounded")
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
            return BoundedFile(opened) if str(path) == rpm else opened

        with mock.patch("builtins.open", side_effect=bounded_open):
            self.assertEqual(rpmpkg.rpm_fields(rpm)["name"], "lr-bounded")

    def test_invalid_and_source_packages_rejected(self):
        bad = Path(self.dir, "bad.rpm")
        bad.write_bytes(b"\xed\xab\xee\xdb" + b"\0" * 200)
        with self.assertRaises(ValueError):
            rpmpkg.rpm_fields(str(bad))


class TestPortalWithoutRpmTools(unittest.TestCase):
    """Dépôt RPM du portail tel qu'il fonctionne sur un serveur Windows : sans rpm ni createrepo_c."""

    def test_repository_signed_and_listed(self):
        if shutil.which("rpmbuild") is None:
            self.skipTest("rpmbuild requis pour fabriquer le paquet d'essai")
        os.makedirs(config.GNUPGHOME, mode=0o700, exist_ok=True)
        keys = subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--list-secret-keys"],
                              capture_output=True, text=True).stdout
        if "sec" not in keys:
            subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--pinentry-mode", "loopback",
                            "--passphrase", "", "--quick-generate-key", "local-repo tests <tests@invalid>",
                            "ed25519", "sign", "0"], check=True, capture_output=True)
        work = tempfile.mkdtemp(dir=fixture.TMP)
        try:
            with mock.patch.object(rpmrepo, "RPM_TOOLS", False):
                with transaction() as c:
                    c.execute("DELETE FROM rpm_repositories")
                shutil.rmtree(config.RPM_DIR, ignore_errors=True)
                rpmrepo.create_repository("el9")
                tmp = os.path.join(config.TMP_DIR, "windows-test.rpm")
                os.makedirs(config.TMP_DIR, exist_ok=True)
                shutil.copy(build_rpm(work, "lr-windows"), tmp)
                rpmrepo.add_rpm(tmp, "el9")
                self.assertEqual([(p["name"], p["release"]) for p in rpmrepo.list_rpms("el9")],
                                 [("lr-windows", "3.el9")])
            repodata = Path(config.RPM_DIR, "el9", "repodata")
            check = subprocess.run(["gpg", "--homedir", config.GNUPGHOME, "--batch", "--verify",
                                    str(repodata / "repomd.xml.asc"), str(repodata / "repomd.xml")],
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
        finally:
            with transaction() as c:
                c.execute("DELETE FROM rpm_repositories")
            shutil.rmtree(config.RPM_DIR, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
