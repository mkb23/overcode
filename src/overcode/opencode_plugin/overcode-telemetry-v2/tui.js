/**
 * overcode-telemetry-v2 — opencode2 (OpenCode 2.0 preview) TUI telemetry
 * plugin: the TUI entrypoint.
 *
 * OVERCODE MANAGED FILE. Overcode copies this into
 * `<project>/.opencode/plugins/overcode-telemetry-v2/` when it launches an
 * opencode2 agent. Delete freely — overcode recreates it.
 * OVERCODE-PLUGIN-MARKER: overcode-telemetry-v2
 *
 * Verified live against opencode2 v0.0.0-dev-19272 (Sep 15 2026):
 *
 *  * The TUI discovers plugins through the *server's* plugin list, which
 *    requires a server entrypoint (index.js — a bare {id, tui} default export
 *    fails with "Plugin must export a default definition with an id and an
 *    effect or setup function") and only registers `features.tui` when the
 *    plugin directory also resolves a `tui` entrypoint — this file.
 *  * The TUI validates the tui module's default export as {id, setup} and
 *    invokes `setup(api)` — the entry function is named `setup`, not `tui`.
 *  * `api.event` does NOT exist in this build; the bus is reachable only
 *    through `api.client.event.subscribe({directory})`, which resolves to an
 *    async generator of `{id, created, type, location, data}` envelopes (the
 *    server's SSE /event stream). Without the `directory` parameter only
 *    global events arrive — no session traffic.
 *
 * The reducer (overcode-telemetry-core.mjs) maps the stream onto the same
 * hook files the v1 plugin writes, consumed by the unchanged
 * HookStatusDetector. Telemetry must never take the TUI down: every step is
 * guarded and `setup` returns without subscribing unless overcode launched
 * the session (OVERCODE_* env vars present).
 */
import {
  createTelemetry,
  isOvercodeSession,
} from "./overcode-telemetry-core.mjs"

export default {
  id: "overcode-telemetry-v2",
  setup: async (api) => {
    if (!isOvercodeSession(process.env)) return
    // A future api-shape change must fail quiet and fast, not spin the
    // reconnect loop below: only subscribe when the bus this build
    // exposes is actually reachable.
    if (
      !api ||
      !api.client ||
      !api.client.event ||
      typeof api.client.event.subscribe !== "function"
    ) {
      return
    }
    let telemetry
    try {
      telemetry = createTelemetry(process.env)
    } catch (e) {
      return
    }

    let closed = false
    let stream = null
    const pump = (async () => {
      // The SSE stream can die with the server (restarts are observed as
      // `server.connected` events); reconnect with a small backoff so a
      // long-lived agent keeps its telemetry.
      let delay = 250
      while (!closed) {
        try {
          stream = await api.client.event.subscribe({
            directory: process.cwd(),
          })
          if (closed) {
            if (stream && stream.return) {
              try { await stream.return() } catch (e) { /* teardown: ignore */ }
            }
            break
          }
          for await (const ev of stream) {
            if (closed) break
            // Reset the backoff only once this connection has actually
            // delivered an event. subscribe() resolves to a LAZY async
            // generator, so connection failures surface inside for-await —
            // resetting right after subscribe() resolves would reconnect a
            // dying stream at the 250ms floor forever (~4x/s) instead of
            // backing off 250ms -> 5s.
            delay = 250
            try { telemetry.handleBusEvent(ev) } catch (e) { /* bad payload: skip */ }
          }
        } catch (e) {
          /* telemetry must never take the TUI down */
        }
        if (closed) break
        await new Promise((resolve) => setTimeout(resolve, delay))
        delay = Math.min(delay * 2, 5000)
      }
    })()

    return () => {
      closed = true
      if (stream && stream.return) {
        Promise.resolve(stream.return()).catch(() => {})
      }
    }
  },
}
