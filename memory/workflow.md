# workflow

## Decision stream

- semantic-memory workflow: one working agent session in one repository — an explicit layer (MCP tools: code_search/symbol/callers/callees/references/file_outline/file_read/project_map/facts_search/facts_add) + the automatic plugin layer (recall→`<memory>` in system, capture of turns into chat, distill at the end of a turn, reindex after edits). The source of truth is memory/*.md in the project root
- Workflow limitation: a commit and any file edits are made only after switching to the build agent; git writes are not done in Plan mode
