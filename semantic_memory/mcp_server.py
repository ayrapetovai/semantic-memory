r"""MCP-сервер semantic-memory: stdio, JSON-RPC, без внешних зависимостей.

Harness подключает его как mcpServers/stdio: command — python из
semantic-memory\\.venv, args — ["-m", "semantic_memory.mcp"]. Корень кода берётся
из SEMANTIC_MEMORY_ROOT (по умолчанию cwd процесса), индекс строится лениво при
первом запросе.

Формат ответов — текст для чтения LLM: `файл:строки` + сигнатура, тело символа
целиком, а не JSON-фрагменты.
"""

import json
import os
import sys

from . import codeindex, config, db, facts, search

PROTOCOL = "2024-11-05"
SERVER = {"name": "semantic-memory", "version": "1.0.0"}

TOOLS = [
    {"name": "code_search",
     "description": "Гибридный поиск по коду проекта: точные токены + семантика. "
                    "Возвращает символы (функции, классы, методы) с файлом и "
                    "строками. Используйте первым, когда нужно найти, где "
                    "реализована функция или механизм.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Запрос: имя, часть имени или описание механизма."},
         "k": {"type": "integer", "description": "Число результатов, по умолчанию 8."},
         "lang": {"type": "string", "description": "Фильтр по языку: python, javascript, go..."},
         "path": {"type": "string", "description": "Фильтр по подстроке пути."}},
         "required": ["query"]}},
    {"name": "symbol",
     "description": "Полное тело символа по имени или qual (Class.method). "
                    "Читайте символ целиком, а не фрагменты из code_search.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Имя символа или Class.method."},
         "body": {"type": "boolean", "description": "Включить тело кода, по умолчанию true."}},
         "required": ["name"]}},
    {"name": "callers",
     "description": "Кто вызывает символ — список символов, которые его используют.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Имя вызываемого символа."}},
         "required": ["name"]}},
    {"name": "callees",
     "description": "Что вызывает символ — имена вызовов внутри его тела.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Имя символа."}},
         "required": ["name"]}},
    {"name": "references",
     "description": "Где имя встречается в проекте (файл:строка), включая места вне вызовов.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Имя функции, класса или метода."}},
         "required": ["name"]}},
    {"name": "file_outline",
     "description": "Карта файла: символы по порядку со строками и сигнатурами.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Путь файла относительно корня проекта."}},
         "required": ["path"]}},
    {"name": "file_read",
     "description": "Чтение фрагмента файла с номерами строк (до 200 строк за вызов).",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Путь файла относительно корня проекта."},
         "offset": {"type": "integer", "description": "1-based первая строка."},
         "limit": {"type": "integer", "description": "Число строк, по умолчанию 120."}},
         "required": ["path"]}},
    {"name": "project_map",
     "description": "Список индексированных файлов с резюме: язык, число строк, "
                    "символы, импорты. Используйте, чтобы увидеть структуру проекта "
                    "перед правкой кода.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Фильтр по подстроке пути."},
         "limit": {"type": "integer", "description": "Число файлов, по умолчанию 40."}}}},
    {"name": "facts_search",
     "description": "Поиск по памяти проекта (memory\\*.md): решения, карточки железа, "
                    "механики Strata, поток решений. Один bullet = один факт.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Запрос."},
         "topic": {"type": "string", "description": "Тема: hardware, game-design, flow."},
         "k": {"type": "integer", "description": "Число результатов, по умолчанию 5."}},
         "required": ["query"]}},
    {"name": "facts_add",
     "description": "Записать факт в память проекта одной строкой в нужную тему.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "Факт: точные единицы и решения."},
         "topic": {"type": "string", "description": "Тема; новая тема создаётся, если имя не найдено."}},
         "required": ["text"]}},
    {"name": "index_rebuild",
     "description": "Перестроить индекс кода по корню проекта (изменённые файлы) или "
                    "полностью (force=true). Вызывайте после большой правки кода.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Подпапка для частичной перестройки."},
         "force": {"type": "boolean", "description": "Полная перестройка, включая эмбеддинги."}}}},
    {"name": "index_stats",
     "description": "Состояние индекса: число фактов, файлов, символов, вызовов и языки.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def root():
    return os.environ.get("SEMANTIC_MEMORY_ROOT") or os.getcwd()


def open_db():
    con = db.connect()
    if con.execute("SELECT COUNT(*) FROM fact").fetchone()[0] == 0:
        facts.rebuild_facts(con)
    if con.execute("SELECT COUNT(*) FROM file").fetchone()[0] == 0:
        codeindex.index_root(con, root())
    return con


def _fmt_symbol(s):
    head = "%s:%d-%d  %s [%s]" % (s["file"], s["start"], s["end"], s["qual"], s["kind"])
    doc = (s.get("doc") or "").strip().replace("\n", " ")[:160]
    sig = (s.get("sig") or "").strip()[:200]
    return "%s\n    %s%s" % (head, sig, ("\n    doc: " + doc) if doc else "")


def call_tool(con, name, args):
    args = args or {}
    if name == "code_search":
        hits = search.search_symbols(con, args.get("query", ""),
                                     int(args.get("k") or 8),
                                     args.get("lang"), args.get("path"))
        if not hits:
            return "Символов по запросу нет. Уточните имя или вызовите project_map."
        parts = ["Найдено %d символов:" % len(hits)]
        for s in hits:
            parts.append(_fmt_symbol(s))
        return "\n".join(parts)

    if name == "symbol":
        found = search.symbol_by_name(con, args.get("name", ""))
        if not found:
            return "Символ %r не найден. Попробуйте code_search." % args.get("name")
        parts = []
        for s in found:
            parts.append(_fmt_symbol(s))
            if args.get("body", True):
                body = search.symbol_body(con, s["id"])
                if body:
                    parts.append("    тело:\n" + _numbered(body["body"], s["start"]))
        return "\n".join(parts)

    if name == "callers":
        rows = search.callers(con, args.get("name", ""))
        if not rows:
            return "Вызовов %r в индексе нет." % args.get("name")
        return "Вызывается из:\n" + "\n".join(
            "  %s:%d  %s [%s]" % (r["file"], r["line"], r["qual"], r["kind"])
            for r in rows)

    if name == "callees":
        names = search.callees(con, args.get("name", ""))
        if not names:
            return "В теле %r вызовов нет." % args.get("name")
        return "Вызывает: " + ", ".join(names)

    if name == "references":
        rows = search.references(con, args.get("name", ""))
        if not rows:
            return "Имя %r встречается только вне индексированных файлов." % args.get("name")
        return "Использование:\n" + "\n".join(
            "  %s:%d" % (r["file"], r["line"]) for r in rows)

    if name == "file_outline":
        rp = args.get("path", "")
        rows = con.execute(
            "SELECT qual, kind, start, end, sig FROM sym WHERE file = ? "
            "ORDER BY start", (rp,)).fetchall()
        if not rows:
            return "Файл %r не индексирован или символов нет." % rp
        return "Оглавление %s:\n" % rp + "\n".join(
            "  %d-%d  %s [%s]  %s" % (r[2], r[3], r[0], r[1], (r[4] or "")[:120])
            for r in rows)

    if name == "file_read":
        rp = args.get("path", "")
        full = os.path.join(root(), rp)
        if not os.path.isfile(full):
            return "Файл %r не найден." % rp
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        off = max(1, int(args.get("offset") or 1))
        limit = min(int(args.get("limit") or 120), 200)
        chunk = lines[off - 1:off - 1 + limit]
        return "%s (%d строк, показаны %d-%d)\n" % (rp, len(lines), off, off + len(chunk) - 1) \
            + _numbered("\n".join(chunk), off)

    if name == "project_map":
        rows = search.file_map(con, args.get("path"), int(args.get("limit") or 40))
        if not rows:
            return "Файлов нет. Вызовите index_rebuild."
        return "Файлы проекта:\n" + "\n".join(
            "  %s (%s, %d строк) — %s" % (r["path"], r["lang"], r["lines"], r["summary"])
            for r in rows)

    if name == "facts_search":
        hits = search.search_facts(con, args.get("query", ""),
                                   int(args.get("k") or 5), args.get("topic"))
        if not hits:
            return "Фактов по запросу нет."
        return "Факты:\n" + "\n".join(
            "  #%s:%d (score %.4f) %s" % (h["topic"], h["num"], h["score"], h["text"])
            for h in hits)

    if name == "facts_add":
        topic = facts.append_fact(con, args.get("text", ""), args.get("topic"))
        n, changed = facts.rebuild_facts(con)
        return "Записан факт в тему %s; документов %d, перестроено %d." % (
            topic, n, len(changed))

    if name == "index_rebuild":
        sub = args.get("path")
        base = os.path.join(root(), sub) if sub else root()
        force = bool(args.get("force"))
        paths = codeindex.iter_code(base)
        files, symbols, changed = codeindex.index_paths(con, root(), paths, force=force)
        return "Индекс: файлов %d, символов %d, перестроено %d (%s)." % (
            files, symbols, len(changed), ", ".join(changed[:12]) or "без изменений")

    if name == "index_stats":
        st = search.stats(con)
        return "Индекс semantic-memory\n  корень: %s\n  факты: %d\n  файлы: %d\n" \
               "  символы: %d\n  вызовы: %d\n  языки: %s\n  эмбеддинги: %s" % (
                   st["root"], st["facts"], st["files"], st["symbols"], st["calls"],
                   ", ".join("%s:%d" % (k, v) for k, v in st["languages"].items()),
                   "включены" if st["embedded"] else "выключены")

    raise ValueError("неизвестный инструмент %r" % name)


def _numbered(text, start):
    out = []
    for i, line in enumerate(text.splitlines(), start):
        out.append("    %4d | %s" % (i, line))
    return "\n".join(out)


def _send(obj):
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _reply(request, result=None, error=None):
    if error:
        _send({"jsonrpc": "2.0", "id": request.get("id"),
               "error": {"code": -32603, "message": str(error)}})
    else:
        _send({"jsonrpc": "2.0", "id": request.get("id"), "result": result})


def serve():
    con = None
    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
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
                       "error": {"code": -32601, "message": "метод не поддерживается"}})
        except Exception as exc:
            _reply(req, error=exc)


if __name__ == "__main__":
    serve()
