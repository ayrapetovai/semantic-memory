import { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import path from "node:path"
import {
  DISTILL_PROMPT,
  TOPIC_RE,
  WRITE_TOOLS,
  bulletText,
  formatMemory,
  isEcho,
  norm,
  parseFacts,
  redact,
  transcript,
} from "./lib.mjs"

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
  added?: string
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
  /** Topics never injected (default: chat, state). */
  excludeTopics?: string[]
  /** Minimum vector similarity for vector-only recall hits. */
  minSim?: number
  chatTopic?: string
  stateTopic?: string
  reindexTools?: string[]
}

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

/** Where the semantic_memory package lives for a venv python: <root>/.venv/bin/python → <root>. */
function packageRoot(python: string): string | undefined {
  if (!path.isAbsolute(python)) return undefined
  const guess = path.resolve(python, "..", "..", "..")
  return existsSync(path.join(guess, "semantic_memory")) ? guess : undefined
}

/** NDJSON client for `python -m semantic_memory.bridge`: one request line → one answer line. */
function startBridge(dir: string, python: string, onExit?: () => void) {
  // in another project the package is not next to cwd, so PYTHONPATH points at the repository
  const root = packageRoot(python)
  const timeoutMs = Number(process.env.SEMANTIC_MEMORY_TIMEOUT_MS) || 10_000
  const env = root ? { ...process.env, PYTHONPATH: root } : process.env
  const proc = spawn(python, ["-m", "semantic_memory.bridge"], { cwd: dir, env })
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
    onExit?.()
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
      }, cmd === "reindex" ? Math.max(timeoutMs, 30_000) : timeoutMs)
    })
  }

  return { request, stop: () => proc.kill() }
}

export default Plugin.define({
  id: "semantic-memory",
  async setup(ctx) {
    const options = (ctx.options || {}) as Options
    // Opt-in: raw prompt capture and the per-turn distill call are noise/cost by
    // default. Injection stays on, but recall is filtered (minSim/excludeTopics).
    const distill = options.distill === true
    const inject = options.inject !== false
    const captureChat = options.captureChat === true
    const recallK = Math.max(1, Math.min(Number(options.recallK) || 5, 10))
    const minSim = Number.isFinite(Number(options.minSim)) ? Number(options.minSim) : 0.45
    const excludeTopics = (options.excludeTopics?.length ? options.excludeTopics : ["chat", "state"])
      .filter((topic) => TOPIC_RE.test(topic))
    const chatTopic = options.chatTopic && TOPIC_RE.test(options.chatTopic) ? options.chatTopic : "chat"
    const stateTopic = options.stateTopic && TOPIC_RE.test(options.stateTopic) ? options.stateTopic : "state"
    const writeTools = options.reindexTools?.length ? options.reindexTools : WRITE_TOOLS

    const dir = ctx.location?.directory || process.cwd()
    const debug = options.debug === true || process.env.SEMANTIC_MEMORY_DEBUG === "1"
    const log = (...args: unknown[]) => {
      if (debug) console.error("[semantic-memory]", ...args)
    }

    // One restart-friendly bridge: a dead interpreter disables memory instead of
    // hanging hooks; after a crash it retries with backoff (max 3). Requests made
    // while restarting are queued (bounded) and replayed when the new process is up.
    let bridge: ReturnType<typeof startBridge>
    let stopping = false
    let restarts = 0
    let restartTimer: ReturnType<typeof setTimeout> | null = null
    const queue: Array<{
      cmd: string
      args: Record<string, unknown>
      resolve: (result: any) => void
    }> = []
    function launch() {
      const born = Date.now()
      bridge = startBridge(dir, pickPython(dir, options.python), () => {
        if (stopping) return
        if (Date.now() - born > 60_000) restarts = 0
        if (restarts >= 3) {
          console.error("[semantic-memory] bridge stopped; memory disabled for this session")
          return
        }
        const delay = [1000, 5000, 15000][restarts++]
        log("bridge exited; restart in", delay, "ms")
        restartTimer = setTimeout(() => {
          restartTimer = null
          if (stopping) return
          launch()
          const waiting = queue.splice(0)
          for (const item of waiting) void bridge.request(item.cmd, item.args).then(item.resolve)
        }, delay)
      })
    }
    launch()
    const request = (cmd: string, args: Record<string, unknown> = {}): Promise<any> => {
      if (stopping) return Promise.resolve({ ok: false, error: "memory stopped" })
      if (restartTimer) {
        if (queue.length >= 64) return Promise.resolve({ ok: false, error: "memory queue full" })
        return new Promise((resolve) => queue.push({ cmd, args, resolve }))
      }
      return bridge.request(cmd, args).then((result) => {
        if (result?.error === "bridge stopped" && restartTimer && restarts < 3) {
          if (queue.length >= 64) return { ok: false, error: "memory queue full" }
          return new Promise((resolve) => queue.push({ cmd, args, resolve }))
        }
        return result
      })
    }

    const pending = new Map<string, MemoryFact[]>()
    const admitted = new Map<string, true>()
    function admit(key: string): boolean {
      if (admitted.has(key)) return false
      admitted.set(key, true)
      if (admitted.size > 512) {
        const oldest = admitted.keys().next().value
        if (oldest !== undefined) admitted.delete(oldest)
      }
      return true
    }
    const controller = new AbortController()

    /** LLM extraction of facts from a dialogue fragment → memory/*.md through the bridge. */
    async function distillInto(sessionID: string, text: string, fallbackTopic: string) {
      if (!distill || !text.trim()) return
      const safe = redact(text)
      if (safe.count) log("redacted:", safe.count)
      const prompt = DISTILL_PROMPT + safe.text.slice(0, 6000)
      const experimental = (ctx as any).experimental?.generate
      const generated = options.model && experimental
        ? await experimental.text({ model: options.model, prompt })
        : await ctx.session.generate({ sessionID, prompt })
      const facts = parseFacts(String((generated as any)?.text || ""), fallbackTopic)
      log("distill →", facts.map((fact) => `${fact.topic}: ${fact.text}`))
      for (const fact of facts) {
        const result = await request("add", { text: fact.text, topic: fact.topic })
        if (result?.added === false) log("distill → skipped duplicate", fact.topic, result.duplicate_of)
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
          const recall = await request("recall", {
            query: text.slice(0, 500), k: recallK, excludeTopics, minSim,
          })
          const facts = (recall.facts || []).filter((fact: MemoryFact) => fact.text && !isEcho(text, fact.text))
          log("recall:", facts.length, facts.map((fact) => fact.topic))
          if (facts.length) pending.set(sessionID, facts)
        }
        if (!captureChat) return
        const key = `${sessionID}\u0000${norm(text)}`
        if (!admit(key)) return // the prompt hook is not an exactly-once boundary
        const safeBullet = redact(text.slice(0, 400))
        if (safeBullet.count) log("redacted:", safeBullet.count)
        const bullet = safeBullet.text
        const dup = await request("recall", {
          query: bullet.slice(0, 200), k: 1, topic: chatTopic, minSim,
        })
        if (!(dup.facts || []).some((fact: MemoryFact) => norm(bulletText(fact.text)) === norm(bullet))) {
          log("chat bullet →", chatTopic)
          await request("add", { text: bullet, topic: chatTopic })
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
      await request("reindex", {}).catch(() => {})
    })

    return () => {
      stopping = true
      controller.abort()
      bridge.stop()
    }
  },
})
