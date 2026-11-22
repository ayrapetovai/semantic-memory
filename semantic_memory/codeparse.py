r"""Parsing code into symbols, calls and references.

Python goes through ast: functions, classes, methods, nested defs, calls inside a
symbol, names used in the file. Other languages use regexes on the definition line
and brace counting to the end of the body; it is an approximation, but it gives the
LLM a file map and the "who calls what" step.
"""

import ast
import os
import re

from . import config

KEYWORDS = {
    "if", "elif", "else", "for", "while", "switch", "case", "return", "do",
    "try", "catch", "throw", "new", "typeof", "function", "class", "def",
    "await", "yield", "assert", "delete", "in", "of", "and", "or", "not",
    "print", "lambda", "match", "with", "return", "void", "struct", "enum",
    "public", "private", "protected", "static", "abstract", "async", "const",
    "let", "var", "use", "impl", "fn", "func", "type", "interface", "package",
    "import", "export", "from", "raise", "pass", "continue", "break", "yield",
}

IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
CALL_RE = re.compile(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")

DEF_PATTERNS = {
    "javascript": [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?:=>|\{)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"), "method"),
    ],
    "typescript": [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]*)?\s*(?:=>|\{)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::[^=]*)?\s*\{"), "method"),
        (re.compile(r"^\s*(?:export\s+)?(?:interface|type)\s+([A-Za-z_$][\w$]*)"), "type"),
    ],
    "go": [
        (re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("), "function"),
        (re.compile(r"^func\s+([A-Za-z_]\w*)\s*\("), "function"),
        (re.compile(r"^type\s+([A-Za-z_]\w*)\s"), "type"),
    ],
    "rust": [
        (re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"), "function"),
        (re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait|impl)\s+([A-Za-z_]\w*)"), "type"),
    ],
    "java": [
        (re.compile(r"^\s*(?:@\w+\s+)?(?:public|private|protected)?\s*(?:static\s+)?(?:abstract\s+)?(?:class|interface|enum|record)\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*(?:@\w+\s+)?(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>[\],\s]+?\s+([A-Za-z_]\w*)\s*\("), "method"),
    ],
    "csharp": [
        (re.compile(r"^\s*(?:public|private|protected|internal|sealed|static|abstract|partial|\s)*\b(class|interface|struct|enum|record)\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*(?:public|private|protected|internal|static|virtual|override|async|\s)*[\w<>[\],\s]+?\s+([A-Za-z_]\w*)\s*\("), "method"),
    ],
    "cpp": [
        (re.compile(r"^\s*(?:struct|class|enum)\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*[\w:<>~&\* ]+?\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:const)?\s*(?:noexcept)?\s*\{"), "function"),
    ],
    "c": [
        (re.compile(r"^\s*(?:struct|enum|union)\s+([A-Za-z_]\w*)"), "type"),
        (re.compile(r"^\s*[\w\* ]+?\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*\{"), "function"),
    ],
    "ruby": [
        (re.compile(r"^\s*def\s+([A-Za-z_]\w*[?!]?)"), "function"),
        (re.compile(r"^\s*class\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*module\s+([A-Za-z_]\w*)"), "type"),
    ],
    "php": [
        (re.compile(r"^\s*(?:final\s+|abstract\s+)?(?:class|interface|trait)\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*(?:public|private|protected|static|\s)*function\s+([A-Za-z_]\w*)\s*\("), "function"),
    ],
}

IMPORT_RE = re.compile(
    r"^\s*(?:import|from|#include|use|require|require_once|using)\s+([\w./@:]+)"
)


class Symbol:
    __slots__ = ("name", "qual", "kind", "start", "end", "sig", "doc", "body")

    def __init__(self, name, qual, kind, start, end, sig, doc, body):
        self.name = name
        self.qual = qual
        self.kind = kind
        self.start = start
        self.end = end
        self.sig = sig
        self.doc = doc
        self.body = body


class FileParse:
    __slots__ = ("path", "lang", "lines", "symbols", "calls", "refs", "imports")

    def __init__(self, path, lang, lines):
        self.path = path
        self.lang = lang
        self.lines = lines
        self.symbols = []
        self.calls = []      # (qual or None, callee, line)
        self.refs = []       # (name, line)
        self.imports = []


def language(path):
    return config.CODE_EXT.get(os.path.splitext(path)[1].lower())


import os  # noqa: E402  (used by language)


def split_identifiers(name):
    """camelCase / snake_case → tokens for the text search."""
    parts = re.split(r"[_.\-]+", name)
    out = []
    for p in parts:
        if not p:
            continue
        out.append(p)
        camel = re.findall(r"[A-Z]+[a-z0-9]+|[A-Z]+|[a-z0-9]+", p)
        if len(camel) > 1:
            out.extend(camel)
    return [t for t in dict.fromkeys(out) if t not in KEYWORDS and len(t) > 1]


def parse(path, text):
    if text.startswith("\ufeff"):
        text = text[1:]
    lang = language(path) or "text"
    lines = text.splitlines()
    fp = FileParse(path, lang, lines)
    for line in lines:
        m = IMPORT_RE.match(line)
        if m:
            fp.imports.append(m.group(1))
    if lang == "python":
        _parse_python(fp, text)
    else:
        _parse_generic(fp)
    return fp


def _parse_python(fp, text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        _parse_generic(fp)
        return

    def make(node, qual, kind):
        start = node.lineno
        end = getattr(node, "end_lineno", node.lineno)
        doc = ast.get_docstring(node) or ""
        sig = fp.lines[start - 1].strip()
        body = "\n".join(fp.lines[start - 1:end])[:config.MAX_BODY_CHARS]
        fp.symbols.append(Symbol(node.name, qual, kind, start, end, sig,
                                 doc[:200], body))

    def pruned(node):
        stack = [node]
        while stack:
            n = stack.pop()
            yield n
            if n is not node and isinstance(
                n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            stack.extend(ast.iter_child_nodes(n))

    def collect(node, qual):
        for n in pruned(node):
            if isinstance(n, ast.Call):
                callee = _call_name(n.func)
                if callee:
                    fp.calls.append((qual, callee, n.lineno))
            elif isinstance(n, ast.Name):
                fp.refs.append((n.id, n.lineno))
            elif isinstance(n, ast.Attribute):
                fp.refs.append((n.attr, n.lineno))

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + child.name
                make(child, qual, "function")
                collect(child, qual)
                walk(child, qual + ".")
            elif isinstance(child, ast.ClassDef):
                qual = prefix + child.name
                make(child, qual, "class")
                collect(child, qual)
                walk(child, qual + ".")
            else:
                if isinstance(child, ast.Call):
                    callee = _call_name(child.func)
                    if callee:
                        fp.calls.append((None, callee, child.lineno))
                walk(child, prefix)

    walk(tree, "")


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id if func.id not in KEYWORDS else None
    if isinstance(func, ast.Attribute):
        return func.attr if func.attr not in KEYWORDS else None
    return None


def _parse_generic(fp):
    patterns = DEF_PATTERNS.get(fp.lang, [])
    if not patterns:
        _refs_only(fp)
        return
    for i, line in enumerate(fp.lines):
        kind = None
        name = None
        for rx, k in patterns:
            m = rx.match(line)
            if m:
                kind = k
                name = m.group(1)
                break
        if kind is None or name in KEYWORDS:
            continue
        end = _extent(fp.lines, i, fp.lang)
        body = "\n".join(fp.lines[i:end])[:config.MAX_BODY_CHARS]
        fp.symbols.append(Symbol(name, name, kind, i + 1, end, line.strip(),
                                 "", body))
        for j in range(i, end):
            for m in CALL_RE.findall(fp.lines[j]):
                if m not in KEYWORDS:
                    fp.calls.append((name, m, j + 1))
    _qualify_nested(fp)


def _qualify_nested(fp):
    """Methods inside a class extent get the qual Class.method."""
    classes = [s for s in fp.symbols if s.kind in ("class", "type")]
    for sym in fp.symbols:
        if sym.kind not in ("function", "method"):
            continue
        outer = [c for c in classes if c.start < sym.start <= c.end]
        if outer:
            inner = min(outer, key=lambda c: c.end - c.start)
            sym.qual = "%s.%s" % (inner.name, sym.name)
            fp.calls = [(inner.name + "." + callee if q == sym.name else q,
                         callee, line) for q, callee, line in fp.calls]


def _extent(lines, start, lang):
    cap = min(len(lines), start + config.MAX_EXTENT_LINES)
    if lang == "ruby":
        depth = 0
        for i in range(start, cap):
            s = lines[i].strip()
            if re.match(r"^(def|class|module|if|unless|while|for|case|begin)\b", s):
                depth += 1
            if s == "end":
                depth -= 1
                if depth <= 0:
                    return i + 1
        return start + 1
    depth = 0
    started = False
    in_str = None
    in_block = False
    for i in range(start, cap):
        line = lines[i]
        j = 0
        while j < len(line):
            ch = line[j]
            if in_block:
                if ch == "*" and j + 1 < len(line) and line[j + 1] == "/":
                    in_block = False
                    j += 2
                    continue
                j += 1
                continue
            if in_str:
                if ch == "\\":
                    j += 2
                    continue
                if ch == in_str:
                    in_str = None
                j += 1
                continue
            if ch == "/" and j + 1 < len(line) and line[j + 1] == "/":
                break
            if ch == "/" and j + 1 < len(line) and line[j + 1] == "*":
                in_block = True
                j += 2
                continue
            if ch in ('"', "'", "`"):
                in_str = ch
                j += 1
                continue
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
            j += 1
        if started and depth <= 0:
            return i + 1
    return start + 1


def _refs_only(fp):
    for i, line in enumerate(fp.lines):
        for m in IDENT_RE.findall(line):
            if m not in KEYWORDS:
                fp.refs.append((m, i + 1))
