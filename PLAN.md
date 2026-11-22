# PLAN.md — автоматическая память диалога + кода

## Цель
Make semantic-memory work as **automatic memory**: conversation events captured
without the model calling tools, retrieved facts injected into the model context,
and the code index kept fresh incrementally. `memory/<topic>.md` stays the source
of truth for facts; Python core stays the owner of embeddings and SQLite index.

## Архитектура

```
OpenCode server
 ├─ MCP stdio:  python -m semantic_memory.mcp      (явные инструменты модели, без изменений)
 └─ plugin:  .opencode/plugins/semantic-memory/     (авто-захват + инъекция)
        │  NDJSON stdin/stdout
        └─ python -m semantic_memory.bridge         (recall / add / reindex / stats)
```

- `memory/<topic>.md` — источник фактов (bullet = факт, `##` — заголовок-контекст).
- SQLite индекс в `INDEX_DIR` (по корню cwd), код индексируется инкрементально
  (sha256) уже сейчас; правки только для свежести фактов и конкурентности.
- Plugin API (V2): `ctx.session.hook("prompt"|"context"|"compaction")`,
  события `session.execution.succeeded`, `ctx.session.generate`/transient generate,
  `ctx.storage`, plugin options из `opencode.json`.

## Этап 1 — Python core

1. `semantic_memory/db.py`
   - `connect()`: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`
     (MCP stdio и bridge-субпроцесс работают с одной базой одновременно).
2. `semantic_memory/mcp_server.py`
   - `open_db()`: вместо «rebuild только если фактов 0» — всегда
     `facts.rebuild_facts(con)` (инкрементальна по hash темы; эмбеддинги только
     для изменённых тем). Внешние изменения `memory/*.md` видны сразу.
3. Новый `semantic_memory/bridge.py`
   - `python -m semantic_memory.bridge` — NDJSON по stdin/stdout:
     строка запроса `{"cmd": ..., "query": ..., "k": ..., "topic": ..., ...}` →
     одна строка JSON-ответ.
   - команды: `ping`, `recall` (search_facts), `add` (append_fact + rebuild
     changed), `reindex` (инкрементальный index_root), `stats`.
   - переиспользует `open_db()` + `call_tool()`; ошибки → `{"error": ...}`,
     процесс не падает от плохого запроса.
4. Тесты (`tests/test_bridge.py`):
   - запуск субпроцесса, `ping`, `add` → `recall` видит факт сразу;
   - внешний append bullet в `memory/<topic>.md` → `recall`/`facts_search`
     видит его без перезапуска процесса;
   - две параллельные writers (MCP-вызов + bridge) не конфликтуют (WAL).

## Этап 2 — плагин (запись фактов)

`.opencode/plugins/semantic-memory/`:
- `package.json` (deps `@opencode/plugin`), `index.ts`.
- `setup(ctx)`:
  - spawn `python -m semantic_memory.bridge` (venv-интерпретатор проекта, если
    есть `.venv`; cwd — корень проекта), cleanup при unload;
  - helper `bridge(cmd, args)` → Promise JSON (интерфейс NDJSON);
  - debounce/retry-safety: одна запись на messageID.
- Захват:
  - **эвристика**: `prompt`-хук → bullet из user-текста (truncate ~300 симв.)
    в topic `chat`; перед записью `recall`-dedupe (score выше порога → пропуск).
  - **LLM-дистилляция** (option `distill: true`): событие
    `session.execution.succeeded` → последние сообщения хода из
    `ctx.session.context({sessionID})`, prompt-шаблон «извлеки 2–4 факта
    (решения/договорённости/факты) с topic» → `ctx.session.generate` дешёвой
    моделью из options → валидация topic `[A-Za-z0-9_-]+` → `add`.
  - **compaction-хук**: transcript (`event.messages`) → LLM-дистилляция
    «что решено к этому моменту» → bullets в topic `state`
    (решения остаются живыми после компакции).
- Cap: в `add` ограничить topic (например 200 bullets, старейшие удаляются).

## Этап 3 — плагин (инъекция recalled-фактов)

- `prompt`-хук: `recall(query=user text, k=options.recallK)` → блок кэшируется
  по sessionID на ход.
- `context`-хук: `event.system.push({ type: "text", text: "<memory>...</memory>" })`
  — тихо для пользователя, влияет только на исходящий вызов модели.
- Очистка кэша при завершении хода (`session.execution.*` события).
- Формат блока: `## <topic>` + bullets; cap по числу фактов (`recallK: 5`).

## Этап 4 — опции, индекс, документы

- `opencode.json`:
  ```jsonc
  {
    "plugins": [
      { "package": "./plugins/semantic-memory",
        "options": { "model": "<дешёвая модель>", "distill": true, "inject": true, "recallK": 5 } }
    ]
  }
  ```
- триггер реиндекса кода после edit/write: `ctx.tool.hook("execute.after")`
  → `reindex` (опция `autoReindex`, по умолчанию true; дешёвый hash-pass).
- README.md: секция «Автоматическая память» — как включается, что пишется,
  privacy (`.gitignore` для `memory/`, если пользователь не хочет коммитить факты).
- MCP-инструменты (`facts_add`, `facts_search`, `index_*`) без изменений —
  остаются для явных запросов модели.

## Risks
- prompt-хуки могут выполняться повторно при retry → идемпотентность по
  messageID/requestID.
- два Python-процесса с одной базой → WAL + busy_timeout (Этап 1 обязателен).
- model-вызов distill добавляет токены на ход; дешёвая модель + `distill:false`
  выключает.
- `memory/` растёт → cap темы + ротация старых bullets (Этап 3/4).
- интерпретатор/venv плагина: если `.venv` нет, bridge падает — плагин
  логирует и деградирует в «инъекция через MCP-инструменты модели».

## Acceptance
- новый bullet из диалога появляется в `memory/` и находится `facts_search`
  без рестарта сервера;
- `<memory>`-блок добавляется к system-контексту ходов и не попадает в историю;
- после компакции решения остаются в topic `state`;
- `python -m semantic_memory.mcp` работает как раньше (MCP round-trip тесты).
