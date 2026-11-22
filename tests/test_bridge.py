r"""Test for the NDJSON bridge used by plugins: subprocess python -m semantic_memory.bridge.

Run: python tests/test_bridge.py
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.makedirs(os.path.join(TMP, "src"), exist_ok=True)
with open(os.path.join(TMP, "src", "app.py"), "w", encoding="utf-8") as f:
    f.write("def build_cache():\n    \"\"\"Document cache.\"\"\"\n    return {}\n")


def start_bridge():
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["SEMANTIC_MEMORY_DB"] = os.path.join(TMP, "index.db")
    env["SEMANTIC_MEMORY_MEMORY_DIR"] = os.path.join(TMP, "memory")
    proc = subprocess.Popen(
        [sys.executable, "-m", "semantic_memory.bridge"],
        cwd=TMP, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    return proc


def send(proc, payload):
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def main():
    proc = start_bridge()
    try:
        ping = send(proc, {"id": 1, "cmd": "ping"})
        assert ping["ok"] and ping["id"] == 1, ping
        assert os.path.abspath(ping["root"]) == os.path.abspath(TMP), ping["root"]

        added = send(proc, {"id": 2, "cmd": "add", "text": "Cooling fan: 120 mm, 12 W.", "topic": "hardware"})
        assert added["ok"] and added["topic"] == "hardware", added
        assert added["facts"] >= 1, added

        hits = send(proc, {"id": 3, "cmd": "recall", "query": "cooling fan", "k": 3})
        assert hits["ok"] and hits["id"] == 3, hits
        assert any("cooling fan" in f["text"].lower() for f in hits["facts"]), hits

        # a bullet written into memory/*.md is picked up without restarting the process
        memory_file = os.path.join(TMP, "memory", "decisions.md")
        with open(memory_file, "w", encoding="utf-8") as f:
            f.write("# decisions\n\n- The response cache takes 64 MB.\n")
        hits = send(proc, {"id": 4, "cmd": "recall", "query": "cache 64 MB", "k": 3})
        assert any("64 MB" in f["text"] for f in hits["facts"]), hits

        reindex = send(proc, {"id": 4, "cmd": "reindex"})
        assert reindex["ok"], reindex
        with open(os.path.join(TMP, "src", "cache.py"), "w", encoding="utf-8") as f:
            f.write("def warm():\n    return 1\n")
        # open_db() indexes the root on every request, so the index shows up
        # in stats; force=true re-reads the files and returns their list.
        stats = send(proc, {"id": 5, "cmd": "stats"})
        assert stats["ok"] and stats["files"] >= 1, stats
        forced = send(proc, {"id": 6, "cmd": "reindex", "force": True})
        assert forced["ok"] and forced["files"] >= 1, forced
        assert any("cache.py" in p for p in forced["changed"]), forced

        bad = send(proc, {"id": 7, "cmd": "unknown"})
        assert not bad["ok"] and bad["id"] == 7, bad
        proc.stdin.write("this is not json\n")
        proc.stdin.flush()
        bad2 = json.loads(proc.stdout.readline())
        assert not bad2["ok"], bad2

        stats = send(proc, {"id": 8, "cmd": "stats"})
        assert stats["ok"] and stats["facts"] >= 2, stats
    finally:
        proc.stdin.close()
        proc.kill()
        proc.wait(timeout=5)
    print("bridge OK")


if __name__ == "__main__":
    main()
