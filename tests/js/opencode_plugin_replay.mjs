/**
 * Replay harness for src/overcode/opencode_plugin/overcode-telemetry.js (#474).
 *
 * Unlike opencode_plugin_harness.mjs (which pokes the reducer through the
 * `internals` seam), this drives the plugin exactly the way opencode does:
 * it calls the exported factory to get the hooks object, then invokes
 * `hooks.event` / `hooks["chat.message"]` / `hooks["tool.execute.*"]` with
 * the verbatim payloads a spy plugin captured from a real session
 * (tests/fixtures_opencode_events/*.jsonl) or that a generator produced.
 *
 * Job (stdin JSON):
 *   {
 *     "plugin": "/abs/path/overcode-telemetry.mjs",
 *     "env": { "OVERCODE_SESSION_NAME": "...", "OVERCODE_TMUX_SESSION": "...",
 *              "OVERCODE_STATE_DIR": "/tmp/…" },
 *     "streams": [ {"name": "s1", "records": [ {hook, input, output} | {hook:"event", event} ]}, … ]
 *   }
 *
 * Each stream gets a fresh plugin instance and its own state dir
 * (<OVERCODE_STATE_DIR>/<name>), so one node process can replay hundreds of
 * generated streams.
 *
 * Result (stdout JSON):
 *   { "ok": true, "streams": [ { "name", "publishes": [[{event, tool_name}, …] per record],
 *                                "state": <final hook_state json or null> }, … ] }
 */

import fs from "node:fs"
import path from "node:path"
import { pathToFileURL } from "node:url"

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = ""
    process.stdin.setEncoding("utf8")
    process.stdin.on("data", (chunk) => { data += chunk })
    process.stdin.on("end", () => resolve(data))
    process.stdin.on("error", reject)
  })
}

function readLog(logPath) {
  try {
    return fs.readFileSync(logPath, "utf8").split("\n").filter((l) => l !== "")
  } catch (e) {
    return []
  }
}

function readState(statePath) {
  try {
    return JSON.parse(fs.readFileSync(statePath, "utf8"))
  } catch (e) {
    return null
  }
}

async function replayStream(mod, baseEnv, stream) {
  const stateDir = path.join(baseEnv.OVERCODE_STATE_DIR, stream.name)
  const env = { ...baseEnv, OVERCODE_STATE_DIR: stateDir }
  const agent = env.OVERCODE_SESSION_NAME
  const dir = path.join(stateDir, env.OVERCODE_TMUX_SESSION)
  const logPath = path.join(dir, `hook_events_${agent}.jsonl`)
  const statePath = path.join(dir, `hook_state_${agent}.json`)

  const savedEnv = process.env
  process.env = env
  let hooks
  try {
    hooks = await mod.OvercodeTelemetryPlugin({ directory: "/proj", worktree: "/proj" })
  } finally {
    process.env = savedEnv
  }
  if (!hooks || !hooks.event) {
    throw new Error(`plugin registered no hooks for stream ${stream.name}`)
  }

  const publishes = []
  let seen = readLog(logPath).length
  for (const record of stream.records) {
    const hook = record.hook
    if (hook === "event") {
      await hooks.event({ event: record.event })
    } else if (hook === "__load__") {
      /* spy bookkeeping, not a hook */
    } else if (typeof hooks[hook] === "function") {
      await hooks[hook](record.input, record.output)
    }
    const lines = readLog(logPath)
    const fresh = lines.slice(seen).map((l) => {
      try {
        const e = JSON.parse(l)
        return { event: e.event, tool_name: e.tool_name ?? null }
      } catch (err) {
        return { event: "<unparseable>", tool_name: null }
      }
    })
    seen = lines.length
    publishes.push(fresh)
  }
  return { name: stream.name, publishes, state: readState(statePath) }
}

async function main() {
  const job = JSON.parse(await readStdin())
  const mod = await import(pathToFileURL(job.plugin).href)
  const exported = Object.keys(mod)
  if (exported.length !== 1 || exported[0] !== "OvercodeTelemetryPlugin") {
    throw new Error(`plugin must export exactly OvercodeTelemetryPlugin, got: ${exported}`)
  }
  const streams = []
  for (const stream of job.streams || []) {
    streams.push(await replayStream(mod, job.env, stream))
  }
  return { ok: true, streams }
}

main()
  .then((result) => { process.stdout.write(JSON.stringify(result)) })
  .catch((err) => {
    process.stdout.write(JSON.stringify({ ok: false, error: String(err && err.stack || err) }))
    process.exitCode = 1
  })
