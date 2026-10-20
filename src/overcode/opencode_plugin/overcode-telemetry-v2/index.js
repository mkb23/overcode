/**
 * overcode-telemetry-v2 — opencode2 (OpenCode 2.0 preview) TUI telemetry
 * plugin: the server entrypoint.
 *
 * OVERCODE MANAGED FILE. Overcode copies this into
 * `<project>/.opencode/plugins/overcode-telemetry-v2/` when it launches an
 * opencode2 agent. Delete freely — overcode recreates it.
 * OVERCODE-PLUGIN-MARKER: overcode-telemetry-v2
 *
 * Verified live against opencode2 v0.0.0-dev-19272: opencode2's server scans
 * `<project>/.opencode/plugins/` and loads each entry — for a plugin
 * directory that means this `index.js` (the `server.*`/`index.*` entrypoint,
 * validated as {id, effect|setup}). The TUI only sees plugins the server
 * registered, so this entrypoint is what makes the whole directory
 * discoverable; the telemetry runs in `tui.js` on the TUI side (the server
 * plugin context has no event bus).
 *
 * The server side adds overcode's prompt context (the enhanced-context
 * line, the one-shot rename notice) to each user prompt, as Claude's
 * UserPromptSubmit hook stdout does. Verified live on dev-19272 (Oct 20
 * 2026): `api.session.hook("prompt", fn)` runs before the prompt is
 * stored, a change to `input.prompt.text` is what the model receives (and
 * the TUI shows), and the standalone server has the agent's env. A
 * `subagent` child's prompt runs the hook too; its session has a
 * `parentID` and is left alone.
 */
import { extendPrompt, isOvercodeSession } from "./overcode-telemetry-core.mjs"

export default {
  id: "overcode-telemetry-v2",
  setup: async (api) => {
    const env = process.env
    if (!isOvercodeSession(env)) return
    if (!api || !api.session || typeof api.session.hook !== "function") return
    let handle = null
    try {
      handle = await api.session.hook("prompt", (input) => extendPrompt(api, env, input))
    } catch (e) {
      return // an api-shape change must not fail the plugin load
    }
    return () => {
      if (handle && typeof handle.dispose === "function") {
        Promise.resolve(handle.dispose()).catch(() => {})
      }
    }
  },
}
