import test from "node:test"
import assert from "node:assert/strict"
import {
  bulletText,
  formatMemory,
  isEcho,
  norm,
  parseFacts,
  redact,
  transcript,
} from "../../.opencode/plugins/semantic-memory/lib.mjs"

test("norm collapses whitespace and case", () => {
  assert.equal(norm("  Hello   WORLD\n"), "hello world")
})

test("bulletText strips the topic prefix", () => {
  assert.equal(bulletText("decisions :: The limit is 10."), "The limit is 10.")
  assert.equal(bulletText("no prefix"), "no prefix")
})

test("isEcho filters the request's own text", () => {
  assert.equal(isEcho("Hybrid ranking", "decisions :: Hybrid ranking merges FTS and vector results."), true)
  assert.equal(isEcho("Cooling fan", "decisions :: The code root is cwd."), false)
})

test("parseFacts validates topics and caps the count", () => {
  const raw = '{"facts":[{"text":"Use dates","topic":"flow"},' +
    '{"text":"Bad topic","topic":"../etc"},{"text":"  "},{"text":"No topic"}]}'
  assert.deepEqual(parseFacts(raw, "chat"), [
    { text: "Use dates", topic: "flow" },
    { text: "Bad topic", topic: "chat" },
    { text: "No topic", topic: "chat" },
  ])
  assert.deepEqual(parseFacts("not json", "chat"), [])
})

test("formatMemory includes topic, date and clean bullet", () => {
  const text = formatMemory([{ topic: "flow", text: "flow :: Use dates", added: "2026-11-23" }])
  assert.match(text, /<memory>/)
  assert.match(text, /\[flow · 2026-11-23\] Use dates/)
})

test("redact masks high-confidence secrets only", () => {
  const { text, count } = redact("api_key=SUPERSECRET123 and token ghp_abcdefghijklmnopqrstuvwxyz012345")
  assert.equal(count, 2)
  assert.match(text, /api_key=\*\*\*/)
  assert.match(text, /ghp_\*\*\*/)
  assert.doesNotMatch(text, /SUPERSECRET123/)
  assert.equal(redact("ordinary decision").count, 0)
})

test("transcript keeps the last user/assistant texts", () => {
  const text = transcript([
    { type: "user", text: "old" },
    { type: "assistant", content: [{ type: "text", text: "answer" }] },
  ])
  assert.match(text, /user: old/)
  assert.match(text, /assistant: answer/)
})
