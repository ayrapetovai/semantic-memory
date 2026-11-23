r"""semantic-memory MCP server: stdio, JSON-RPC, no external dependencies.

The harness connects to it as mcpServers/stdio: command — the python from
semantic-memory\.venv, args — the path to run_mcp.py. The code root is the process
working directory (the harness session cwd); the index is built lazily on the first
request and kept in a separate file per root (config.INDEX_DIR).

Answers are text for an LLM to read: `file:lines` + signature, the symbol body in
full, not JSON fragments.
"""

import json
import os
import sys

from . import codeindex, config, db, facts, search

PROTOCOL = "2024-11-05"
SERVER = {"name": "semantic-memory", "version": "1.0.0"}

TOOLS = [
    {"name": "code_search",
     "description": "Hybrid search over project code: exact tokens + semantics. "
                    "Returns symbols (functions, classes, methods) with file and "
                    "line numbers. Use it first when you need to find where a "
                    "function or mechanism is implemented.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Query: a name, part of a name, or a description of the mechanism."},
         "k": {"type": "integer", "description": "Number of results, defaults to 8."},
         "lang": {"type": "string", "description": "Filter by language: python, javascript, go..."},
         "path": {"type": "string", "description": "Filter by path substring."}},
         "required": ["query"]}},
    {"name": "symbol",
     "description": "Full body of a symbol by name or qual (Class.method). "
                    "Read a symbol in full instead of fragments from code_search.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Symbol name or Class.method."},
         "body": {"type": "boolean", "description": "Include the body, defaults to true."}},
         "required": ["name"]}},
    {"name": "callers",
     "description": "Who calls the symbol — the list of symbols that use it.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Name of the called symbol."}},
         "required": ["name"]}},
    {"name": "callees",
     "description": "What the symbol calls — the names of calls inside its body.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Symbol name."}},
         "required": ["name"]}},
    {"name": "references",
     "description": "Where the name appears in the project (file:line), including places outside calls.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Function, class or method name."}},
         "required": ["name"]}},
    {"name": "file_outline",
     "description": "File map: symbols in order with line ranges and signatures.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Path of the file relative to the project root."}},
         "required": ["path"]}},
    {"name": "file_read",
     "description": "Read a slice of a file with line numbers (up to 200 lines per call).",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Path of the file relative to the project root."},
         "offset": {"type": "integer", "description": "First line, 1-based."},
         "limit": {"type": "integer", "description": "Number of lines, defaults to 120."}},
         "required": ["path"]}},
    {"name": "project_map",
     "description": "List of indexed files with a summary: language, line count, "
                    "symbols, imports. Use it to see the project structure before editing code.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Filter by path substring."},
         "limit": {"type": "integer", "description": "Number of files, defaults to 40."}}}},
    {"name": "facts_search",
     "description": "Search over project memory (memory/*.md): decisions, conventions, "
                    "notes about code. One bullet = one fact. Relevant-to-the-query "
                    "results only; use excludeTopics to drop a noisy topic.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Query."},
         "topic": {"type": "string", "description": "Topic: the name of memory/<topic>.md."},
         "k": {"type": "integer", "description": "Number of results, defaults to 5."},
         "excludeTopics": {"type": "array", "items": {"type": "string"},
                           "description": "Topics to omit (for example chat, state)."},
         "minSim": {"type": "number",
                    "description": "Minimum vector similarity for vector-only hits."}},
         "required": ["query"]}},
    {"name": "facts_add",
     "description": "Write a fact into project memory. One bullet = one fact. "
                    "Identical or near-identical facts in the topic are skipped "
                    "unless force=true; secrets are redacted.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "Fact text: a decision or convention."},
         "topic": {"type": "string", "description": "Topic; a new topic file is created when the name is unknown."},
         "force": {"type": "boolean", "description": "Write even if a duplicate exists."}},
         "required": ["text"]}},
    {"name": "memory_lint",
     "description": "Report duplicate, near-duplicate, undated and stale facts. "
                    "apply=true removes exact duplicates (archive sections are untouched).",
     "inputSchema": {"type": "object", "properties": {
         "apply": {"type": "boolean", "description": "Remove exact duplicates."},
         "minSim": {"type": "number", "description": "Near-duplicate threshold."},
         "staleDays": {"type": "integer", "description": "Age in days for a stale fact."}}}},
    {"name": "index_rebuild",
     "description": "Rebuild the code index under the project root (changed files) or "
                    "completely (force=true). Call it after a large code change.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Subdirectory for a partial rebuild."},
         "force": {"type": "boolean", "description": "Full rebuild, including embeddings."}}}},
    {"name": "index_stats",
     "description": "Index state: number of facts, files, symbols, calls and languages.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def root():
    """The code root is the process working directory (the harness session cwd)."""
    return os.getcwd()


def open_db(scan_code=True):
    """Index for the current root (the process cwd): one file under config.INDEX_DIR.

    `scan_code=False` skips the sha256 walk when the caller only needs facts
    (the bridge calls it for recall/add/stats; the plugin reindexes after edits).
    """
    rt = root()
    con = db.connect(config.db_path(rt))
    if db.meta_get(con, "root") != rt:
        codeindex.reset_code(con)
    if scan_code:
        codeindex.index_root(con, rt)
    facts.rebuild_facts(con)  # incremental: embeddings only for changed topics
    return con


def _fmt_symbol(s):
    head = "%s:%d-%d  %s [%s]" % (s["file"], s["start"], s["end"], s["qual"], s["kind"])
    doc = (s.get("doc") or "").strip().replace("\n", " ")[:160]
    sig = (s.get("sig") or "").strip()[:200]
    return "%s\n    %s%s" % (head, sig, ("\n    doc: " + doc) if doc else "")


def _norm_path(path):
    """Path argument → relative form with "/": Windows separators and the absolute root."""
    p = str(path).replace("\\", "/")
    base = root().replace("\\", "/").rstrip("/") + "/"
    if p.lower().startswith(base.lower()):
        p = p[len(base):]
    return p


def call_tool(con, name, args):
    args = args or {}
    if args.get("path"):
        args["path"] = _norm_path(args["path"])
    if name == "code_search":
        hits = search.search_symbols(con, args.get("query", ""),
                                     int(args.get("k") or 8),
                                     args.get("lang"), args.get("path"))
        if not hits:
            return "No symbols match the query. Refine the name or call project_map."
        parts = ["Symbols found:"]
        for s in hits:
            parts.append(_fmt_symbol(s))
        return "\n".join(parts)

    if name == "symbol":
        found = search.symbol_by_name(con, args.get("name", ""))
        if not found:
            return "Symbol %r not found. Try code_search." % args.get("name")
        parts = []
        for s in found:
            parts.append(_fmt_symbol(s))
            if args.get("body", True):
                body = search.symbol_body(con, s["id"])
                if body:
                    parts.append("    body:\n" + _numbered(body["body"], s["start"]))
        return "\n".join(parts)

    if name == "callers":
        rows = search.callers(con, args.get("name", ""))
        if not rows:
            return "Nothing calls %r in the index." % args.get("name")
        return "Called from:\n" + "\n".join(
            "  %s:%d  %s [%s]" % (r["file"], r["line"],
                                 r["qual"] or "(module)", r["kind"] or "module")
            for r in rows)

    if name == "callees":
        names = search.callees(con, args.get("name", ""))
        if not names:
            return "%r makes no calls." % args.get("name")
        return "Calls: " + ", ".join(names)

    if name == "references":
        rows = search.references(con, args.get("name", ""))
        if not rows:
            return "%r is not referenced in indexed files." % args.get("name")
        return "Used in:\n" + "\n".join(
            "  %s:%d" % (r["file"], r["line"]) for r in rows)

    if name == "file_outline":
        rp = args.get("path", "")
        rows = con.execute(
            "SELECT qual, kind, start, end, sig FROM sym WHERE file = ? "
            "ORDER BY start", (rp,)).fetchall()
        if not rows:
            return "File %r is not indexed." % rp
        return "Outline of %s:\n" % rp + "\n".join(
            "  %d-%d  %s [%s]  %s" % (r[2], r[3], r[0], r[1], (r[4] or "")[:120])
            for r in rows)

    if name == "file_read":
        rp = args.get("path", "")
        full = config.safe_path(root(), rp)
        if full is None:
            return "path escapes the project root: %r" % rp
        if not os.path.isfile(full):
            return "File %r not found." % rp
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        off = max(1, int(args.get("offset") or 1))
        limit = min(int(args.get("limit") or 120), 200)
        chunk = lines[off - 1:off - 1 + limit]
        return "%s (%d lines, showing %d-%d)\n" % (rp, len(lines), off, off + len(chunk) - 1) \
            + _numbered("\n".join(chunk), off)

    if name == "project_map":
        rows = search.file_map(con, args.get("path"), int(args.get("limit") or 40))
        if not rows:
            return "No files in the index. Call index_rebuild."
        return "Project files:\n" + "\n".join(
            "  %s (%s, %d lines) — %s" % (r["path"], r["lang"], r["lines"], r["summary"])
            for r in rows)

    if name == "facts_search":
        hits = search.search_facts(con, args.get("query", ""),
                                   int(args.get("k") or 5), args.get("topic"),
                                   exclude_topics=args.get("excludeTopics"),
                                   min_sim=args.get("minSim"))
        if not hits:
            return "No facts match the query."
        return "Facts:\n" + "\n".join(
            "  #%s:%d (score %.4f, %s) %s%s" % (
                h["topic"], h["num"], h["score"], h["via"], h["text"],
                (" (%s)" % h["added"]) if h.get("added") else "")
            for h in hits)

    if name == "facts_add":
        result = facts.add_fact(con, args.get("text", ""), args.get("topic"),
                                force=bool(args.get("force")))
        n, changed = facts.rebuild_facts(con)
        if not result["added"]:
            return "Skipped: duplicates #%s:%d (%s); %d documents." % (
                result["topic"], result["duplicate_of"], result["matched"], n)
        return "Fact written to topic %s; %d documents, %d rebuilt." % (
            result["topic"], n, len(changed))

    if name == "memory_lint":
        report = facts.lint(con, apply=bool(args.get("apply")),
                            min_sim=args.get("minSim"),
                            stale_days=args.get("staleDays"))
        rows = report["findings"]
        if not rows:
            return "Memory is clean: %d topics, no duplicates or stale facts." % (
                report["topics"])
        lines = ["Memory lint: %d finding(s), %d exact duplicate(s) removed."
                 % (len(rows), report["removed"])]
        for f in rows:
            detail = ""
            if f["kind"] in ("duplicate", "near-duplicate"):
                detail = " of #%s:%d" % (f["topic"], f["of"])
            elif f["kind"] == "stale":
                detail = " age %d days" % f["age"]
            lines.append("  %s: #%s:%d%s — %s" % (
                f["kind"], f["topic"], f["num"], detail, f["text"][:160]))
        return "\n".join(lines)

    if name == "index_rebuild":
        sub = args.get("path")
        base = config.safe_path(root(), sub) if sub else root()
        if base is None:
            return "path escapes the project root: %r" % sub
        force = bool(args.get("force"))
        paths = codeindex.iter_code(base)
        files, symbols, changed = codeindex.index_paths(con, root(), paths, force=force)
        return "Index: %d files, %d symbols, %d rebuilt (%s)." % (
            files, symbols, len(changed), ", ".join(changed[:12]) or "no changes")

    if name == "index_stats":
        st = search.stats(con)
        warn = ""
        if st["symbols"] > config.ANN_WARN:
            warn = ("\n  warning: %d symbols; vector search is a brute-force scan "
                    "(install sqlite-vec or scope the index)" % st["symbols"])
        return "semantic-memory index\n  root: %s\n  files: %d\n  symbols: %d\n" \
               "  calls: %d\n  languages: %s\n  embeddings: %s%s" % (
            st["root"], st["files"], st["symbols"], st["calls"],
            ", ".join("%s:%d" % (k, v) for k, v in st["languages"].items()),
            "on" if st["embedded"] else "off", warn)

    raise ValueError("unknown tool %r" % name)


def _numbered(text, start):
    out = []
    for i, line in enumerate(text.splitlines(), start):
        out.append("    %4d | %s" % (i, line))
    return "\n".join(out)


def _stream():
    """Raw byte stream of stdout; wrappers (captured output, notebooks) have none."""
    return getattr(sys.stdout, "buffer", None)


def _send(obj):
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
    stream = _stream()
    if stream is None:
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.flush()
        return
    stream.write(payload)
    stream.flush()


def _reply(request, result=None, error=None):
    if error:
        _send({"jsonrpc": "2.0", "id": request.get("id"),
               "error": {"code": -32603, "message": str(error)}})
    else:
        _send({"jsonrpc": "2.0", "id": request.get("id"), "result": result})


def serve():
    con = None
    stdin = getattr(sys.stdin, "buffer", sys.stdin)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        if req.get("id") is None and method and method.startswith("notifications/"):
            continue
        try:
            if method == "initialize":
                version = req.get("params", {}).get("protocolVersion") or PROTOCOL
                _reply(req, {"protocolVersion": version if version in
                             (PROTOCOL, "2025-06-10", "2025-03-26") else PROTOCOL,
                             "capabilities": {"tools": {}}, "serverInfo": SERVER})
            elif method == "tools/list":
                _reply(req, {"tools": TOOLS})
            elif method == "ping":
                _reply(req, {})
            elif method == "tools/call":
                if con is None:
                    con = open_db()
                text = call_tool(con, req.get("params", {}).get("name"),
                                 req.get("params", {}).get("arguments"))
                _reply(req, {"content": [{"type": "text", "text": text}],
                             "isError": False})
            else:
                _send({"jsonrpc": "2.0", "id": req.get("id"),
                       "error": {"code": -32601, "message": "method not supported"}})
        except Exception as exc:
            _reply(req, error=exc)


if __name__ == "__main__":
    serve()
