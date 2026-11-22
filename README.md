# semantic-memory

Поисковая память для LLM: факты проекта + индекс кода, один гибридный движок,
MCP-сервер для harness.

## Что здесь есть

- Факты проекта читаются из `memory/*.md` в корне проекта (единственный источник
  памяти): один bullet = один факт, `##` = раздел, тема — имя файла. Путь
  переопределяется через `SEMANTIC_MEMORY_MEMORY_DIR`.
- `semantic_memory/` — пакет:
  - `facts.py` — факты по темам, индекс перестраивается только для изменённых тем;
  - `codeparse.py` — разбор кода: Python через `ast`, остальные языки
    (js/ts/go/rust/c/cpp/cs/rb/php) — определение + extent тела по скобкам;
  - `codeindex.py` — обход корня, символы, вызовы, ссылки, резюме файла;
    файл перестраивается только при изменении sha256;
  - `search.py` — гибрид: SQLite FTS5 по токенам (включая разбор camelCase) +
    векторы MiniLM, слияние по рангам;
  - `mcp_server.py` — MCP-сервер на stdio, 12 инструментов, без внешних зависимостей;
  - `bridge.py` — NDJSON-мост для плагина harness (`recall` / `add` / `reindex` / `stats`);
  - `cli.py` — тот же функционал из командной строки.
- `init.py` — venv, пакеты, кэш модели, сборка индекса.
- `.opencode/plugins/semantic-memory/` — плагин OpenCode V2: авто-захват диалога и
  инъекция recalled-фактов (см. «Автопамять»).
- `tests/test_semantic_memory.py` — smoke-тесты разбора, индекса, поиска и MCP.
- `tests/test_bridge.py` — smoke-тест NDJSON-моста.

## Зачем не просто grep

LLM читает код фрагментами и теряет связи: кто вызывает функцию, где её определения,
что делает файл. Здесь вместо фрагментов — символ целиком (`файл:строки` + тело),
справочник вызовов и карта файлов. Точный поиск по идентификаторам и семантический
поиск по описанию работают вместе: `code_search "hybrid ranking"` находит
`search_symbols`, даже если слово «ranking» в коде не написано.

## Запуск

Собирается одинаково на Windows и Linux; отличается только путь к python в venv
(`.venv\Scripts\python.exe` и `.venv/bin/python`).

Windows (PowerShell):

```powershell
python init.py                 # venv + пакеты + индекс (cwd)
python init.py --check         # состояние без правок
.venv\Scripts\python.exe -X utf8 -m semantic_memory.cli code "hybrid search" -k 6
```

Linux (bash):

```bash
python3 init.py                # venv + пакеты + индекс (cwd)
python3 init.py --check
.venv/bin/python -m semantic_memory.cli code "hybrid search" -k 6
```

CLI (команды те же на обеих системах, из папки semantic-memory; корень кода —
cwd, переопределяется `--root`):

```bash
.venv/bin/python -m semantic_memory.cli index
.venv/bin/python -m semantic_memory.cli symbol Store.load
.venv/bin/python -m semantic_memory.cli callers build_index
.venv/bin/python -m semantic_memory.cli facts "кулер" --topic hardware
.venv/bin/python -m semantic_memory.cli add "БП: 550 Вт, 80+" --topic hardware
.venv/bin/python -m semantic_memory.cli stats
```

На Windows вместо `.venv/bin/python` — `.venv\Scripts\python.exe`, а вместо
`export VAR=…` — `$env:VAR = …`. Тесты: `python tests/test_semantic_memory.py`
(на Windows `python tests\test_semantic_memory.py`).

## Кроссплатформенность

- Пути в индексе хранятся относительными и с `/` (`codeindex.rel`), запросы вида
  `semantic_memory/search.py` работают на обеих системах; `~` раскрывается в
  `--root` CLI и `SEMANTIC_MEMORY_ROOT` init.py.
- SQLite, `ast`, regex-разбор и `fastembed` (ONNX) есть на Windows и Linux; кэш модели
  лежит в `.models`, скачивается при первом запуске, если донора нет.
- Вывод — UTF-8: на Windows CLI перестраивает stdout, на Linux кодировка по умолчанию.
- `.gitattributes` фиксирует LF в текстовых файлах, чтобы checkout на Windows не
  менял переводы строк.
- Проверено на Windows и Linux (тесты + MCP по stdio).

## MCP для harness

Сервер: `python /path/to/semantic-memory/run_mcp.py` — можно запустить из любой
директории; или `python -m semantic_memory.mcp` из папки semantic-memory (тот же
`mcp_server.serve()` из `semantic_memory.cli serve`). Корень кода — рабочая
директория процесса (cwd сессии harness). Первый вызов инструмента строит индекс
под текущий корень в отдельный файл `.indexes/<hash корня>.db`: каждый проект
хранит свой индекс, переключение между директориями не трогает чужие, а
неизменённые файлы пропускаются по sha256. `SEMANTIC_MEMORY_DB` задаёт один
общий файл на все корни (тогда новый корень очищает индекс старого). Факты
читаются из `memory/*.md` в файл каждого корня — источник один.

### OpenCode

V2 читает серверы под `mcp.servers` (не `mcpServers`), локальный сервер —
`"type": "local"` и `command` одним массивом. В глобальный конфиг
`~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "mcp": {
    "servers": {
      "semantic-memory": {
        "type": "local",
        "command": ["/path/to/semantic-memory/.venv/bin/python",
                    "/path/to/semantic-memory/run_mcp.py"]
      }
    }
  }
}
```

На Windows те же пути: `.venv\\Scripts\\python.exe` и `run_mcp.py`. Тот же блок
пишет CLI:

```bash
opencode mcp add semantic-memory --global -- \
  /path/to/semantic-memory/.venv/bin/python \
  /path/to/semantic-memory/run_mcp.py
```

`cwd` не указывайте: OpenCode запускает сервер в рабочей директории открытого
проекта — именно её индекс получает сессия. `pool: true` тоже не ставьте: он
делает одно соединение на все директории и ломает индекс по cwd.

Проверка: `/mcps` в TUI или `opencode mcp list` — `✓ semantic-memory connected`.
Инструменты видны Code Mode как `tools.semantic-memory.*`.

### Автопамять (плагин OpenCode)

Каталог `.opencode/plugins/semantic-memory/` OpenCode грузит сам (плагины из
`.opencode/plugins/` подхватываются автоматически, отдельных правок в конфиг не
нужно). Плагин не трогает MCP-инструменты — он добавляет захват и инъекцию без
вызовов модели:

- `prompt` hook — recalled-факты по тексту реплики и bullet в тему `chat`; эхо
  собственного запроса из recall отсеивается, дубли bullets проверяются по recalled.
- `context` hook — блок `<memory>` добавляется в system на каждый ход агент-цикла,
  включая tool-continuation (история не меняется).
- `session.execution.succeeded` — конец хода: модель из хвоста диалога извлекает
  0–3 факта и пишет их в тему `chat` (или в тему из ответа модели).
- `compaction` hook — перед свёрткой transcript пишет состояние в тему `state`.
- `tool.execute.after` — после `edit` / `write` / `apply_patch` вызывает reindex,
  так что индекс кода остаётся свежим в ходе хода.

Мост — отдельный процесс `python -m semantic_memory.bridge` на NDJSON (строка
запроса → строка ответа с тем же `id`): эмбеддинги, FTS и инкрементальный индекс
остаются в Python, плагин только вызывает команды. Мост открывает ту же базу, что
MCP-сервер; `journal_mode=WAL` и `busy_timeout` в `db.connect`, поэтому плагин и
MCP работают параллельно. Тема хранит не больше `config.MAX_TOPIC_BULLETS` bullets
(старые удаляются). Python моста: option `python` → `SEMANTIC_MEMORY_PYTHON` →
`.venv/bin/python` (`.venv\Scripts\python.exe` на Windows) → `python3`.

Опции задаются только когда плагин подключен явно (авто-discovery использует
значения по умолчанию):

```jsonc
{
  "plugins": [
    {
      "package": "./.opencode/plugins/semantic-memory",
      "options": {
        "python": "/path/to/semantic-memory/.venv/bin/python",
        "model": { "providerID": "localapi", "id": "gyro-gyro-s" },
        "distill": true,
        "inject": true,
        "captureChat": true,
        "debug": false,
        "recallK": 5,
        "chatTopic": "chat",
        "stateTopic": "state",
        "reindexTools": ["edit", "write", "apply_patch"]
      }
    }
  ]
}
```

`model` — модель для извлечения фактов (`ctx.experimental.generate.text`); без неё
извлечение идёт транзитным запросом `ctx.session.generate` на модели сессии.
`distill: false` оставляет только захват реплик и инъекцию. `debug: true` (или
`SEMANTIC_MEMORY_DEBUG=1`) пишет в stderr recall/inject/distill — вместе с
`opencode run --print-logs` видно `[semantic-memory] inject → system: N`.
Проверка: `memory/chat.md` растёт с репликами, `memory/state.md` — со свёртками.

### Другие harness

Формат `mcpServers` (Claude, Cursor и т. п.), stdio. `cwd` не указывайте —
harness запускает сервер в рабочей директории сессии, её и индексируем:

```json
{
  "mcpServers": {
    "semantic-memory": {
      "command": "C:\\Users\\%USERNAME%\\semantic-memory\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\%USERNAME%\\semantic-memory\\run_mcp.py"]
    }
  }
}
```

Linux — тот же блок, только пути:

```json
{
  "mcpServers": {
    "semantic-memory": {
      "command": "/path/to/workspace/semantic-memory/.venv/bin/python",
      "args": ["/path/to/workspace/semantic-memory/run_mcp.py"]
    }
  }
}
```

Проверка без harness — запросы по stdin:

```powershell
Get-Content tests\mcp_requests.jsonl | .venv\Scripts\python.exe -m semantic_memory.mcp
```

```bash
cat tests/mcp_requests.jsonl | .venv/bin/python -m semantic_memory.mcp
```

Инструменты: `code_search`, `symbol`, `callers`, `callees`, `references`,
`file_outline`, `file_read`, `project_map`, `facts_search`, `facts_add`,
`index_rebuild`, `index_stats`.

## Ограничения

- Разбор не-Python файлов — приближённый: строка определения и extent по скобкам.
  Для точных границ нужен tree-sitter (не установлен).
- Векторы требуют `fastembed` и кэш ONNX (~240 МБ, в `.gitignore`); без них
  поиск работает текстом.
- Эмбеддинги пересчитываются для изменённых файлов и тем; удалённые файлы снимаются
  из индекса автоматически.
- Файлы больше `config.MAX_FILE_BYTES` и папки из `config.SKIP_DIRS` не индексируются.
- Индексы — файлы в `.indexes` (в `.gitignore`): сессии с разными cwd работают
  параллельно и не мешают друг другу; один и тот же cwd делит одну SQLite-базу.
