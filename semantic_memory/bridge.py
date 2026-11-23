r"""NDJSON bridge for OpenCode plugins: one request line → one JSON answer line.

Run: python -m semantic_memory.bridge
Requests:
  {"cmd": "ping"}
  {"cmd": "recall", "query": "...", "k": 5, "topic": "...",
   "excludeTopics": ["chat"], "minSim": 0.45}
  {"cmd": "add", "text": "...", "topic": "...", "force": false}
  {"cmd": "reindex", "path": "...", "force": false}
  {"cmd": "lint", "apply": false}
  {"cmd": "stats"}
The id field (when supplied) comes back with the answer — the client matches lines.
Errors are {"ok": false, "error": "..."}; a bad line never ends the process.

Facts are rebuilt on every request (incremental by topic hash). The code tree is
NOT hashed on every request: recall/add/stats only read the existing index, and
`reindex` (or the plugin's post-edit hook) refreshes it.
"""

import json
import sys

from . import codeindex, config, db, facts, search
from .mcp_server import open_db, root


def _stats(con):
    return {
        "facts": con.execute("SELECT COUNT(*) FROM fact").fetchone()[0],
        "files": con.execute("SELECT COUNT(*) FROM file").fetchone()[0],
        "symbols": con.execute("SELECT COUNT(*) FROM sym").fetchone()[0],
        "embedded": db.meta_get(con, "embedded") == "1",
        "root": db.meta_get(con, "root") or root(),
    }


def _reindex(con, req):
    sub = req.get("path")
    base = config.safe_path(root(), sub) if sub else root()
    if base is None:
        raise ValueError("path escapes the project root: %r" % sub)
    force = bool(req.get("force"))
    if sub:
        files, symbols, changed = codeindex.index_paths(
            con, root(), codeindex.iter_code(base), force=force)
        removed = []
    else:
        files, symbols, changed, removed = codeindex.index_root(con, root(), force=force)
    return {"files": files, "symbols": symbols, "changed": changed,
            "removed": removed}


def handle(req):
    cmd = req.get("cmd")
    con = open_db(scan_code=False)
    if cmd == "ping":
        return {"ok": True, **_stats(con)}
    if cmd == "recall":
        hits = search.search_facts(
            con, req.get("query", ""), int(req.get("k") or 5),
            req.get("topic"), exclude_topics=req.get("excludeTopics"),
            min_sim=req.get("minSim"))
        return {"facts": hits}
    if cmd == "add":
        result = facts.add_fact(con, req.get("text", ""), req.get("topic"),
                                force=bool(req.get("force")))
        n, changed = facts.rebuild_facts(con)
        return {**result, "facts": n, "changed": changed}
    if cmd == "reindex":
        return _reindex(con, req)
    if cmd == "lint":
        return facts.lint(con, apply=bool(req.get("apply")))
    if cmd == "stats":
        return _stats(con)
    raise ValueError("unknown command: %s" % cmd)


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            result = handle(req)
            payload = {"ok": True, **result}
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
        try:
            req_id = json.loads(line).get("id")
        except Exception:
            req_id = None
        if req_id is not None:
            payload["id"] = req_id
        stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdout.flush()


if __name__ == "__main__":
    serve()
