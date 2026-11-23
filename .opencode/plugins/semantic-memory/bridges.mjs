// Bridge registry for the plugin: one `python -m semantic_memory.bridge` per project
// directory, tested by tests/plugin/bridges.test.mjs. A shared OpenCode server runs
// sessions from many directories at once, so the memory directory is chosen per
// request rather than once when the plugin instance loads.

/** Answers of `python -m semantic_memory.bridge` are NDJSON lines matched by their id. */
function makeClient(proc, { timeoutMs, timers, onExit, onStderr }) {
  let buffer = ""
  let seq = 0
  let dead = false
  const inflight = new Map()

  proc.stdout?.on("data", (chunk) => {
    buffer += chunk.toString("utf8")
    let nl
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const raw = buffer.slice(0, nl)
      buffer = buffer.slice(nl + 1)
      if (!raw.trim()) continue
      let msg
      try {
        msg = JSON.parse(raw)
      } catch {
        continue
      }
      const entry = inflight.get(msg.id)
      if (entry) {
        inflight.delete(msg.id)
        entry.resolve(msg)
      }
    }
  })
  proc.stderr?.on("data", (chunk) => onStderr(chunk.toString("utf8").trimEnd()))
  proc.on("exit", () => {
    dead = true
    const pending = [...inflight.values()]
    inflight.clear()
    onExit(pending)
  })

  return {
    request(cmd, args = {}) {
      if (dead || !proc.stdin?.writable) return Promise.resolve({ ok: false, error: "bridge stopped" })
      const id = ++seq
      return new Promise((resolve) => {
        inflight.set(id, { cmd, args, resolve })
        proc.stdin.write(JSON.stringify({ id, cmd, ...args }) + "\n")
        timers.setTimeout(() => {
          if (inflight.has(id)) {
            inflight.delete(id)
            resolve({ ok: false, error: "bridge timeout" })
          }
        }, cmd === "reindex" ? Math.max(timeoutMs, 30_000) : timeoutMs)
      })
    },
    stop() {
      dead = true
      for (const entry of inflight.values()) entry.resolve({ ok: false, error: "bridge stopped" })
      inflight.clear()
      proc.kill()
    },
  }
}

/**
 * Bridges keyed by project directory. A dead interpreter disables memory for that
 * directory instead of hanging hooks, retrying with backoff (restartDelays) until it
 * gives up. Requests that were in flight or arrive while restarting are queued per
 * directory (bounded) and replayed once the new process is up. Idle bridges are
 * stopped so a long-lived server does not accumulate Python processes.
 */
export function createRegistry(spawnProc, options = {}) {
  // `??` and `||` cannot be mixed in one chain, so the fallback is parenthesised
  const timeoutMs = options.timeoutMs ?? (Number(process.env.SEMANTIC_MEMORY_TIMEOUT_MS) || 10_000)
  const idleMs = options.idleMs ?? (Number(process.env.SEMANTIC_MEMORY_IDLE_MS) || 600_000)
  const sweepMs = options.sweepMs ?? 60_000
  const maxBridges = options.maxBridges ?? 32
  const queueSize = options.queueSize ?? 64
  const restartDelays = options.restartDelays ?? [1000, 5000, 15000]
  const log = options.log ?? (() => {})
  const timers = options.timers ?? globalThis
  const clock = options.clock ?? Date.now

  const states = new Map()
  let stopping = false

  /** Spawn the interpreter for an existing state, so the restart count survives relaunches. */
  function attach(state) {
    const born = clock()
    state.client = makeClient(spawnProc(state.dir), {
      timeoutMs,
      timers,
      onExit: (pending) => {
        state.client = undefined
        if (stopping) {
          for (const entry of pending) entry.resolve({ ok: false, error: "memory stopped" })
          return
        }
        if (clock() - born > 60_000) state.restarts = 0
        if (state.restarts >= restartDelays.length) {
          state.givenUp = true
          log("bridge exited in", state.dir, "; memory disabled for that directory")
          for (const entry of pending) entry.resolve({ ok: false, error: "memory disabled" })
          return
        }
        const delay = restartDelays[state.restarts++]
        log("bridge exited in", state.dir, "; restart in", delay, "ms")
        for (const entry of pending) {
          if (state.queue.length >= queueSize) entry.resolve({ ok: false, error: "memory queue full" })
          else state.queue.push({ cmd: entry.cmd, args: entry.args, resolve: entry.resolve })
        }
        state.restartTimer = timers.setTimeout(() => {
          state.restartTimer = null
          if (stopping) return
          attach(state)
          const waiting = state.queue.splice(0)
          for (const item of waiting) void request(state.dir, item.cmd, item.args).then(item.resolve)
        }, delay)
      },
      // stderr of the interpreter is a real failure (bad python, missing package): keep
      // it on stderr always, and mirror it to the debug sink when debug is enabled
      onStderr: (text) => {
        console.error("[semantic-memory]", text)
        log("bridge stderr:", text)
      },
    })
    log("bridge spawned in", state.dir)
    if (states.size > maxBridges) evictUntil(maxBridges)
  }

  function launch(dir) {
    const state = {
      dir,
      client: undefined,
      restarts: 0,
      restartTimer: null,
      queue: [],
      lastUsed: clock(),
      givenUp: false,
    }
    states.set(dir, state)
    attach(state)
    return state
  }

  /** Replay everything that waited for the interpreter to come back. */
  function drain(state) {
    const waiting = state.queue.splice(0)
    for (const item of waiting) void request(state.dir, item.cmd, item.args).then(item.resolve)
  }

  /** Stop the least recently used bridges until at most `limit` remain. */
  function evictUntil(limit) {
    const idle = [...states.values()]
      .filter((state) => !state.restartTimer && !state.queue.length)
      .sort((a, b) => a.lastUsed - b.lastUsed)
    for (const state of idle.slice(0, Math.max(0, states.size - limit))) {
      states.delete(state.dir)
      state.client?.stop()
      log("idle bridge stopped for", state.dir)
    }
  }

  function evictIdle(now = clock()) {
    for (const state of states.values()) {
      if (state.restartTimer || state.queue.length) continue
      if (now - state.lastUsed < idleMs) continue
      states.delete(state.dir)
      state.client?.stop()
      log("idle bridge stopped for", state.dir)
    }
  }

  function request(dir, cmd, args = {}) {
    if (stopping) return Promise.resolve({ ok: false, error: "memory stopped" })
    let state = states.get(dir)
    if (!state) state = launch(dir)
    state.lastUsed = clock()
    if (state.givenUp) return Promise.resolve({ ok: false, error: "memory disabled" })
    if (state.restartTimer) {
      if (state.queue.length >= queueSize) return Promise.resolve({ ok: false, error: "memory queue full" })
      return new Promise((resolve) => state.queue.push({ cmd, args, resolve }))
    }
    if (!state.client) return Promise.resolve({ ok: false, error: "bridge stopped" })
    return state.client.request(cmd, args)
  }

  const sweep = timers.setInterval(() => evictIdle(), sweepMs)
  sweep?.unref?.()

  return {
    request,
    evictIdle,
    size: () => states.size,
    dirs: () => [...states.keys()],
    stopAll() {
      stopping = true
      timers.clearInterval(sweep)
      for (const state of states.values()) {
        if (state.restartTimer) timers.clearTimeout(state.restartTimer)
        state.client?.stop()
        for (const queued of state.queue) queued.resolve({ ok: false, error: "memory stopped" })
        state.queue = []
      }
      states.clear()
    },
  }
}
