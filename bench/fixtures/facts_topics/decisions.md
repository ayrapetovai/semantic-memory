# decisions

## Decision stream

- (2026-01-01) Hybrid ranking merges FTS and vector results with reciprocal rank fusion.
- (2026-01-02) The code root is the process cwd and each root gets its own index file.
- (2026-01-03) New memory bullets are dated and secrets are redacted before they are written.
- (2026-01-04) Archived bullets stay in memory/*.md but are not indexed or injected.
- (2026-01-05) A fact is skipped on write when its normalized text matches an existing bullet.
