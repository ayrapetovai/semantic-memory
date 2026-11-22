# workflow

## Поток решений

- Workflow semantic-memory: одна рабочая сессия агента в одном репозитории — явный слой (MCP-инструменты: code_search/symbol/callers/callees/references/file_outline/file_read/project_map/facts_search/facts_add) + автоматический слой плагина (recall→<memory> в system, захват реплик в chat, distill на конец хода, reindex после правок). Источник истины — memory/*.md в корне проекта
- Ограничение workflow: коммит и любые правки файлов выполняются только после переключения на build-агент; в Plan mode git-записи не делается
