// Pure helpers shared by the plugin and tests/plugin/lib.test.mjs.
// Kept as .mjs so Node's built-in test runner can import them without a TS loader.

export const TOPIC_RE = /^[A-Za-z0-9_-]+$/
export const WRITE_TOOLS = ["edit", "write", "apply_patch"]
export const DISTILL_PROMPT =
  "From the dialogue fragment extract 0-3 facts for the long-term memory of the " +
  "project: decisions, agreements, parameters, constraints. Answer with JSON only, " +
  'no explanation: {"facts":[{"text":"...","topic":"..."}]}. ' +
  'topic — a short file name ([A-Za-z0-9_-]+). If there are no facts — {"facts":[]}.\n\n'

export function norm(text) {
  return String(text ?? "").toLowerCase().replace(/\s+/g, " ").trim()
}

/** "topic :: bullet" from recalled → only the bullet text. */
export function bulletText(text) {
  const i = String(text ?? "").indexOf(" :: ")
  return i >= 0 ? String(text).slice(i + 4) : String(text ?? "")
}

/** Echo of the current request: recall returns the bullet written from this same text. */
export function isEcho(query, fact) {
  const q = norm(query)
  const f = norm(bulletText(fact))
  return !!q && (f === q || f.includes(q))
}

export function formatMemory(facts) {
  const lines = (facts || [])
    .map((f) => {
      const date = f.added ? ` · ${f.added}` : ""
      return `- [${f.topic}${date}] ${bulletText(f.text)}`
    })
    .join("\n")
  return (
    "<memory>\n" +
    "Relevant facts from the project memory (memory/*.md). Rely on them; write new " +
    "decisions with the facts_add MCP tool or leave them to the plugin.\n" +
    lines +
    "\n</memory>"
  )
}

/** V2 messages: {type:"user", text} and {type:"assistant", content:[{type:"text"}]}. */
export function messageText(msg) {
  if (typeof msg?.text === "string") return msg.text
  const items = Array.isArray(msg?.content) ? msg.content : Array.isArray(msg?.parts) ? msg.parts : []
  return items
    .filter((item) => item?.type === "text")
    .map((item) => String(item.text || ""))
    .join("\n")
}

/** Tail of the user/assistant dialogue used for fact extraction. */
export function transcript(messages) {
  const lines = []
  for (const msg of (messages || []).slice(-6)) {
    const role = msg?.type ?? msg?.role
    if (role !== "user" && role !== "assistant") continue
    const text = messageText(msg)
    if (text.trim()) lines.push(`${role}: ${text.slice(0, 1200)}`)
  }
  return lines.join("\n")
}

/** Model answer {"facts":[{text,topic}]} → validated facts. */
export function parseFacts(raw, fallbackTopic) {
  const match = String(raw ?? "").match(/\{[\s\S]*\}/)
  if (!match) return []
  let obj
  try {
    obj = JSON.parse(match[0])
  } catch {
    return []
  }
  if (!Array.isArray(obj.facts)) return []
  return obj.facts
    .filter((fact) => typeof fact?.text === "string" && fact.text.trim())
    .slice(0, 3)
    .map((fact) => ({
      text: String(fact.text).trim().slice(0, 400),
      topic: typeof fact.topic === "string" && TOPIC_RE.test(fact.topic) ? fact.topic : fallbackTopic,
    }))
}

// High-confidence secrets only: a false positive costs a masked word, a false
// negative leaks a credential into a prompt or memory/*.md.
const SECRETS = [
  [/(api[_-]?key|access[_-]?token|secret|password|passwd)\s*[:=]\s*\S+/gi, "$1=***"],
  [/\bsk-[A-Za-z0-9_-]{8,}/g, "sk-***"],
  [/\bghp_[A-Za-z0-9]{20,}/g, "ghp_***"],
  [/\bAKIA[0-9A-Z]{16}\b/g, "AKIA***"],
  [/\bBearer\s+[A-Za-z0-9._-]{10,}/gi, "Bearer ***"],
  [/\b[A-Fa-f0-9]{40,}\b/g, "***"],
]

/** Mask secrets in text before it is sent to a model or written to memory. */
export function redact(text) {
  let out = String(text ?? "")
  let count = 0
  for (const [rx, repl] of SECRETS) {
    rx.lastIndex = 0
    const matches = out.match(rx)
    if (!matches) continue
    count += matches.length
    out = out.replace(rx, repl)
  }
  return { text: out, count }
}
