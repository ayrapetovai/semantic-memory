import { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import path from "node:path"

/**
 * Automatic memory: the dialogue is written into memory/*.md through the Python
 * bridge (python -m semantic_memory.bridge), relevant facts are added to the
 * system prompt of every model turn. The semantic-memory MCP tools stay for
 * explicit requests (facts_search, facts_add, index_*).
 */

interface MemoryFact {
  topic: string
  text: string
  score?: number
}

interface Options {
  /** Python with semantic-memory installed (defaults to .venv/bin/python). */
  python?: string
  /** Model for distillation: {"providerID": "...", "id": "..."}; without it the session model is used. */
  model?: { providerID: string; id: string }
  /** Write facts from a model turn (LLM extraction). */
  distill?: boolean
  /** Feed recalled facts into system before each model turn. */
  inject?: boolean
  /** Write the user's turns into the chat topic. */
  captureChat?: boolean
  /** Log recall/inject/distill to server stderr (SEMANTIC_MEMORY_DEBUG=1). */
  debug?: boolean
  recallK?: number
  chatTopic?: string
  stateTopic?: string
  reindexTools?: string[]
}

const TOPIC_RE = /^[A-Za-z0-9_-]+$/
const WRITE_TOOLS = ["edit", "write", "apply_patch"]
const DISTILL_PROMPT =
  "From the dialogue fragment extract 0-3 facts for the long-term memory of the " +
  "project: decisions, agreements, parameters, constraints. Answer with JSON only, " +
  'no explanation: {"facts":[{"text":"...","topic":"..."}]}. ' +
  'topic — a short file name ([A-Za-z0-9_-]+). If there are no facts — {"facts":[]}.\n\n'

/** Python with semantic-memory installed: option → SEMANTIC_MEMORY_PYTHON → .venv. */
function pickPython(dir: string, opt?: string): string {
  if (opt) return opt
  if (process.env.SEMANTIC_MEMORY_PYTHON) return process.env.SEMANTIC_MEMORY_PYTHON
  const unix = path.join(dir, ".venv", "bin", "python")
  if (existsSync(unix)) return unix
  const win = path.join(dir, ".venv", "Scripts", "python.exe")
  if (existsSync(win)) return win
  return "python3"
}

/** NDJSON client for `python -m semantic_memory.bridge`: one request line → one answer line. */
function startBridge(dir: string, python: string) {
  const proc = spawn(python, ["-m", "semantic_memory.bridge"], { cwd: dir, env: process.env })
  let buffer = ""
  let seq = 0
  let dead = false
  const waiters = new Map<number, (result: any) => void>()

  proc.stdout?.on("data", (chunk) => {
    buffer += chunk.toString("utf8")
    let nl: number
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const raw = buffer.slice(0, nl)
      buffer = buffer.slice(nl + 1)
      if (!raw.trim()) continue
      let msg: any
      try {
        msg = JSON.parse(raw)
      } catch {
        continue
      }
      const resolve = waiters.get(msg.id)
      if (resolve) {
        waiters.delete(msg.id)
        resolve(msg)
      }
    }
  })
  proc.stderr?.on("data", (chunk) => console.error("[semantic-memory]", chunk.toString("utf8").trimEnd()))
  proc.on("exit", () => {
    dead = true
    for (const resolve of waiters.values()) resolve({ ok: false, error: "bridge stopped" })
    waiters.clear()
  })

  function request(cmd: string, args: Record<string, unknown> = {}): Promise<any> {
    if (dead || !proc.stdin?.writable) return Promise.resolve({ ok: false, error: "bridge stopped" })
    const id = ++seq
    return new Promise((resolve) => {
      waiters.set(id, resolve)
      proc.stdin.write(JSON.stringify({ id, cmd, ...args }) + "\n")
      setTimeout(() => {
        if (waiters.has(id)) {
          waiters.delete(id)
          resolve({ ok: false, error: "bridge timeout" })
        }
      }, 30_000)
    })
  }

  return { request, stop: () => proc.kill() }
}

function norm(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim()
}

/** "topic :: bullet" from recalled → only the bullet text. */
function bulletText(text: string): string {
  const i = text.indexOf(" :: ")
  return i >= 0 ? text.slice(i + 4) : text
}

/** Echo of the current request: recall returns the bullet written from this same text. */
function isEcho(query: string, fact: string): boolean {
  const q = norm(query)
  const f = norm(bulletText(fact))
  return !!q && (f === q || f.includes(q))
}

function formatMemory(facts: MemoryFact[]): string {
  const lines = facts.map((f) => `- [${f.topic}] ${bulletText(f.text)}`).join("\n")
  return (
    "<memory>\n" +
    "Relevant facts from the project memory (memory/*.md). Rely on them; write new " +
    "decisions with the facts_add MCP tool or leave them to the plugin.\n" +
    lines +
    "\n</memory>"
  )
}

/** V2 messages: {type:"user", text} and {type:"assistant", content:[{type:"text"}]}. */
function messageText(msg: any): string {
  if (typeof msg?.text === "string") return msg.text
  const items: any[] = Array.isArray(msg?.content) ? msg.content : Array.isArray(msg?.parts) ? msg.parts : []
  return items
    .filter((item) => item?.type === "text")
    .map((item) => String(item.text || ""))
    .join("\n")
}

/** Tail of the user/assistant dialogue used for fact extraction. */
function transcript(messages: any[]): string {
  const lines: string[] = []
  for (const msg of messages.slice(-6)) {
    const role = msg?.type ?? msg?.role
    if (role !== "user" && role !== "assistant") continue
    const text = messageText(msg)
    if (text.trim()) lines.push(`${role}: ${text.slice(0, 1200)}`)
  }
  return lines.join("\n")
}

/** Model answer {"facts":[{text,topic}]} → validated facts. */
function parseFacts(raw: string, fallbackTopic: string): MemoryFact[] {
  const match = raw.match(/\{[\s\S]*\}/)
  if (!match) return []
  let obj: any
  try {
    obj = JSON.parse(match[0])
  } catch {
    return []
  }
  if (!Array.isArray(obj.facts)) return []
  return obj.facts
    .filter((fact: any) => typeof fact?.text === "string" && fact.text.trim())
    .slice(0, 3)
    .map((fact: any) => ({
      text: String(fact.text).trim().slice(0, 400),
      topic: typeof fact.topic === "string" && TOPIC_RE.test(fact.topic) ? fact.topic : fallbackTopic,
    }))
}

export default Plugin.define({
  id: "semantic-memory",
  async setup(ctx) {
    const options = (ctx.options || {}) as Options
    const distill = options.distill !== false
    const inject = options.inject !== false
    const captureChat = options.captureChat !== false
    const recallK = Math.max(1, Math.min(Number(options.recallK) || 5, 10))
    const chatTopic = options.chatTopic && TOPIC_RE.test(options.chatTopic) ? options.chatTopic : "chat"
    const stateTopic = options.stateTopic && TOPIC_RE.test(options.stateTopic) ? options.stateTopic : "state"
    const writeTools = options.reindexTools?.length ? options.reindexTools : WRITE_TOOLS

    const dir = ctx.location?.directory || process.cwd()
    const bridge = startBridge(dir, pickPython(dir, options.python))
    const debug = options.debug === true || process.env.SEMANTIC_MEMORY_DEBUG === "1"
    const log = (...args: unknown[]) => {
      if (debug) console.error("[semantic-memory]", ...args)
    }
    const pending = new Map<string, MemoryFact[]>()
    const admitted = new Set<string>()
    const controller = new AbortController()

    /** LLM extraction of facts from a dialogue fragment → memory/*.md through the bridge. */
    async function distillInto(sessionID: string, text: string, fallbackTopic: string) {
      if (!distill || !text.trim()) return
      const prompt = DISTILL_PROMPT + text.slice(0, 6000)
      const experimental = (ctx as any).experimental?.generate
      const generated = options.model && experimental
        ? await experimental.text({ model: options.model, prompt })
        : await ctx.session.generate({ sessionID, prompt })
      const facts = parseFacts(String((generated as any)?.text || ""), fallbackTopic)
      log("distill →", facts.map((fact) => `${fact.topic}: ${fact.text}`))
      for (const fact of facts) {
        await bridge.request("add", { text: fact.text, topic: fact.topic })
      }
    }

    // User turns: recalled facts for the turn + a bullet in the chat topic.
    await ctx.session.hook("prompt", async (event: any) => {
      try {
        const sessionID = String(event.sessionID)
        const text = String(event.prompt?.text || "").trim()
        if (!text) return
        log("prompt turn:", sessionID)
        if (inject) {
          const recall = await bridge.request("recall", { query: text.slice(0, 500), k: recallK })
          const facts = (recall.facts || []).filter((fact: MemoryFact) => fact.text && !isEcho(text, fact.text))
          log("recall:", facts.length, facts.map((fact) => fact.topic))
          if (facts.length) pending.set(sessionID, facts)
        }
        if (!captureChat) return
        const key = `${sessionID}\u0000${norm(text)}`
        if (admitted.has(key)) return // the prompt hook is not an exactly-once boundary
        admitted.add(key)
        const bullet = text.slice(0, 400)
        const dup = await bridge.request("recall", { query: bullet.slice(0, 200), k: 1, topic: chatTopic })
        if (!(dup.facts || []).some((fact: MemoryFact) => norm(bulletText(fact.text)) === norm(bullet))) {
          log("chat bullet →", chatTopic)
          await bridge.request("add", { text: bullet, topic: chatTopic })
        }
      } catch (err) {
        console.error("[semantic-memory] prompt hook:", err)
      }
    })

    // Memory note in system for every turn of the agent loop (including continuation).
    await ctx.session.hook("context", async (event: any) => {
      log("context turn:", String(event.sessionID), "pending:", pending.size)
      const facts = pending.get(String(event.sessionID))
      if (!facts?.length) return
      event.system.push({ type: "text", text: formatMemory(facts) })
      log("inject → system:", facts.length)
    })

    // Compaction: record the state before the transcript gets folded away.
    await ctx.session.hook("compaction", async (event: any) => {
      void distillInto(String(event.sessionID), transcript(event.messages || []), stateTopic).catch((err) =>
        console.error("[semantic-memory] compaction distill:", err),
      )
    })

    // End of a turn: extract facts from the last exchange and drop the memory note.
    void (async () => {
      for await (const event of ctx.event.subscribe({
        types: ["session.execution.succeeded", "session.execution.failed", "session.execution.interrupted"],
        signal: controller.signal,
      }) as AsyncIterable<any>) {
        const sessionID = String(event.data?.sessionID ?? event.location?.sessionID ?? "")
        // started and step events arrive too: drop the note only on a failure.
        if (event.type === "session.execution.failed" || event.type === "session.execution.interrupted") {
          if (sessionID) pending.delete(sessionID)
          continue
        }
        if (event.type !== "session.execution.succeeded") continue
        if (!sessionID) continue
        try {
          const messages = await ctx.session.context({ sessionID })
          await distillInto(sessionID, transcript(messages), chatTopic)
        } catch (err) {
          console.error("[semantic-memory] execution distill:", err)
        } finally {
          pending.delete(sessionID)
        }
      }
    })().catch((err) => console.error("[semantic-memory] event loop:", err))

    // Reindex the code right after file edits.
    await ctx.tool.hook("execute.after", async (event: any) => {
      const name = String(event.tool || event.name || "")
      if (!writeTools.includes(name)) return
      if (event.status !== "completed") return
      await bridge.request("reindex", {}).catch(() => {})
    })

    return () => {
      controller.abort()
      bridge.stop()
    }
  },
})
