"""VM Alpine jetable, root : deux véritables installations depuis des archives, puis retour arrière après échec."""
import hashlib
import io
from pathlib import Path
import re
import tarfile
from unittest import mock
import sys

sys.path.insert(0, '/opt/loco/portal')
import locoadmin

source = Path(locoadmin.read_conf()['SOURCE_DIR'])
ca = hashlib.sha256(Path('/etc/loco/tls/ca.crt').read_bytes()).hexdigest()
conf = locoadmin.read_conf()


def release(version, broken=False):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for path in source.rglob('*'):
            if not path.is_file() or '.git' in path.parts or '__pycache__' in path.parts:
                continue
            name = str(path.relative_to(source))
            data = path.read_bytes()
            if name == 'portal/loco/config.py':
                data, count = re.subn(rb'^VERSION = "[0-9.]+"', f'VERSION = "{version}"'.encode(), data, flags=re.M)
                assert count == 1
            if broken and name == 'linux/install.sh':
                data = b'#!/bin/bash\necho "WU_ENABLED=0" > /etc/loco/loco.conf\nexit 1\n'
            member = tarfile.TarInfo(f'loco-{version}/{name}')
            member.size, member.mode = len(data), path.stat().st_mode & 0o777
            tar.addfile(member, io.BytesIO(data))
    archive = buf.getvalue()
    sums = f'{hashlib.sha256(archive).hexdigest()}  loco-{version}.tar.gz\n'.encode()
    files = {'archive': archive, 'sums': sums}
    with mock.patch.object(locoadmin, 'latest_release', return_value={'version': version, 'archive': 'archive', 'sums': 'sums'}), \
            mock.patch.object(locoadmin, '_get', side_effect=lambda url, timeout=30: io.BytesIO(files[url])):
        return locoadmin.update_loco(log=lambda _line: None)


major, minor, patch = map(int, locoadmin.current_version().split('.'))
next_version = f'{major}.{minor}.{patch + 1}'
broken_version = f'{major}.{minor}.{patch + 2}'
assert release(next_version)
assert locoadmin.current_version() == next_version
assert not release(broken_version, broken=True)
assert locoadmin.current_version() == next_version
assert locoadmin.read_conf()['WU_ENABLED'] == conf['WU_ENABLED']
assert hashlib.sha256(Path('/etc/loco/tls/ca.crt').read_bytes()).hexdigest() == ca
print('Archives vérifiées : installation réelle puis retour arrière après échec, configuration et CA conservées.')
