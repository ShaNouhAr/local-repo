"""Reprise des paramètres réels d'un conteneur, sans exposer ses secrets au shell."""
import importlib.util
from pathlib import Path
import shlex
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('docker_migration',
                                             Path(__file__).resolve().parents[1] / 'linux/docker/migrate-env.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class TestDockerMigration(unittest.TestCase):
    def test_effective_settings_and_bind_mounts_are_preserved(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work, '.env')
            path.write_text('LOCO_MAX_UPLOAD=9g\nLOCO_HOST=\n')
            inspection = [{'Config': {'Env': ['LR_HOST=10.20.0.10', 'LR_MAX_UPLOAD=7g',
                                               r'LR_PROXY=http://user:p$word\test@proxy:3128', 'TZ=Europe/Paris']},
                           'Mounts': [{'Type': 'bind', 'Destination': '/srv/local-repo', 'Source': '/srv/custom'},
                                      {'Type': 'bind', 'Destination': '/var/backups/local-repo', 'Source': '/backups'}]}]
            migration.migrate(inspection, path)
            values = dict(shlex.split(line)[0].split('=', 1) for line in path.read_text().splitlines())
            self.assertEqual(values['LOCO_HOST'], '10.20.0.10')
            self.assertEqual(values['LOCO_MAX_UPLOAD'], '9g')
            self.assertEqual(values['LOCO_PROXY'], r'http://user:p$word\test@proxy:3128')
            self.assertEqual(values['LOCO_DATA_DIR'], '/srv/custom')
            self.assertEqual(values['LOCO_BACKUP_DIR'], '/backups')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.with_name('.env.before-loco').read_text(), 'LOCO_MAX_UPLOAD=9g\nLOCO_HOST=\n')

    def test_ambiguous_old_and_new_settings_are_rejected_without_overwriting(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work, '.env')
            initial = 'LR_HOST=old\nLOCO_HOST=new\n'
            path.write_text(initial)
            with self.assertRaises(ValueError):
                migration.migrate([{'Config': {'Env': []}, 'Mounts': []}], path)
            self.assertEqual(path.read_text(), initial)


if __name__ == '__main__':
    unittest.main()
