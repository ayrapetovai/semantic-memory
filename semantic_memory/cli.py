r"""semantic-memory CLI.

  python -m semantic_memory.cli index [path] [--force]     index code under the root
  python -m semantic_memory.cli serve                      MCP server on stdio
  python -m semantic_memory.cli code "hybrid search" [-k 8] [--lang python] [--path sub]
  python -m semantic_memory.cli symbol Class.method        symbol body
  python -m semantic_memory.cli callers name | callees name | refs name
  python -m semantic_memory.cli outline path               file outline
  python -m semantic_memory.cli map [--path sub]           project files
  python -m semantic_memory.cli facts "cooling fan" [--topic hardware]
  python -m semantic_memory.cli add "a fact" [--topic flow]
  python -m semantic_memory.cli stats
"""

import argparse
import os
import sys

from . import codeindex, config, db, facts, mcp_server, search


def root(args):
    value = args.root or os.environ.get("SEMANTIC_MEMORY_ROOT")
    return os.path.abspath(os.path.expanduser(value)) if value else os.getcwd()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="semantic-memory: fact memory + code index")
    parser.add_argument("command", help="index|serve|code|symbol|callers|callees|refs|outline|map|facts|add|stats")
    parser.add_argument("arg", nargs="?", default="")
    parser.add_argument("--root", default=None, help="code root of the project (defaults to cwd)")
    parser.add_argument("-k", type=int, default=8, help="number of results")
    parser.add_argument("--topic", default=None, help="fact topic")
    parser.add_argument("--lang", default=None, help="filter by language")
    parser.add_argument("--path", default=None, help="filter by path substring")
    parser.add_argument("--no-embed", action="store_true", help="search without vectors")
    parser.add_argument("--force", action="store_true", help="full index rebuild")
    args = parser.parse_args(argv)

    con = db.connect(config.db_path(root(args)))
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
        print("index: %d files, %d symbols, rebuilt %d, removed %d"
              % (files, symbols, len(changed), removed))
        return 0

    if cmd == "stats":
        st = search.stats(con)
        print("root: %s" % st["root"])
        print("facts: %d · files: %d · symbols: %d · calls: %d"
              % (st["facts"], st["files"], st["symbols"], st["calls"]))
        print("languages: %s" % ", ".join("%s:%d" % kv for kv in st["languages"].items()))
        print("embeddings: %s · model: %s" %
              ("on" if st["embedded"] else "off", config.MODEL_NAME))
        return 0

    if cmd == "add":
        topic = facts.append_fact(con, args.arg, args.topic)
        n, changed = facts.rebuild_facts(con)
        print("fact written to %s; %d documents, rebuilt %d" % (topic, n, len(changed)))
        return 0

    if cmd == "facts":
        hits = search.search_facts(con, args.arg, args.k, args.topic,
                                   use_embed=not args.no_embed)
        if not hits:
            print("no facts")
            return 0
        for h in hits:
            print("#%s:%d  %.4f  %s" % (h["topic"], h["num"], h["score"], h["text"]))
        return 0

    if cmd == "code":
        hits = search.search_symbols(con, args.arg, args.k, args.lang, args.path,
                                     use_embed=not args.no_embed)
        if not hits:
            print("no symbols")
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
            print("symbol not found")
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
            print("called from:")
            for r in rows:
                print("  %s:%d  %s [%s]" % (r["file"], r["line"], r["qual"], r["kind"]))
        elif cmd == "callees":
            print("calls: " + ", ".join(search.callees(con, args.arg)))
        else:
            print("used in:")
            for r in search.references(con, args.arg):
                print("  %s:%d" % (r["file"], r["line"]))
        return 0

    if cmd == "outline":
        rows = con.execute("SELECT qual, kind, start, end, sig FROM sym WHERE file = ? "
                           "ORDER BY start", (args.arg,)).fetchall()
        if not rows:
            print("file not indexed")
            return 1
        for r in rows:
            print("  %d-%d  %s [%s]  %s" % (r[2], r[3], r[0], r[1], (r[4] or "")[:100]))
        return 0

    if cmd == "map":
        for f in search.file_map(con, args.path, args.k):
            print("  %s (%s, %d lines) — %s" % (f["path"], f["lang"], f["lines"], f["summary"]))
        return 0

    print("unknown command: %s" % cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
