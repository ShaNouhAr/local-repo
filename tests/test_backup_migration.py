"""Les sauvegardes Linux historiques restaurent données et clés dans les nouveaux chemins."""
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

import test_portal  # initialise le portail isolé et son chemin d'import
from loco.backup import restore_linux


class TestBackupMigration(unittest.TestCase):
    def test_restore_old_archive_to_new_paths_keeps_current_configuration(self):
        with tempfile.TemporaryDirectory(dir=test_portal.TMP) as work:
            root = Path(work)
            etc, data, state = (root / name for name in ('etc', 'data', 'state'))
            etc.mkdir()
            config = etc / 'loco.conf'
            config.write_text('current machine')
            archive = root / 'old.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name, content in [('etc/local-repo/local-repo.conf', b'DATA=/srv/custom\n'),
                                      ('etc/local-repo/portal.secret', b'original secret'),
                                      ('var/lib/local-repo/gnupg/private-key', b'original key'),
                                      ('srv/custom/apt/pool/package.deb', b'original package'),
                                      ('portal.db', b'original database')]:
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    tar.addfile(member, io.BytesIO(content))
            restore_linux(archive, data, etc, state)
            self.assertEqual(config.read_text(), 'current machine')
            self.assertEqual((etc / 'portal.secret').read_bytes(), b'original secret')
            self.assertEqual((data / 'apt/pool/package.deb').read_bytes(), b'original package')
            self.assertEqual((state / 'gnupg/private-key').read_bytes(), b'original key')
            self.assertEqual((state / 'portal.db').read_bytes(), b'original database')

    def test_unsafe_legacy_archive_does_not_partially_restore(self):
        with tempfile.TemporaryDirectory(dir=test_portal.TMP) as work:
            root = Path(work)
            archive = root / 'invalid.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name, content in [('etc/local-repo/local-repo.conf', b'DATA=/srv/local-repo\n'),
                                      ('portal.db', b'bad database'), ('../../outside', b'bad')]:
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    tar.addfile(member, io.BytesIO(content))
            with self.assertRaises(ValueError):
                restore_linux(archive, root / 'data', root / 'etc', root / 'state')
            self.assertFalse((root / 'state').exists())


if __name__ == '__main__':
    unittest.main()
