# semantic-memory

Поисковая память для LLM: факты проекта + индекс кода, один гибридный движок,
MCP-сервер для harness.

## Что здесь есть

- Факты проекта читаются из `../mem0-like/memory/*.md` (единственный источник
  памяти): один bullet = один факт, `##` = раздел. Темы: `hardware`, `game-design`,
  `flow`. Своя папка `memory/` используется только если её нет; путь переопределяется
  через `SEMANTIC_MEMORY_MEMORY_DIR`.
- `semantic_memory/` — пакет:
  - `facts.py` — факты по темам, индекс перестраивается только для изменённых тем;
  - `codeparse.py` — разбор кода: Python через `ast`, остальные языки
    (js/ts/go/rust/c/cpp/cs/rb/php) — определение + extent тела по скобкам;
  - `codeindex.py` — обход корня, символы, вызовы, ссылки, резюме файла;
    файл перестраивается только при изменении sha256;
  - `search.py` — гибрид: SQLite FTS5 по токенам (включая разбор camelCase) +
    векторы MiniLM, слияние по рангам;
  - `mcp_server.py` — MCP-сервер на stdio, 12 инструментов, без внешних зависимостей;
  - `cli.py` — тот же функционал из командной строки.
- `init.py` — venv, пакеты, кэш модели, сборка индекса.
- `tests/test_semantic_memory.py` — smoke-тесты разбора, индекса, поиска и MCP.

## Зачем не просто grep

LLM читает код фрагментами и теряет связи: кто вызывает функцию, где её определения,
что делает файл. Здесь вместо фрагментов — символ целиком (`файл:строки` + тело),
справочник вызовов и карта файлов. Точный поиск по идентификаторам и семантический
поиск по описанию работают вместе: `code_search "hybrid ranking"` находит
`search_symbols`, даже если слово «ranking» в коде не написано.

## Запуск

```powershell
python init.py                 # venv + пакеты + индекс
python init.py --check         # состояние без правок
```

CLI (из папки semantic-memory):

```powershell
python -m semantic_memory.cli index
python -m semantic_memory.cli code "hybrid search" -k 6
python -m semantic_memory.cli symbol Store.load
python -m semantic_memory.cli callers build_index
python -m semantic_memory.cli facts "кулер" --topic hardware
python -m semantic_memory.cli add "БП: 550 Вт, 80+" --topic hardware
python -m semantic_memory.cli stats
```

Тесты:

```powershell
python tests\test_semantic_memory.py
```

## MCP для harness

Сервер: `python -m semantic_memory.mcp` (тот же `mcp_server.serve()` из
`semantic_memory.cli serve`). Корень кода — `SEMANTIC_MEMORY_ROOT` (по умолчанию
cwd процесса), индекс — `SEMANTIC_MEMORY_DB` (по умолчанию `index.db`). Первый
вызов инструмента строит индекс, если он пуст.

Конфиг в формате harness (`mcpServers`, stdio):

```json
{
  "mcpServers": {
    "semantic-memory": {
      "command": "C:\\Users\\Artem\\Documents\\Strata\\Workspace\\semantic-memory\\.venv\\Scripts\\python.exe",
      "args": ["-m", "semantic_memory.mcp"],
      "cwd": "C:\\Users\\Artem\\Documents\\Strata\\Workspace\\semantic-memory",
      "env": { "SEMANTIC_MEMORY_ROOT": "C:\\Users\\Artem\\Documents\\Strata\\Workspace" }
    }
  }
}
```

Проверка без harness — запросы по stdin:

```powershell
Get-Content tests\mcp_requests.jsonl | .venv\Scripts\python.exe -m semantic_memory.mcp
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
