r"""Reporting-only A/B session harness (run before a release, never in CI).

Usage:
    python bench/ab.py --runs 3 --model <provider/model>

Condition A attaches the plugin and this repository's MCP server; condition B
removes the plugin and overrides MCP servers with `false` (OpenCode 2.1.3 ignores
`mcp.servers.<name>.disabled`). Three deterministic scenarios are run in fresh
temporary projects. The table is written to bench/results/<date>.md.

Requires the `opencode` CLI on PATH. When it is absent the script prints a
message and exits 0 — measurement is optional, offline gates are not.
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_SRC = os.path.join(ROOT, ".opencode", "plugins", "semantic-memory")
VENV_PY = os.path.join(ROOT, ".venv", "bin", "python")
if os.name == "nt":
    VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.isfile(VENV_PY):
    VENV_PY = "python3"

SCENARIOS = {
    "callers": {
        "files": {"src/core.py": "def helper():\n    return 1\n\ndef main():\n    return helper()\n"},
        "prompt": "Which function calls helper() in src/core.py? Answer with the caller name only.",
        "check": lambda text, _: "main" in text.lower(),
    },
    "convention": {
        "files": {
            "config.toml": "maxRetries = 3\ntimeoutSeconds = 10\n",
        },
        "prompt": "In config.toml, rename the keys to snake_case and make all output English. "
                  "Report the resulting keys.",
        "check": lambda _, files: "max_retries" in files.get("config.toml", "")
        and "timeout_seconds" in files.get("config.toml", ""),
    },
    "decision": {
        "files": {"memory/decisions.md": "# decisions\n\n## Decision stream\n\n"
                                         "- The bridge token limit is ZZ-4211.\n"},
        "prompt": "What is the bridge token limit recorded in project memory? "
                  "Answer with the identifier only.",
        "check": lambda text, _: "ZZ-4211" in text,
    },
}


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def config_for(condition, fixture):
    plugin = os.path.join(fixture, ".opencode", "plugins", "semantic-memory")
    if condition == "on":
        shutil.copytree(PLUGIN_SRC, plugin, dirs_exist_ok=True)
        servers = {"semantic-memory": {"type": "local", "command": [VENV_PY,
                                                                     os.path.join(ROOT, "run_mcp.py")]}}
        plugins = [{"package": plugin, "options": {"python": VENV_PY}}]
    else:
        servers = {"semantic-memory": {"type": "local", "command": ["/usr/bin/false"]}}
        plugins = ["-semantic-memory"]
    write_json(os.path.join(fixture, "opencode.json"),
               {"mcp": {"servers": servers}, "plugins": plugins})


def run_case(condition, name, scenario, model):
    fixture = tempfile.mkdtemp(prefix="sm-ab-")
    try:
        config_for(condition, fixture)
        for rel, text in scenario["files"].items():
            full = os.path.join(fixture, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(text)
        env = dict(os.environ, PYTHONPATH=ROOT)
        cmd = ["opencode", "run", "--session", "%s-%s" % (condition, name),
               "--format", "json", "--auto"]
        if model:
            cmd += ["--model", model]
        cmd.append(scenario["prompt"])
        proc = subprocess.run(cmd, cwd=fixture, env=env, capture_output=True,
                              text=True, timeout=600)
        answer = proc.stdout
        try:
            answer = "\n".join(str(m.get("text", "")) for m in json.loads(proc.stdout)
                               if isinstance(m, dict))
        except ValueError:
            pass
        files = {rel: open(os.path.join(fixture, rel), encoding="utf-8").read()
                 for rel in scenario["files"] if os.path.isfile(os.path.join(fixture, rel))}
        exported = subprocess.run(["opencode", "session", "export", "%s-%s" % (condition, name)],
                                  cwd=fixture, capture_output=True, text=True, timeout=120)
        tokens = cost = 0
        try:
            messages = json.loads(exported.stdout)
            for message in messages if isinstance(messages, list) else [messages]:
                info = (message or {}).get("tokens") or {}
                tokens += info.get("input", 0) or 0
                cost += (message or {}).get("cost", 0) or 0
        except ValueError:
            pass
        return {
            "condition": condition, "scenario": name,
            "pass": scenario["check"](answer, files),
            "input_tokens": tokens, "cost": round(cost, 5),
            "memory_created": os.path.isdir(os.path.join(fixture, "memory")),
        }
    finally:
        shutil.rmtree(fixture, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    if shutil.which("opencode") is None:
        print("opencode CLI not found; A/B harness skipped (reporting only).")
        return 0
    rows = []
    for run in range(args.runs):
        for name, scenario in SCENARIOS.items():
            for condition in ("on", "off"):
                rows.append(run_case(condition, "%s-%d" % (name, run), scenario, args.model))
    output = args.output or os.path.join(ROOT, "bench", "results",
                                         datetime.date.today().isoformat() + ".md")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    lines = ["# A/B session results — %s" % datetime.date.today().isoformat(), "",
             "| condition | scenario | pass | input tokens | cost | memory dir |",
             "|---|---|---|---:|---:|---|"]
    for row in rows:
        lines.append("| %s | %s | %s | %d | %.5f | %s |" % (
            row["condition"], row["scenario"], "yes" if row["pass"] else "no",
            row["input_tokens"], row["cost"], "yes" if row["memory_created"] else "no"))
    lines += ["", "Pass rate: %d/%d" % (sum(r["pass"] for r in rows), len(rows)), ""]
    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote %s (%d/%d passed)" % (output, sum(r["pass"] for r in rows), len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
