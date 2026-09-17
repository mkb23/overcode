/**
 * overcode-telemetry-v2 — opencode2 (OpenCode 2.0 preview) TUI telemetry
 * plugin: the server entrypoint stub.
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
 * registered, so this stub is what makes the whole directory discoverable;
 * the actual telemetry runs in `tui.js` on the TUI side (the server plugin
 * context has no event bus).
 */
export default {
  id: "overcode-telemetry-v2",
  setup: async () => {},
}
