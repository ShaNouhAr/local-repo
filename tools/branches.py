#!/usr/bin/env python3
"""Génère les branches « linux » et « windows » depuis une version de main (lancé par la CI à chaque version).

Chaque branche ne contient que ce qu'il faut pour installer le serveur sur son système : portail commun,
gabarits nginx, installateur et documentation du système. Le développement, les tests et la CI restent
sur main ; les branches ne se modifient pas à la main (elles sont réécrites à chaque version).

  tools/branches.py REF          crée ou avance les branches locales linux et windows (sans les pousser)

README.md : les sections entre <!-- seulement:X --> et <!-- /seulement:X --> ne restent que sur main et
la branche X (X = linux, windows ou main) ; un bloc « <!-- branche:X » … « --> », invisible sur main,
est révélé dans la branche X.
"""

import fnmatch
import os
import re
import subprocess
import sys
import tempfile

# Fichiers retirés de chaque branche (motifs sur les chemins du dépôt).
COMMON_EXCLUDE = [".github/*", "tests/*", "tools/*"]
EXCLUDE = {
    "linux": COMMON_EXCLUDE + ["windows/*", "portal/lrwin.py", "conf/nginx-apt-proxy.conf", "docs/windows.md"],
    "windows": COMMON_EXCLUDE + [
        "install.sh", "remove.sh", "bin/*", "lib/*", "docker/*", "lxc/*", "Dockerfile", "docker-compose.yml",
        ".dockerignore", ".env.example", "docs/EXPLOITATION.md", "conf/*.service", "conf/*.timer", "conf/*.path",
        "conf/apt-cacher-ng-override.conf",
    ],
}
BRANCHES = tuple(EXCLUDE)


def git(*args, env=None, input=None):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=env, input=input).stdout.strip()


def readme(text, branch):
    for name in ("linux", "windows", "main"):
        keep = name == branch
        pattern = re.compile(rf"<!-- seulement:{name} -->\n?(.*?)<!-- /seulement:{name} -->\n?", re.S)
        text = pattern.sub(lambda m: m.group(1) if keep else "", text)
    for name in ("linux", "windows"):
        pattern = re.compile(rf"<!-- branche:{name}\n(.*?)-->\n?", re.S)
        text = pattern.sub(lambda m: m.group(1) if name == branch else "", text)
    if "<!-- seulement:" in text or "<!-- branche:" in text:
        sys.exit("README.md : marqueur de branche mal fermé")
    return re.sub(r"\n{3,}", "\n\n", text)


def build(ref, branch):
    files = git("ls-tree", "-r", "--name-only", ref).splitlines()
    excluded = EXCLUDE[branch]
    kept = [f for f in files if not any(fnmatch.fnmatch(f, p) for p in excluded)]
    with tempfile.TemporaryDirectory() as work:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(work, "index"))
        git("read-tree", "--empty", env=env)
        entries = git("ls-tree", "-r", ref, "--", *kept).splitlines() if kept else []
        git("update-index", "--index-info", env=env, input="\n".join(entries) + "\n")
        text = git("show", f"{ref}:README.md")
        blob = git("hash-object", "-w", "--stdin", input=readme(text + "\n", branch))
        git("update-index", "--cacheinfo", f"100644,{blob},README.md", env=env)
        tree = git("write-tree", env=env)
    parent = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/heads/{branch}^{{commit}}"],
                            capture_output=True, text=True).stdout.strip()
    if parent and git("rev-parse", f"{parent}^{{tree}}") == tree:
        print(f"{branch} : déjà à jour")
        return
    version = git("describe", "--tags", "--always", ref)
    message = (f"{branch} : local-repo {version}\n\nBranche générée depuis main ({git('rev-parse', '--short', ref)}) "
               f"par tools/branches.py ;\nne pas la modifier à la main.\n")
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    commit = git(*args)
    git("update-ref", f"refs/heads/{branch}", commit)
    print(f"{branch} : {commit[:12]} ({len(kept)} fichiers)")


def main(argv):
    if len(argv) != 1:
        sys.exit(__doc__)
    ref = git("rev-parse", "--verify", f"{argv[0]}^{{commit}}")
    for branch in BRANCHES:
        build(ref, branch)


if __name__ == "__main__":
    main(sys.argv[1:])
