"""Point d'entrée gunicorn (« app:app »). Administration : python3 -m localrepo.cli."""

from localrepo import app, startup

startup()

__all__ = ["app"]
