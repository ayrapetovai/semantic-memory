# measurement

## Decision stream

- Effectiveness measurement plan agreed: 1) offline recall precision@k pytest over memory fixtures (precision@5 >= 0.8), echo/dedup and embeddings-vs-FTS checks; 2) A/B session scenarios run 3x with plugin ON vs OFF (disable via plugins: ["-semantic-memory"]) scored by deterministic checks; 3) NDJSON metric events from plugin debug logs for tokens/cost; 4) injection hit-rate judged with ctx.experime
