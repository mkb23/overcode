// Minimal fake of opencode2's TuiPluginApi for the v2 telemetry plugin.
//
// Verified live against opencode2 v0.0.0-dev-19272: the TUI invokes the
// plugin's `setup(api)` and the bus is reachable only through
// `api.client.event.subscribe({directory})`, which resolves to an async
// generator of `{id, created, type, location, data}` envelopes (the server's
// SSE /event stream). `api.event` does NOT exist in this build.
//
// Usage: node opencode2_plugin_harness.mjs <plugin.js> <events.json> [--no-overcode-env] [--flaky]
// Prints JSON: {registered, state, events, subscribe_times, sleep_delays}
// from the hook files the plugin wrote.
//
// `--no-overcode-env` unsets the OVERCODE_* identity vars before importing
// the plugin, so the harness can prove the plugin registers nothing when
// overcode did not launch the TUI (the env vars may leak in from the parent
// process, so they are deleted explicitly rather than left alone).
//
// `--flaky` drives the plugin's reconnect loop: the events file holds an
// array of stream specs — "throw" (a connection whose async generator
// rejects on the first iteration, how a dead SSE stream surfaces inside
// `for await`) or an array of events (a healthy replay). Subscribes past
// the spec list get an empty stream. The output adds `subscribe_times`
// (ms since setup) so tests can pin the backoff policy: the delay must
// double per failed connection and reset only once a connection has
// actually delivered an event.
//
// `--dispose-while-subscribing` races teardown with the FIRST
// subscription: the initial `subscribe()` stays pending, the harness
// invokes the plugin's disposer, and only THEN resolves the subscription
// with a parked connection (a stream whose `next()` never resolves — a
// live SSE subscription with no traffic yet). The output adds
// `late_stream_returned` (whether the plugin called `.return()` on the
// late-resolved stream) so tests can prove the pump neither parks in
// `for await` forever nor leaves the stream un-closed.
import fs from "node:fs"
import path from "node:path"
import os from "node:os"

const [, , pluginPath, eventsPath] = process.argv
const flaky = process.argv.includes("--flaky")
const disposeWhileSubscribing = process.argv.includes("--dispose-while-subscribing")
const events = JSON.parse(fs.readFileSync(eventsPath, "utf8"))

const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "oc2harness-"))
if (process.argv.includes("--no-overcode-env")) {
  delete process.env.OVERCODE_STATE_DIR
  delete process.env.OVERCODE_TMUX_SESSION
  delete process.env.OVERCODE_SESSION_NAME
} else {
  process.env.OVERCODE_STATE_DIR = stateDir
  process.env.OVERCODE_TMUX_SESSION = "harness"
  process.env.OVERCODE_SESSION_NAME = "harness-agent"
}

let subscribed = false
let callIndex = 0
const subscribeTimes = []
const t0 = Date.now()
let drainDone
const drained = new Promise((resolve) => { drainDone = resolve })
let resolveFirstSubscribe = null
let lateStreamReturned = false

// The stream the pending first subscription eventually resolves to. It
// models a live SSE connection with NO traffic yet: `next()` never
// resolves. The pump must close it via `.return()` without ever
// awaiting an event; the wrapper records whether `.return()` was
// called (the dispose-while-subscribing teardown contract).
function lateStreamProxy() {
  return {
    next: () => new Promise(() => {}),
    return: () => {
      lateStreamReturned = true
      return Promise.resolve({ done: true })
    },
    [Symbol.asyncIterator]() { return this },
  }
}

// Installed BEFORE importing the plugin: record every setTimeout delay
// issued in this process — the plugin's backoff sleeps AND the harness's
// own drain/settle timers — so tests can pin the backoff policy by VALUE
// (plugin: 250/500/1000) instead of wall-clock subscribe() gaps, which
// only ever stretch under load. Ambient harness timers (2000/8000) are
// distinguishable by their values.
const sleepDelays = []
const realSetTimeout = globalThis.setTimeout
globalThis.setTimeout = (fn, delay, ...args) => {
  const d = Number(delay)
  sleepDelays.push(d > 0 ? d : 0)
  return realSetTimeout(fn, delay, ...args)
}

// One connection = one spec: "throw" rejects inside `for await` (the lazy
// generator only fails on iteration); an array replays those events.
// An empty/past-spec stream connects cleanly but delivers nothing.
function streamFor(spec) {
  if (spec === "throw") {
    return (async function* () {
      throw new Error("connection reset by harness")
    })()
  }
  const evs = Array.isArray(spec) ? spec : []
  return (async function* () {
    for (const e of evs) yield e
    if (!flaky || evs.length) drainDone()
  })()
}

const api = {
  client: {
    event: {
      async subscribe() {
        subscribed = true
        subscribeTimes.push(Date.now() - t0)
        if (disposeWhileSubscribing) {
          // The first subscription stays pending until the harness has
          // run the disposer (the dispose-while-subscribing race).
          return new Promise((resolve) => { resolveFirstSubscribe = resolve })
        }
        if (flaky) return streamFor(events[callIndex++])
        return streamFor(events)
      },
    },
  },
  // Telemetry must tolerate a partially-present api: only client is used.
}

async function main() {
  const mod = await import(path.resolve(pluginPath))
  const plugin = mod.default
  if (typeof plugin !== "object" || typeof plugin.setup !== "function") {
    return { registered: false, error: "missing default {id, setup}" }
  }
  const cleanup = await plugin.setup(api, {}, { id: plugin.id ?? "test" })

  if (disposeWhileSubscribing) {
    // Dispose while `await subscribe()` is still pending, then let the
    // subscription resolve with a live stream: the plugin must close the
    // late stream itself and publish nothing from it.
    if (typeof cleanup === "function") {
      try { await cleanup() } catch (e) { /* cleanup must be safe to call */ }
    }
    resolveFirstSubscribe(lateStreamProxy())
    await new Promise((resolve) => setTimeout(resolve, 500))
  } else {
    // Give the plugin's background SSE pump until the generator is drained
    // (or a timeout, so a broken plugin cannot hang the test).
    let timeoutTimer
    const timeoutP = new Promise((resolve) => {
      timeoutTimer = setTimeout(resolve, flaky ? 8000 : 2000)
    })
    await Promise.race([drained, timeoutP])
    clearTimeout(timeoutTimer)
    if (flaky) {
      // Let the pump reconnect a few more times so tests can see the delay
      // AFTER a connection that delivered events (the reset point).
      await new Promise((resolve) => setTimeout(resolve, 2000))
    }
    if (typeof cleanup === "function") {
      try { await cleanup() } catch (e) { /* cleanup must be safe to call */ }
    }
  }

  const agent = "harness-agent"
  const dir = path.join(stateDir, "harness")
  const read = (f) => {
    try { return fs.readFileSync(path.join(dir, f), "utf8") } catch { return null }
  }
  const state = read(`hook_state_${agent}.json`)
  const eventsOut = (read(`hook_events_${agent}.jsonl`) ?? "")
    .split("\n").filter(Boolean).map((l) => JSON.parse(l))
  return {
    registered: subscribed,
    state: state ? JSON.parse(state) : null,
    events: eventsOut,
    subscribe_times: subscribeTimes,
    sleep_delays: sleepDelays,
    late_stream_returned: lateStreamReturned,
  }
}

try {
  console.log(JSON.stringify(await main()))
} finally {
  // The harness must not leak its oc2harness-* state dir; removal runs
  // after the result JSON is printed so stdout keeps the result.
  fs.rmSync(stateDir, { recursive: true, force: true })
}
