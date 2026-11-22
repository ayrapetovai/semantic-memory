r"""CLI semantic-memory.

  python -m semantic_memory.cli index [path] [--force]     индекс кода по корню
  python -m semantic_memory.cli serve                      MCP-сервер на stdio
  python -m semantic_memory.cli code "hybrid search" [-k 8] [--lang python] [--path sub]
  python -m semantic_memory.cli symbol Class.method        тело символа
  python -m semantic_memory.cli callers name | callees name | refs name
  python -m semantic_memory.cli outline path               карта файла
  python -m semantic_memory.cli map [--path sub]           файлы проекта
  python -m semantic_memory.cli facts "кулер" [--topic hardware]
  python -m semantic_memory.cli add "факт" [--topic flow]
  python -m semantic_memory.cli stats
"""

import argparse
import os
import sys

from . import codeindex, config, db, facts, mcp_server, search


def root(args):
    return args.root or os.environ.get("SEMANTIC_MEMORY_ROOT") or os.getcwd()


def main(argv=None):
    parser = argparse.ArgumentParser(description="semantic-memory: память фактов + индекс кода")
    parser.add_argument("command", help="index|serve|code|symbol|callers|callees|refs|outline|map|facts|add|stats")
    parser.add_argument("arg", nargs="?", default="")
    parser.add_argument("--root", default=None, help="корень кода проекта (по умолчанию cwd)")
    parser.add_argument("-k", type=int, default=8, help="число результатов")
    parser.add_argument("--topic", default=None, help="тема фактов")
    parser.add_argument("--lang", default=None, help="фильтр по языку")
    parser.add_argument("--path", default=None, help="фильтр по подстроке пути")
    parser.add_argument("--no-embed", action="store_true", help="поиск без векторов")
    parser.add_argument("--force", action="store_true", help="полная перестройка индекса")
    args = parser.parse_args(argv)

    con = db.connect()
    cmd = args.command

    if cmd == "serve":
        mcp_server.serve()
        return 0

    if cmd == "index":
        base = os.path.join(root(args), args.arg) if args.arg else root(args)
        files, symbols, changed = codeindex.index_paths(
            con, root(args), codeindex.iter_code(base), force=args.force)
        removed = 0
        if not args.arg:
            files, symbols, changed, removed = codeindex.index_root(con, root(args), force=args.force)
        print("индекс: файлов %d, символов %d, перестроено %d, удалено %d"
              % (files, symbols, len(changed), removed))
        return 0

    if cmd == "stats":
        st = search.stats(con)
        print("корень: %s" % st["root"])
        print("факты: %d · файлы: %d · символы: %d · вызовы: %d"
              % (st["facts"], st["files"], st["symbols"], st["calls"]))
        print("языки: %s" % ", ".join("%s:%d" % kv for kv in st["languages"].items()))
        print("эмбеддинги: %s · модель: %s" %
              ("включены" if st["embedded"] else "выключены", config.MODEL_NAME))
        return 0

    if cmd == "add":
        topic = facts.append_fact(con, args.arg, args.topic)
        n, changed = facts.rebuild_facts(con)
        print("записан факт в %s; документов %d, перестроено %d" % (topic, n, len(changed)))
        return 0

    if cmd == "facts":
        hits = search.search_facts(con, args.arg, args.k, args.topic,
                                   use_embed=not args.no_embed)
        if not hits:
            print("фактов нет")
            return 0
        for h in hits:
            print("#%s:%d  %.4f  %s" % (h["topic"], h["num"], h["score"], h["text"]))
        return 0

    if cmd == "code":
        hits = search.search_symbols(con, args.arg, args.k, args.lang, args.path,
                                     use_embed=not args.no_embed)
        if not hits:
            print("символов нет")
            return 0
        for s in hits:
            print("%s:%d-%d  %s [%s]  %.4f" %
                  (s["file"], s["start"], s["end"], s["qual"], s["kind"], s["score"]))
            if s["sig"]:
                print("    %s" % s["sig"][:120])
        return 0

    if cmd == "symbol":
        found = search.symbol_by_name(con, args.arg)
        if not found:
            print("символ не найден")
            return 1
        for s in found[:3]:
            print("%s:%d-%d  %s [%s]" % (s["file"], s["start"], s["end"], s["qual"], s["kind"]))
            body = search.symbol_body(con, s["id"])
            if body:
                for i, line in enumerate(body["body"].splitlines(), s["start"]):
                    print("    %4d | %s" % (i, line))
        return 0

    if cmd in ("callers", "callees", "refs"):
        if cmd == "callers":
            rows = search.callers(con, args.arg)
            print("вызывается из:")
            for r in rows:
                print("  %s:%d  %s [%s]" % (r["file"], r["line"], r["qual"], r["kind"]))
        elif cmd == "callees":
            print("вызывает: " + ", ".join(search.callees(con, args.arg)))
        else:
            print("использование:")
            for r in search.references(con, args.arg):
                print("  %s:%d" % (r["file"], r["line"]))
        return 0

    if cmd == "outline":
        rows = con.execute("SELECT qual, kind, start, end, sig FROM sym WHERE file = ? "
                           "ORDER BY start", (args.arg,)).fetchall()
        if not rows:
            print("файл не проиндексирован")
            return 1
        for r in rows:
            print("  %d-%d  %s [%s]  %s" % (r[2], r[3], r[0], r[1], (r[4] or "")[:100]))
        return 0

    if cmd == "map":
        for f in search.file_map(con, args.path, args.k):
            print("  %s (%s, %d строк) — %s" % (f["path"], f["lang"], f["lines"], f["summary"]))
        return 0

    print("неизвестная команда: %s" % cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
