r"""NDJSON-мост для плагинов OpenCode: строка запроса → строка JSON-ответа.

Запуск: python -m semantic_memory.bridge
Запросы:
  {"cmd": "ping"}
  {"cmd": "recall", "query": "...", "k": 5, "topic": "..."}
  {"cmd": "add", "text": "...", "topic": "..."}
  {"cmd": "reindex", "path": "...", "force": false}
  {"cmd": "stats"}
Поле id (если передано) возвращается с ответом — клиент сопоставляет строки.
Ошибки — {"ok": false, "error": "..."}; процесс не завершается от плохой строки.
Код индексируется и факты перестраиваются при каждом запросе (как в MCP).
"""

import json
import os
import sys

from . import codeindex, db, facts, search
from .mcp_server import open_db, root


def _stats(con):
    return {
        "facts": con.execute("SELECT COUNT(*) FROM fact").fetchone()[0],
        "files": con.execute("SELECT COUNT(*) FROM file").fetchone()[0],
        "symbols": con.execute("SELECT COUNT(*) FROM sym").fetchone()[0],
        "embedded": db.meta_get(con, "embedded") == "1",
        "root": db.meta_get(con, "root") or root(),
    }


def handle(req):
    cmd = req.get("cmd")
    con = open_db()
    if cmd == "ping":
        return {"ok": True, **_stats(con)}
    if cmd == "recall":
        hits = search.search_facts(con, req.get("query", ""),
                                   int(req.get("k") or 5), req.get("topic"))
        return {"facts": hits}
    if cmd == "add":
        topic = facts.append_fact(con, req.get("text", ""), req.get("topic"))
        n, changed = facts.rebuild_facts(con)
        return {"topic": topic, "facts": n, "changed": changed}
    if cmd == "reindex":
        sub = req.get("path")
        base = os.path.join(root(), sub) if sub else root()
        files, symbols, changed = codeindex.index_paths(
            con, root(), codeindex.iter_code(base), force=bool(req.get("force")))
        return {"files": files, "symbols": symbols, "changed": changed}
    if cmd == "stats":
        return _stats(con)
    raise ValueError("неизвестная команда: %s" % cmd)


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
