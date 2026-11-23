#!/usr/bin/env bash
# Uninstall semantic-memory: the OpenCode config entries, the plugin copies, the pip
# package, and the data it produced. Dry run by default; --apply performs the removals.
#
#   ./uninstall.sh                    show the plan, change nothing
#   ./uninstall.sh --apply            config entries, plugin copies, pip uninstall, orphaned index DBs
#   ./uninstall.sh --apply --purge    also .venv/, .models/, all of .indexes/, and memory/
#   ./uninstall.sh --apply --global   also edit ~/.config/opencode/opencode.jsonc
#   ./uninstall.sh --apply --config other/opencode.jsonc --keep-plugin --no-kill
#
# Config targets default to this project's .opencode/opencode.jsonc plus any --config paths.
# The global config is only touched with --global, since it affects every project.
# memory/ and .models/ are never removed without --purge or --remove-memory: memory/*.md
# holds the facts distilled from past sessions, and deleting it cannot be undone.
# Config edits are reversible: each edited file is copied to <file>.uninstall-backup first.
#
# Windows has no bash. Do the same steps by hand: stop the processes, drop the
# mcp.servers semantic-memory block from the config, delete .opencode/plugins/semantic-memory,
# then .venv\Scripts\python.exe -m pip uninstall semantic-memory.
set -euo pipefail

NAME="semantic-memory"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

APPLY=0
PURGE=0
REMOVE_MEMORY=0
NO_BACKUP=0
KEEP_CONFIG=0
KEEP_PLUGIN=0
KEEP_PYTHON=0
KEEP_INDEXES=0
KEEP_MODELS=0
KEEP_MEMORY=0
KILL=1
GLOBAL_CONFIG=0
EXTRA_CONFIGS=()

usage() {
  sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while (($#)); do
  case "$1" in
    --apply) APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    --purge) PURGE=1 ;;
    --remove-memory) REMOVE_MEMORY=1 ;;
    --keep-memory) KEEP_MEMORY=1 ;;
    --keep-config) KEEP_CONFIG=1 ;;
    --keep-plugin) KEEP_PLUGIN=1 ;;
    --keep-python) KEEP_PYTHON=1 ;;
    --keep-indexes) KEEP_INDEXES=1 ;;
    --keep-models) KEEP_MODELS=1 ;;
    --global) GLOBAL_CONFIG=1 ;;
    --no-kill) KILL=0 ;;
    --no-backup) NO_BACKUP=1 ;;
    --config)
      EXTRA_CONFIGS+=("$2")
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "unknown flag: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ((KEEP_MEMORY)); then
  REMOVE_MEMORY=0
  PURGE=0
fi

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

# Prints what a step would do, and does it only under --apply.
run() {
  local label="$1"
  shift
  if ((APPLY)); then
    printf '  do   %s\n' "$label"
    "$@"
  else
    printf '  plan %s\n' "$label"
  fi
}

# The MCP server and the plugin bridge only: python running run_mcp.py or the bridge
# module. pgrep -f "$NAME" would also match shells that merely mention the name.
server_pids() {
  local pid cmd bin
  while read -r pid cmd; do
    bin="${cmd%% *}"
    case "$bin" in
      *python | *python3 | *python.exe | *pythonw) ;;
      *) continue ;;
    esac
    # Anchored forms only: a mention of the file inside python -c code is not the server.
    case "$cmd" in *" -c "*) continue ;; esac
    case "$cmd" in
      *run_mcp.py | *run_mcp.py\ * | *" -m semantic_memory.bridge"*) printf '%s\n' "$pid" ;;
    esac
  done < <(ps -eo pid=,cmd=)
}

mode="dry run"
if ((APPLY)); then
  mode="apply"
fi
echo "semantic-memory uninstall ($mode) from $ROOT"

if ((KILL)); then
  pids="$(server_pids || true)"
  if [[ -n "$pids" ]]; then
    run "stop processes: $(echo "$pids" | tr '\n' ' ')" kill $pids
  else
    echo "  skip no $NAME processes running"
  fi
fi

if ((KEEP_PLUGIN)); then
  echo "  skip plugin copies (--keep-plugin)"
else
  # The repo copy is tracked by git; copies in other projects came from init.py --plugin.
  copies="$(find "$HOME" -path "*/.opencode/plugins/$NAME" -not -path "*/.venv/*" 2>/dev/null | sort || true)"
  if [[ -z "$copies" ]]; then
    echo "  skip no plugin copies found"
  fi
  for copy in $copies; do
    if [[ "$copy" == "$ROOT/.opencode/plugins/$NAME" ]] &&
      git -C "$ROOT" ls-files --error-unmatch ".opencode/plugins/$NAME/index.ts" >/dev/null 2>&1; then
      run "git rm $copy" git -C "$ROOT" rm -r --quiet "$copy"
    else
      run "delete $copy" rm -rf "$copy"
    fi
  done
fi

if ((KEEP_CONFIG)); then
  echo "  skip config entries (--keep-config)"
else
  configs=()
  if ((GLOBAL_CONFIG)); then
    [[ -f "$HOME/.config/opencode/opencode.jsonc" ]] && configs+=("$HOME/.config/opencode/opencode.jsonc")
  elif grep -q "$NAME" "$HOME/.config/opencode/opencode.jsonc" 2>/dev/null; then
    echo "  note the global config lists $NAME too; add --global to remove it"
  fi
  [[ -f "$ROOT/.opencode/opencode.jsonc" ]] && configs+=("$ROOT/.opencode/opencode.jsonc")
  for extra in ${EXTRA_CONFIGS[@]+"${EXTRA_CONFIGS[@]}"}; do
    configs+=("$extra")
  done
  if [[ ${#configs[@]} -eq 0 ]]; then
    echo "  skip no config files to inspect"
  else
    APPLY="$APPLY" "$PYTHON" - "$NAME" "${configs[@]}" <<'PY'
import difflib
import os
import re
import sys

name = sys.argv[1]
bare = name.strip('"')
apply = os.environ.get("APPLY") == "1"
key = '"%s"' % name
ws = " \t\r\n"
plugins_re = re.compile(r'"plugins?"\s*:\s*\[')


def comment_ranges(text):
    """(start, end) spans of // and /* */ comments, strings respected."""
    spans = []
    i = 0
    in_str = False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if text[i : i + 2] == "//":
            start = i
            i += 2
            while i < len(text) and text[i] != "\n":
                i += 1
            spans.append((start, i))
            continue
        if text[i : i + 2] == "/*":
            start = i
            end = text.find("*/", i + 2)
            if end == -1:
                spans.append((start, len(text)))
                break
            spans.append((start, end + 2))
            i = end + 2
            continue
        i += 1
    return spans


def in_comment(spans, i):
    return any(start <= i < end for start, end in spans)


def strip_comments(text):
    """Copy of text with every comment span removed, for the balance guard."""
    for start, end in reversed(comment_ranges(text)):
        text = text[:start] + text[end:]
    return text


def find_key(text):
    """Offset of the first real occurrence of key, ignoring commented-out text, or None."""
    spans = comment_ranges(text)
    pos = 0
    while True:
        i = text.find(key, pos)
        if i == -1:
            return None
        if not in_comment(spans, i):
            return i
        pos = i + len(key)


def match_bracket(text, open_idx):
    """Index of the bracket closing text[open_idx], respecting strings, or None."""
    opener = text[open_idx]
    closer = {"{": "}", "[": "]"}[opener]
    depth = 0
    j = open_idx
    in_str = escaped = False
    while j < len(text):
        c = text[j]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return None


def quoted_end(text, i):
    """Offset just past the quoted string starting at text[i], or None."""
    j = i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == '"':
            return j + 1
        j += 1
    return None


def array_elements(text, open_idx, close_idx):
    """(start, end) ranges of the top-level elements of an array, or None."""
    elements = []
    j = open_idx + 1
    while j < close_idx:
        if text[j] in ws or text[j] == ",":
            j += 1
            continue
        start = j
        if text[j] in "[{":
            close = match_bracket(text, j)
            if close is None:
                return None
            end = close + 1
        elif text[j] == '"':
            end = quoted_end(text, j)
            if end is None:
                return None
        else:
            end = j
            while end < close_idx and text[end] not in ",\r\n":
                end += 1
        elements.append((start, end))
        j = end
    return elements


def collapse(head, rest):
    """Keep one space where two runs of whitespace met after a removal."""
    if head.endswith((" ", "\t")) and rest.startswith((" ", "\t")):
        return head[:-1] + rest
    if head.endswith("[") and rest.startswith((" ", "\t")):
        return head + rest.lstrip(" \t")
    return head + rest


def remove_span(text, start, end):
    """Drop text[start:end] and the comma that separated it from its neighbours.

    Lines the span owns outright (nothing else on them) are removed whole, so no
    blank line or dangling comma is left behind.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    alone = text[line_start:start].strip() == "" and text[end:line_end].strip() in ("", ",")
    j = end
    while j < len(text) and text[j] in ws:
        j += 1
    comma_after = j < len(text) and text[j] == ","
    if comma_after and alone:
        return text[:line_start] + text[line_end + 1:]
    if comma_after:
        return collapse(text[:start], text[j + 1:])
    # Nothing but the closing bracket follows, so this was the last entry and the
    # comma before it is now a trailing comma.
    k = line_start if alone else start
    while k > 0 and text[k - 1] in ws:
        k -= 1
    tail = line_end if alone else end
    if k > 0 and text[k - 1] == ",":
        return collapse(text[:k - 1], text[tail:])
    return collapse(text[:line_start if alone else start], text[tail:])


def strip_plugins(text):
    """Drop plugin array elements referencing the plugin by package path or name."""
    out = text
    while True:
        spans = comment_ranges(out)
        found = None
        for m in plugins_re.finditer(out):
            if not in_comment(spans, m.start()):
                found = m
                break
        if found is None:
            return out
        open_idx = found.end() - 1
        close_idx = match_bracket(out, open_idx)
        if close_idx is None:
            return out
        elements = array_elements(out, open_idx, close_idx)
        if elements is None:
            return out
        targets = [span for span in elements if bare in out[span[0]:span[1]]]
        if not targets:
            return out
        before = out
        for start, end in reversed(targets):
            out = remove_span(out, start, end)
        if out == before:
            return out  # nothing removable: stop rather than loop


def strip_entry(text):
    """Remove the first entry named key: an object or string value, or an array element."""
    i = find_key(text)
    if i is None:
        return None
    j = i + len(key)
    while j < len(text) and text[j] in ws:
        j += 1
    if j >= len(text):
        return None
    if text[j] == ":":
        k = j + 1
        while k < len(text) and text[k] in ws:
            k += 1
        if k >= len(text):
            return None
        if text[k] == "{":
            close = match_bracket(text, k)
            if close is None:
                return None
            return remove_span(text, i, close + 1)
        if text[k] == '"':
            end = quoted_end(text, k)
            if end is None:
                return None
            return remove_span(text, i, end)
        return None
    if text[j] in (",", "]", "}") or text[j] in ws:
        return remove_span(text, i, i + len(key))
    return None


def balanced(text):
    """Balanced braces and brackets outside strings and comments: the guard before writing a config back."""
    depth = 0
    in_str = escaped = False
    for c in strip_comments(text):
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_str


for path in sys.argv[2:]:
    if not os.path.isfile(path):
        print("  skip %s: no such file" % path)
        continue
    text = open(path, encoding="utf-8").read()
    if bare not in text:
        print("  skip %s: no %s entry" % (path, bare))
        continue
    new = strip_plugins(text)
    while True:
        if find_key(new) is None:
            break
        stripped = strip_entry(new)
        if stripped is None or stripped == new:
            print("  skip %s: the %s entry is not a plain object, string or array element; edit it by hand" % (path, bare))
            new = text
            break
        new = stripped
    if new == text:
        continue
    if not balanced(new):
        print("  skip %s: refusing to write, the result is not balanced" % path)
        continue
    print("  config %s" % path)
    for line in difflib.unified_diff(text.splitlines(True), new.splitlines(True),
                                     fromfile=path + ".before", tofile=path + ".after"):
        print("    " + line.rstrip())
    if apply:
        backup = path + ".uninstall-backup"
        if not os.path.exists(backup):
            open(backup, "w", encoding="utf-8").write(text)
        open(path, "w", encoding="utf-8").write(new)
        print("    wrote %s (backup %s)" % (path, backup))
PY
  fi
fi

if ((KEEP_PYTHON)); then
  echo "  skip pip package (--keep-python)"
else
  if "$PYTHON" -m pip show "$NAME" >/dev/null 2>&1; then
    run "pip uninstall $NAME ($PYTHON)" "$PYTHON" -m pip uninstall -y "$NAME"
  else
    echo "  skip $NAME is not installed in $PYTHON"
  fi
fi

if ((KEEP_INDEXES)); then
  echo "  skip index databases (--keep-indexes)"
elif ((PURGE)); then
  [[ -d "$ROOT/.indexes" ]] && run "delete all index data $ROOT/.indexes" rm -rf "$ROOT/.indexes"
else
  # Orphans only: index files whose project root no longer exists.
  [[ -d "$ROOT/.indexes" ]] && run "collect orphaned index files" "$PYTHON" -m semantic_memory.cli gc
fi

if ((KEEP_MODELS)); then
  echo "  skip embedding model (--keep-models)"
elif ((PURGE)) && [[ -d "$ROOT/.models" ]]; then
  run "delete downloaded model cache $ROOT/.models" rm -rf "$ROOT/.models"
fi

if ((KEEP_MEMORY)) || ! ((REMOVE_MEMORY || PURGE)); then
  echo "  keep memory/*.md (add --remove-memory or --purge to delete)"
elif [[ ! -d "$ROOT/memory" ]]; then
  echo "  skip no memory directory"
elif ((NO_BACKUP)); then
  run "delete $ROOT/memory (no backup)" rm -rf "$ROOT/memory"
else
  dest="${TMPDIR:-/tmp}/semantic-memory-memory-$(date +%Y%m%d-%H%M%S).tar.gz"
  run "back up then delete $ROOT/memory" bash -c \
    "tar -czf '$dest' -C '$ROOT' memory && rm -rf '$ROOT/memory' && echo '    saved $dest'"
fi

if ((PURGE)) && [[ -d "$ROOT/.venv" ]]; then
  run "delete the venv $ROOT/.venv" rm -rf "$ROOT/.venv"
fi

echo
if ((APPLY)); then
  left="$(server_pids || true)"
  echo "remaining processes: ${left:-none}"
  echo "restart OpenCode and check: opencode mcp list"
else
  echo "nothing changed; rerun with --apply"
fi
