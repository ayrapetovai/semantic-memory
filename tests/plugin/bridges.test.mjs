import test from "node:test"
import assert from "node:assert/strict"
import { createRegistry } from "../../.opencode/plugins/semantic-memory/bridges.mjs"

/**
 * Clock and timers that move together, so restart backoff and idle eviction are
 * deterministic. `advance` runs the due timers in order, so an in-flight request can be
 * left unanswered by holding the first process (`holdFirst`). Run with `node --test`.
 */
function fakeClock(start = 1000) {
  let now = start
  let seq = 0
  const jobs = new Map()
  const timers = {
    setTimeout: (fn, ms) => {
      const id = ++seq
      jobs.set(id, { fn, at: now + ms })
      return id
    },
    clearTimeout: (id) => jobs.delete(id),
    setInterval: (fn, ms) => {
      const id = ++seq
      jobs.set(id, { fn, at: now + ms, every: ms })
      return id
    },
    clearInterval: (id) => jobs.delete(id),
    advance: (delta) => {
      const target = now + delta
      for (;;) {
        const due = [...jobs.entries()]
          .filter(([, job]) => job.at <= target)
          .sort((a, b) => a[1].at - b[1].at)
        if (!due.length) break
        for (const [id, job] of due) {
          now = job.at
          if (job.every) jobs.set(id, { fn: job.fn, at: job.at + job.every })
          else jobs.delete(id)
          job.fn()
        }
      }
      now = target
    },
  }
  return { clock: () => now, timers, advance: timers.advance }
}

/** Stand-in for `python -m semantic_memory.bridge`: answers every line it is given. */
function fakeProc(dir, spawned) {
  const handlers = { stdout: [], stderr: [], exit: [] }
  const proc = {
    dir,
    killed: false,
    requests: [],
    // hold = true keeps the answers back, so a request can stay in flight for a crash test
    hold: false,
    stdin: {
      writable: true,
      write: (line) => {
        const msg = JSON.parse(line)
        proc.requests.push(msg)
        if (proc.hold) return
        for (const fn of handlers.stdout) fn(JSON.stringify({ id: msg.id, ok: true, added: msg.text }) + "\n")
      },
    },
    stdout: { on: (_event, fn) => handlers.stdout.push(fn) },
    stderr: { on: (_event, fn) => handlers.stderr.push(fn) },
    on: (_event, fn) => handlers.exit.push(fn),
    kill: () => {
      proc.killed = true
    },
    crash: () => {
      for (const fn of handlers.exit) fn()
    },
  }
  spawned.push(proc)
  return proc
}

function newRegistry(options = {}) {
  const { clock, timers, advance } = fakeClock()
  const spawned = []
  const registry = createRegistry((dir) => {
    const proc = fakeProc(dir, spawned)
    // only the first process stays silent, so a request can be left in flight
    if (options.holdFirst) proc.hold = spawned.length === 1
    return proc
  }, {
    timers,
    clock,
    sweepMs: 10_000_000,
    restartDelays: [10, 20, 30],
    ...options,
  })
  return { registry, spawned, advance }
}

test("bridges are keyed by directory and requests reach their own process", async () => {
  const { registry, spawned } = newRegistry()
  await registry.request("/a", "recall", { query: "one" })
  await registry.request("/b", "add", { text: "two" })
  assert.deepEqual(spawned.map((proc) => proc.dir), ["/a", "/b"])
  assert.deepEqual(spawned[0].requests.map((req) => req.cmd), ["recall"])
  assert.deepEqual(spawned[1].requests.map((req) => req.cmd), ["add"])
})

test("a directory keeps its bridge between requests", async () => {
  const { registry, spawned } = newRegistry()
  assert.equal((await registry.request("/a", "recall", { query: "x" })).ok, true)
  await registry.request("/a", "add", { text: "kept" })
  assert.equal(spawned.length, 1)
  assert.deepEqual(spawned[0].requests.map((req) => req.cmd), ["recall", "add"])
})

test("a crash restarts the bridge and replays the request that was in flight", async () => {
  const { registry, spawned, advance } = newRegistry({ holdFirst: true })
  const inFlight = registry.request("/a", "recall", { query: "x" })
  spawned[0].crash() // the request is still in flight when the process dies
  advance(10)
  assert.equal(spawned.length, 2)
  assert.equal((await inFlight).ok, true)
  assert.deepEqual(spawned[1].requests.map((req) => req.cmd), ["recall"])
})

test("requests made while the bridge restarts are queued and replayed", async () => {
  const { registry, spawned, advance } = newRegistry()
  await registry.request("/a", "recall", { query: "x" })
  spawned[0].crash()
  const queued = registry.request("/a", "add", { text: "t" })
  assert.equal(spawned.length, 1) // no new process until the backoff elapses
  advance(10)
  assert.equal((await queued).ok, true)
  assert.deepEqual(spawned[1].requests.map((req) => req.cmd), ["add"])
})

test("a bridge that keeps dying disables memory for that directory", async () => {
  const { registry, spawned, advance } = newRegistry()
  await registry.request("/a", "recall", { query: "x" })
  spawned[0].crash()
  advance(10)
  spawned[1].crash()
  advance(20)
  spawned[2].crash()
  advance(30)
  spawned[3].crash() // the backoff is spent
  assert.equal(spawned.length, 4)
  assert.deepEqual(await registry.request("/a", "recall", { query: "y" }), {
    ok: false,
    error: "memory disabled",
  })
  assert.equal(spawned.length, 4)
})

test("the restart count resets once a process has lived long", async () => {
  const { registry, spawned, advance } = newRegistry()
  await registry.request("/a", "recall", { query: "x" })
  spawned[0].crash()
  advance(10)
  advance(61_000) // the replacement ran long enough to reset the backoff
  spawned[1].crash()
  advance(10)
  assert.equal(spawned.length, 3)
  assert.equal((await registry.request("/a", "add", { text: "t" })).ok, true)
})

test("idle bridges are stopped and a later request respawns one", async () => {
  const { registry, spawned, advance } = newRegistry({ idleMs: 5_000 })
  await registry.request("/a", "recall", { query: "x" })
  registry.evictIdle(6_001) // past the idle window (lastUsed is the clock's 1000)
  assert.equal(registry.size(), 0)
  assert.equal(spawned[0].killed, true)
  assert.equal((await registry.request("/a", "recall", { query: "again" })).ok, true)
  assert.equal(spawned.length, 2)
})

test("the periodic sweep drops idle bridges", async () => {
  const { registry, spawned, advance } = newRegistry({ idleMs: 5_000, sweepMs: 1_000 })
  await registry.request("/a", "recall", { query: "x" })
  advance(6_000)
  assert.equal(registry.size(), 0)
  assert.equal(spawned[0].killed, true)
})

test("the registry caps how many bridges it keeps", async () => {
  const { registry, spawned, advance } = newRegistry({ maxBridges: 2 })
  await registry.request("/a", "recall", { query: "1" })
  advance(1)
  await registry.request("/b", "recall", { query: "2" })
  advance(1)
  await registry.request("/c", "recall", { query: "3" })
  assert.deepEqual(registry.dirs(), ["/b", "/c"])
  assert.equal(spawned[0].killed, true)
})

test("a bridge that never answers is timed out", async () => {
  const { registry, advance } = newRegistry({ holdFirst: true, timeoutMs: 5 })
  const pending = registry.request("/a", "recall", { query: "x" })
  advance(5)
  assert.deepEqual(await pending, { ok: false, error: "bridge timeout" })
})

test("teardown stops every bridge and releases what was waiting", async () => {
  const { registry, spawned } = newRegistry()
  await registry.request("/a", "recall", { query: "x" })
  spawned[0].crash() // a restart is pending, so the next request is queued
  const queued = registry.request("/a", "add", { text: "t" })
  await registry.request("/b", "recall", { query: "y" }) // a live bridge that must be killed
  registry.stopAll()
  assert.deepEqual(await queued, { ok: false, error: "memory stopped" })
  assert.equal(registry.size(), 0)
  assert.equal(spawned[1].killed, true) // /a already exited on its own, /b is killed
  assert.deepEqual(await registry.request("/a", "recall", { query: "z" }), {
    ok: false,
    error: "memory stopped",
  })
})
