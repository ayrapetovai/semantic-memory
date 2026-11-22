# plugin

## Поток решений

- OpenCode V2: ctx.event.subscribe({types}) фильтрует не полностью — приходят session.execution.started и step-события; pending-памятку снимать только на failed/interrupted, иначе context hook не получает facts
- Плагин semantic-memory: debug-лог (option debug или SEMANTIC_MEMORY_DEBUG=1), isEcho() отсекает echo собственного запроса, bulletText() срезает префикс «Поток решений :: », distill идёт через ctx.experimental.generate.text если задан model
- Автопамять проверена end-to-end: блок <memory> доходит до модели (ответ «Лимит токенов моста: ZZ-4211» без инструментов); коммит 8bc3601 — bridge.py (NDJSON), плагин, WAL+busy_timeout, MAX_TOPIC_BULLETS
- Доставка плагина: он закоммичен в репозиторий и автоподхватывается только когда OpenCode открыт в самом semantic-memory; для чужого проекта нужен путь к каталогу плагина в plugins[] opencode.jsonc (абсолютный/относительный от конфига), копирование файла не требуется
- Плагин вне репозитория обязан указать интерпретатор: options.python или SEMANTIC_MEMORY_PYTHON指向 venv репозитория, иначе bridge падает на ModuleNotFoundError и хуки деградируют (инъекции и захвата нет)
- Файлы плагина .opencode/plugins/semantic-memory/index.ts и package.json существуют только в клоне репозитория: init.py их не копирует, OpenCode ничего не создаёт — в чужом проекте автопамять не работает
- План доставки плагина в другой проект: init.py --plugin копирует PLUGIN_SRC→code_root()/.opencode/plugins/semantic-memory (идемпотентно по sha256, чужие правки не перезаписывать без --force) и проверяет мост ping'ом; --plugin --print печатает сниппет opencode.jsonc с абсолютным путём и option python
