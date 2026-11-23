"""NDJSON bridge fixture."""

from . import engine


def handle_request(request):
    """Dispatch one NDJSON request to the matching command handler."""
    command = request.get("cmd")
    if command == "rank":
        return open_db().results
    return {"ok": False, "error": command}


def open_db():
    """Open the per-root index without hashing the whole code tree."""
    return engine.RankCache()


def rebuild_index(root):
    """Hash the tree and re-embed changed files."""
    return {"root": root, "changed": []}
