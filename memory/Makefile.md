# Makefile

## Decision stream

- Recipe lines in a Makefile must start with a tab, not spaces: the applied patch added 7 spaces, which made `make` fail with 'missing separator'; the main.o recipe (line 14) and the `clean:` rule were converted to tabs.
- Makefile recipes require tabs: recipe lines with 7 spaces cause "missing separator". After applying the_patch.patch the main.o recipe line (line 14) and the `clean` rule were fixed from spaces to tabs, the magpie build is restored.
