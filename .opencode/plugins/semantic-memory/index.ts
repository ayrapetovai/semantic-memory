import { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"
import { appendFileSync, existsSync } from "node:fs"
import path from "node:path"
import { createRegistry } from "./bridges.mjs"
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
 *
 * The plugin instance is shared by the server, so the project directory is resolved
 * per event from the session's location. Each directory gets its own bridge: a
 * session must never read or write another project's memory.
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
  /** Write debug lines to this file too (the plugin's stderr is not visible to a shared server). */
  logFile?: string
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

/**
 * A session's project directory, or undefined when the server does not report one.
 * Never fall back to the plugin's own location or process.cwd(): in a shared server
 * that would read and write another project's memory. `hint` is a directory carried by
 * the event itself; it wins and replaces the cached one, so a moved session is tracked.
 */
async function directoryOf(
  ctx: any,
  sessionID: string,
  known: Map<string, string>,
  hint?: unknown,
): Promise<string | undefined> {
  if (typeof hint === "string" && hint) {
    if (sessionID) known.set(sessionID, hint)
    return hint
  }
  const cached = sessionID ? known.get(sessionID) : undefined
  if (cached) return cached
  if (!sessionID) return undefined
  try {
    const session = await ctx.session.get({ sessionID })
    const dir = session?.location?.directory
    if (typeof dir !== "string" || !dir) return undefined
    known.set(sessionID, dir)
    if (known.size > 512) {
      const oldest = known.keys().next().value
      if (oldest !== undefined) known.delete(oldest)
    }
    return dir
  } catch {
    return undefined
  }
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

    const debug = options.debug === true || process.env.SEMANTIC_MEMORY_DEBUG === "1"
    const log = (...args: unknown[]) => {
      if (!debug) return
      const line = `[semantic-memory] ${args.map((arg) => (typeof arg === "string" ? arg : JSON.stringify(arg))).join(" ")}`
      console.error(line)
      // a shared server swallows the plugin's stderr, so a file sink keeps it observable
      if (options.logFile) {
        try {
          appendFileSync(options.logFile, line + "\n")
        } catch {
          /* diagnostics must never break a hook */
        }
      }
    }
    const instanceDir = ctx.location?.directory

    // Bridges keyed by project directory: one server hosts sessions from many
    // locations, and a plugin instance outlives the location that loaded it, so the
    // directory is resolved per event, not here. Each directory gets its own
    // interpreter; idle ones are stopped so a long-lived server does not accumulate
    // Python processes. A dead interpreter disables memory for that directory instead
    // of hanging hooks; after a crash it restarts with backoff (max 3).
    const sessionDirs = new Map<string, string>()
    const registry = createRegistry((dir: string) => {
      // in another project the package is not next to cwd, so PYTHONPATH points at the repository
      const python = pickPython(dir, options.python)
      const root = packageRoot(python)
      const env = root ? { ...process.env, PYTHONPATH: root } : process.env
      log("bridge python:", python, "for:", dir, "options:", JSON.stringify(options))
      if (instanceDir && instanceDir !== dir) log("plugin instance dir differs:", instanceDir)
      return spawn(python, ["-m", "semantic_memory.bridge"], { cwd: dir, env })
    }, { log })

    /**
     * Bridge calls scoped to the session's own project, or undefined when the server
     * reports no directory for it — then memory is skipped rather than guessed.
     */
    async function memoryFor(event: any, sessionID: string) {
      const dir = await directoryOf(ctx, sessionID, sessionDirs, event?.location?.directory)
      if (!dir) {
        log("no directory for session:", sessionID || "?", "memory skipped")
        return undefined
      }
      return (cmd: string, args: Record<string, unknown> = {}): Promise<any> => registry.request(dir, cmd, args)
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

    /** LLM extraction of facts from a dialogue fragment → memory/*.md of that project. */
    async function distillInto(event: any, sessionID: string, text: string, fallbackTopic: string) {
      if (!distill || !text.trim()) return
      const call = await memoryFor(event, sessionID)
      if (!call) return
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
        const result = await call("add", { text: fact.text, topic: fact.topic })
        if (result?.added === false) log("distill → skipped duplicate", fact.topic, result.duplicate_of)
      }
    }

    // User turns: recalled facts for the turn + a bullet in the chat topic.
    await ctx.session.hook("prompt", async (event: any) => {
      try {
        const sessionID = String(event.sessionID)
        const text = String(event.prompt?.text || "").trim()
        if (!text) return
        const call = await memoryFor(event, sessionID)
        if (!call) return
        log("prompt turn:", sessionID)
        if (inject) {
          const recall = await call("recall", {
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
        const dup = await call("recall", {
          query: bullet.slice(0, 200), k: 1, topic: chatTopic, minSim,
        })
        if (!(dup.facts || []).some((fact: MemoryFact) => norm(bulletText(fact.text)) === norm(bullet))) {
          log("chat bullet →", chatTopic)
          await call("add", { text: bullet, topic: chatTopic })
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
      const sessionID = String(event.sessionID)
      void distillInto(event, sessionID, transcript(event.messages || []), stateTopic).catch((err) =>
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
          await distillInto(event, sessionID, transcript(messages), chatTopic)
        } catch (err) {
          console.error("[semantic-memory] execution distill:", err)
        } finally {
          pending.delete(sessionID)
        }
      }
    })().catch((err) => console.error("[semantic-memory] event loop:", err))

    // Reindex the code of the project the tool just edited.
    await ctx.tool.hook("execute.after", async (event: any) => {
      const name = String(event.tool || event.name || "")
      if (!writeTools.includes(name)) return
      if (event.status !== "completed") return
      const call = await memoryFor(event, String(event.sessionID || ""))
      if (!call) return
      await call("reindex", {}).catch(() => {})
    })

    return () => {
      controller.abort()
      registry.stopAll()
    }
  },
})
