r"""Тест NDJSON-моста для плагинов: subprocess python -m semantic_memory.bridge.

Запуск: python tests/test_bridge.py
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
    f.write("def build_cache():\n    \"\"\"Кэш документов.\"\"\"\n    return {}\n")


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

        added = send(proc, {"id": 2, "cmd": "add", "text": "Кулер: 120 мм, 12 Вт.", "topic": "hardware"})
        assert added["ok"] and added["topic"] == "hardware", added
        assert added["facts"] >= 1, added

        hits = send(proc, {"id": 3, "cmd": "recall", "query": "кулер", "k": 3})
        assert hits["ok"] and hits["id"] == 3, hits
        assert any("кулер" in f["text"].lower() for f in hits["facts"]), hits

        # внешний bullet в memory/*.md подхватывается без перезапуска процесса
        memory_file = os.path.join(TMP, "memory", "decisions.md")
        with open(memory_file, "w", encoding="utf-8") as f:
            f.write("# decisions\n\n- Кэш ответов занимает 64 МБ.\n")
        hits = send(proc, {"id": 4, "cmd": "recall", "query": "кэш 64 МБ", "k": 3})
        assert any("64 МБ" in f["text"] for f in hits["facts"]), hits

        reindex = send(proc, {"id": 4, "cmd": "reindex"})
        assert reindex["ok"], reindex
        with open(os.path.join(TMP, "src", "cache.py"), "w", encoding="utf-8") as f:
            f.write("def warm():\n    return 1\n")
        # open_db() индексирует корень на каждом запросе, поэтому индекс виден
        # в stats; force=true перечитывает файлы и возвращает их список.
        stats = send(proc, {"id": 5, "cmd": "stats"})
        assert stats["ok"] and stats["files"] >= 1, stats
        forced = send(proc, {"id": 6, "cmd": "reindex", "force": True})
        assert forced["ok"] and forced["files"] >= 1, forced
        assert any("cache.py" in p for p in forced["changed"]), forced

        bad = send(proc, {"id": 7, "cmd": "unknown"})
        assert not bad["ok"] and bad["id"] == 7, bad
        proc.stdin.write("это не json\n")
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
