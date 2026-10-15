"""Reprend les réglages effectifs de l'ancien conteneur, y compris ses dossiers bind."""
import json
import os
from pathlib import Path
import re
import sys


def migrate(inspect, path):
    container = inspect[0]
    previous = {}
    for line in container['Config']['Env']:
        key, _, value = line.partition('=')
        if key.startswith('LR_'):
            previous['LOCO_' + key[3:]] = value
        elif key == 'TZ':
            previous[key] = value
    for mount in container.get('Mounts', []):
        if mount['Type'] == 'bind':
            if mount['Destination'] == '/srv/local-repo':
                previous['LOCO_DATA_DIR'] = mount['Source']
            elif mount['Destination'] == '/var/backups/local-repo':
                previous['LOCO_BACKUP_DIR'] = mount['Source']
    path = Path(path)
    text = path.read_text() if path.exists() else ''
    lines, present = [], set()
    for line in text.splitlines():
        match = re.match(r'^(LR_|LOCO_)([A-Z0-9_]+)=(.*)$', line)
        if match:
            key = 'LOCO_' + match[2]
            if key in present:
                raise ValueError('Variable définie plusieurs fois dans .env : ' + key)
            present.add(key)
            if match[3].strip() not in ('', "''", '""'):
                lines.append(key + '=' + match[3])
                continue
            previous.setdefault(key, '')
        elif line.startswith('TZ='):
            present.add('TZ')
            lines.append(line)
            continue
        else:
            lines.append(line)
    explicit = {line.split('=', 1)[0] for line in lines if '=' in line and not line.startswith('#')}
    for key, value in previous.items():
        if key not in explicit:
            if '\n' in value or '\r' in value:
                raise ValueError('Valeur multiligne non prise en charge : ' + key)
            # Les quotes simples de Compose empêchent l'expansion de $ dans un mot de passe de proxy.
            lines.append(key + "='" + value.replace("'", "\\'") + "'")
    if path.exists() and not path.with_name('.env.before-loco').exists():
        backup = path.with_name('.env.before-loco')
        backup.write_bytes(path.read_bytes())
        os.chmod(backup, 0o600)
    path.write_text('\n'.join(lines) + '\n')
    os.chmod(path, 0o600)


if __name__ == '__main__':
    migrate(json.load(sys.stdin), sys.argv[1])
