"""Copie SQLite cohérente pour les sauvegardes et retours arrière, sans sessions distantes transitoires."""
from contextlib import closing
import sqlite3


def snapshot(source, destination):
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "remote_sessions" in tables:
            dst.execute("PRAGMA secure_delete=ON")
            dst.execute("DELETE FROM remote_packets")
            dst.execute("DELETE FROM remote_sessions")
            dst.commit()
            dst.execute("VACUUM")
