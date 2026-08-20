/**
 * Test harness for src/overcode/opencode_plugin/overcode-telemetry.js.
 *
 * Driven by tests/unit/test_opencode_plugin.py: reads a JSON job from stdin,
 * replays it through the plugin, and prints a JSON result to stdout. This
 * exists because the plugin is the one piece of Phase 5 that Python cannot
 * exercise directly — it runs inside opencode's Bun process.
 *
 * Job shape:
 *   {
 *     "plugin": "/abs/path/to/overcode-telemetry.mjs",
 *     "env": { "OVERCODE_SESSION_NAME": "...", ... },
 *     "actions": [
 *       {"kind": "bus",   "event":  {"type": "...", "properties": {...}}},
 *       {"kind": "chat",  "input": {...}, "output": {...}},
 *       {"kind": "before","input": {...}, "output": {...}},
 *       {"kind": "after", "input": {...}, "output": {...}}
 *     ]
 *   }
 *
 * Result shape:
 *   { "ok": true, "hooks": [...], "rootSessionIds": [...] }
 *   { "ok": false, "error": "..." }
 */

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

async function main() {
  const job = JSON.parse(await readStdin())
  const mod = await import(pathToFileURL(job.plugin).href)

  // Exercise the real plugin entry point too, so the no-op guard and the hook
  // registration are covered rather than only the reducer beneath them.
  const savedEnv = process.env
  process.env = { ...job.env }
  // opencode calls every export as a plugin factory, so a stray exported
  // helper would crash the host process at load time. Guard against that here.
  const exported = Object.keys(mod)
  if (exported.length !== 1 || exported[0] !== "OvercodeTelemetryPlugin") {
    throw new Error(`plugin must export exactly OvercodeTelemetryPlugin, got: ${exported}`)
  }

  let hooks
  try {
    hooks = await mod.OvercodeTelemetryPlugin({})
  } finally {
    process.env = savedEnv
  }

  const hookNames = Object.keys(hooks || {}).sort()
  if (hookNames.length === 0) {
    return { ok: true, hooks: hookNames, rootSessionIds: [] }
  }

  const telemetry = mod.OvercodeTelemetryPlugin.internals.createTelemetry(job.env)
  for (const action of job.actions || []) {
    switch (action.kind) {
      case "bus":
        telemetry.handleBusEvent(action.event)
        break
      case "chat": {
        const message = (action.output && action.output.message) || {}
        telemetry.onUserMessage(
          (action.input && action.input.sessionID) || message.sessionID,
          message.id,
        )
        break
      }
      case "before":
        telemetry.onToolBefore(action.input, action.output)
        break
      case "after":
        telemetry.onToolAfter(action.input, action.output)
        break
      default:
        throw new Error(`unknown action kind: ${action.kind}`)
    }
  }

  return { ok: true, hooks: hookNames, rootSessionIds: telemetry.rootSessionIds }
}

main()
  .then((result) => { process.stdout.write(JSON.stringify(result)) })
  .catch((err) => {
    process.stdout.write(JSON.stringify({ ok: false, error: String(err && err.stack || err) }))
    process.exitCode = 1
  })
