# ui-test

## Decision stream

- Tests rewrite config.toml to cover both short_greeting values and restore it in tearDown; plain `python3 -m unittest` only collects test*.py, so test_ui.py re-exports the ui_test classes.
