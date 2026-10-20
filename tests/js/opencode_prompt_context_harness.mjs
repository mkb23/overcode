/**
 * Test harness for the prompt context the opencode plugins add to each
 * user prompt (the enhanced-context line, the one-shot rename notice).
 *
 * Driven by tests/unit/test_opencode_prompt_context.py: reads a JSON job
 * from stdin, runs the plugin's real hooks with job.env as process.env,
 * and prints a JSON result.
 *
 * v1 job:  {"version": 1, "plugin": "/abs/overcode-telemetry.mjs", "env": {...},
 *           "actions": [{"kind": "bus", "event": {...}},
 *                       {"kind": "chat", "input": {...}, "output": {...}}]}
 *   result: {"ok": true, "hooks": [...], "outputs": [<each chat output after the hook>]}
 *
 * v2 job:  {"version": 2, "plugin": "/abs/index.js", "env": {...},
 *           "sessions": {"<id>": {<session.get result>}},
 *           "prompts": [<prompt hook input>, ...]}
 *   result: {"ok": true, "registered": ["prompt"], "inputs": [<each input after the hook>]}
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

async function runV1(job, mod) {
  const hooks = (await mod.OvercodeTelemetryPlugin({})) || {}
  const outputs = []
  for (const action of job.actions || []) {
    if (action.kind === "bus") {
      await hooks.event({ event: action.event })
    } else if (action.kind === "chat") {
      await hooks["chat.message"](action.input, action.output)
      outputs.push(action.output)
    } else {
      throw new Error(`unknown action kind: ${action.kind}`)
    }
  }
  return { ok: true, hooks: Object.keys(hooks).sort(), outputs }
}

async function runV2(job, mod) {
  const handlers = {}
  const api = {
    session: {
      hook: async (name, fn) => {
        handlers[name] = fn
        return { dispose: () => { delete handlers[name] } }
      },
      get: async ({ sessionID }) => (job.sessions || {})[sessionID],
    },
  }
  const dispose = await mod.default.setup(api)
  const registered = Object.keys(handlers)
  const inputs = []
  for (const input of job.prompts || []) {
    if (handlers.prompt) await handlers.prompt(input)
    inputs.push(input)
  }
  if (typeof dispose === "function") dispose()
  return { ok: true, registered, inputs, disposed: Object.keys(handlers).length === 0 }
}

async function main() {
  const job = JSON.parse(await readStdin())
  const mod = await import(pathToFileURL(job.plugin).href)
  // The plugins read process.env when they load their hooks.
  process.env = { ...job.env }
  return job.version === 2 ? runV2(job, mod) : runV1(job, mod)
}

main()
  .then((result) => { process.stdout.write(JSON.stringify(result)) })
  .catch((err) => {
    process.stdout.write(JSON.stringify({ ok: false, error: String(err && err.stack || err) }))
    process.exitCode = 1
  })
