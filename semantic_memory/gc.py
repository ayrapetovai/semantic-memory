r"""Garbage collection for per-root index files.

Each code root gets its own `.indexes/<hash>.db`; over time a machine accumulates
databases for directories that no longer exist. `collect` reports them, `remove`
deletes the database plus its WAL/SHM companions.
"""

import os
import sqlite3
import time


def collect(index_dir, days=None, keep_roots=()):
    """[(path, root, reason, size)] for orphaned/old index databases."""
    out = []
    if not index_dir or not os.path.isdir(index_dir):
        return out
    keep = {os.path.realpath(r) for r in keep_roots}
    for name in sorted(os.listdir(index_dir)):
        if not name.endswith(".db"):
            continue
        path = os.path.join(index_dir, name)
        root = None
        try:
            con = sqlite3.connect(path)
            try:
                row = con.execute("SELECT v FROM meta WHERE k = 'root'").fetchone()
            finally:
                con.close()
            root = row[0] if row else None
        except sqlite3.Error:
            pass
        if root and os.path.realpath(root) in keep:
            continue
        reason = None
        if not root:
            reason = "no-root"
        elif not os.path.isdir(root):
            reason = "root-missing"
        elif days is not None:
            age = (time.time() - os.path.getmtime(path)) / 86400.0
            if age > days:
                reason = "old"
        if reason:
            out.append((path, root, reason, os.path.getsize(path)))
    return out


def remove(path):
    removed = []
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
            removed.append(path + suffix)
        except FileNotFoundError:
            pass
    return removed
