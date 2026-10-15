"""Archive des sources : fichiers Git et nouveaux fichiers non ignorés, sans dossiers locaux ni sorties de build."""
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

root, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
version = re.search(r'^VERSION = "([0-9.]+)"', (root / "portal/loco/config.py").read_text(), re.M).group(1)
if (root / ".git").exists():
    names = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]).split(b"\0")
    paths = sorted({root / os.fsdecode(n) for n in names if n})
else:
    # Archive de release déjà filtrée (construction CI ou sur un serveur sans Git).
    paths = sorted(p for p in root.rglob("*") if p.is_file() and not any(x in p.parts for x in (".git", "__pycache__", "test-results", "out")))
def root_owner(member):
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    return member


with tarfile.open(output, "w:gz") as tar:
    for path in paths:
        if path == output or not path.is_file() or path.is_symlink():
            continue
        tar.add(path, arcname=f"loco-{version}/{path.relative_to(root)}", recursive=False, filter=root_owner)
