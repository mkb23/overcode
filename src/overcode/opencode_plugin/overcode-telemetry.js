/**
 * overcode-telemetry — opencode plugin that publishes overcode hook-state files.
 *
 * OVERCODE MANAGED FILE. Overcode copies this into `<project>/.opencode/plugins/`
 * when it launches an opencode agent, and refreshes it in place on later
 * launches. Delete it freely — overcode recreates it. If you replace it with
 * your own file (drop the marker line below), overcode leaves it alone.
 * OVERCODE-PLUGIN-MARKER: overcode-telemetry
 *
 * It translates opencode's bus events into the JSON files overcode's
 * HookStatusDetector already reads for Claude Code:
 *
 *   <state-dir>/<tmux-session>/agents/<session-id>/hook_state.json    (latest snapshot)
 *   <state-dir>/<tmux-session>/agents/<session-id>/hook_events.jsonl  (append-only log)
 *
 * (keyed by OVERCODE_SESSION_ID; by the agent's name, hook_state_<agent>.json,
 * when an older launcher did not set it)
 *
 * It is a NO-OP unless OVERCODE_SESSION_NAME and OVERCODE_TMUX_SESSION are both
 * in the environment, so it is inert in any opencode session overcode did not
 * launch — including one that picked the file up from a shared project dir.
 *
 * ── Observed event vocabulary (opencode v1.18.19, macOS/arm64, verified live) ──
 *
 * The `event` hook receives `{ event: { type, properties } }`. Types seen:
 *
 *   session.created   { sessionID, info: { id, slug, directory, title, agent,
 *                                          model: {id, providerID}, cost,
 *                                          tokens: {...}, time: {...} } }
 *   session.updated   { sessionID, info: { ..., cost, tokens } }
 *   session.status    { sessionID, status: { type: "busy" | "idle" } }
 *   session.idle      { sessionID }
 *   session.diff      { sessionID, diff: [] }
 *   session.error     { sessionID?, error? }        (shape unconfirmed; handled defensively)
 *   session.deleted   { ... }                       (not observed live)
 *   permission.asked  { id, sessionID, permission: "bash", patterns: [...],
 *                       metadata: { command }, always: [...],
 *                       tool: { messageID, callID } }
 *   permission.replied{ sessionID, requestID, reply: "once" | "always" | "reject" }
 *   message.updated   { sessionID, info: { id, role: "user"|"assistant", tokens,
 *                                          cost, time: { created, completed } } }
 *   message.part.updated / message.part.delta   (very high volume — ignored)
 *   plugin.added / catalog.updated / reference.updated / integration.updated (ignored)
 *
 * Dedicated hooks (lower volume, richer payloads — preferred where they exist):
 *
 *   chat.message              (input { sessionID, agent, model },
 *                              output { message: { id, role: "user" }, parts })
 *   tool.execute.before       (input { tool, sessionID, callID }, output { args })
 *   tool.execute.after        (input { tool, sessionID, callID, args },
 *                              output { title, metadata, output })
 *
 * Observed ordering for a permissioned tool call:
 *   chat.message → session.status(busy) → tool.execute.before → permission.asked
 *   → permission.replied → [tool.execute.after when allowed] → session.status(idle)
 *   → session.idle
 * On reject there is no tool.execute.after; session.idle follows directly.
 *
 * Re-verified against v1.18.29 (Sep 21 2026; corpus in
 * tests/fixtures_opencode_events/, spec in tests/opencode_oracle.py):
 *   * `session.idle` is marked deprecated in the schema; `session.status
 *     {type: "idle"}` precedes it by ~1ms and is the signal to rely on.
 *   * A `task` sub-agent runs its whole turn on this same plugin —
 *     session.created {info: {parentID}}, chat.message, tool.execute.*,
 *     session.idle — with its own session id. Only its permission.asked /
 *     permission.replied concern the parent (the dialog is answered in the
 *     parent's TUI).
 *   * A double-Escape interrupt is session.error {name: "MessageAbortedError"}
 *     followed by idle. A provider failure is session.error {name: "APIError",
 *     data: {message}} followed by idle within the same millisecond.
 *   * `/exit` emits nothing at all (no session.deleted); the Python detector
 *     spots the bare shell prompt instead.
 *
 * ── Mapping to overcode's hook vocabulary (hook_status_detector._HOOK_STATUS_MAP) ──
 *
 *   chat.message / message.updated(role=user, unseen)  → UserPromptSubmit  (running)
 *   session.status(busy|retry), not already running    → UserPromptSubmit  (running; safety net)
 *   tool.execute.before                                → PreToolUse        (running)
 *   permission.asked  (root or child session)          → PermissionRequest (waiting_approval)
 *   permission.replied, reply != "reject"              → PreToolUse        (running)
 *   permission.replied, reply == "reject"              → PostToolUse       (running)
 *   tool.execute.after                                 → PostToolUse       (running)
 *   session.status(idle) / session.idle                → Stop              (waiting_user; once per settle)
 *   session.error, not MessageAbortedError             → StopFailure       (error; the idle after it publishes nothing)
 *   session.deleted                                    → SessionEnd        (terminated)
 *   anything else from a child session                 → ignored
 *
 * chat.message also adds overcode's prompt context to the agent's own
 * prompts (not a `task` child's): `overcode hook-handler --prompt-context`
 * prints what Claude's UserPromptSubmit hook prints (the enhanced-context
 * line, the one-shot rename notice), and the plugin adds it as a synthetic
 * text part. The agent's first prompts go to agents/<id>/prompts.jsonl.
 */

// The hook-file writer (snapshot + event log, obligation carry-over, the
// session-id cap), the tool-name map and the foreground classifier are
// shared with the opencode2 plugin. Overcode installs the core at this
// relative path next to this file; opencode loads only
// `{plugin,plugins}/*.{ts,js}`, so neither the subdirectory nor the .mjs
// is ever taken for a plugin of its own. Imported dynamically so a missing
// core turns telemetry off instead of failing opencode's plugin load.
let core = null
try {
  core = await import("./overcode-telemetry-v2/overcode-telemetry-core.mjs")
} catch (e) {
  core = null
}
const {
  MAX_SESSION_IDS,
  ascendingId,
  canonicalToolName,
  createWriter,
  isOvercodeSession,
  promptContext,
  resolveStateDir,
} = core || {}

// What each published event means for the agent, mirrored from
// hook_status_detector._HOOK_STATUS_MAP — the reducer keeps the last one so
// a repeated idle signal settles once and an error survives the idle that
// follows it.
const EVENT_STATUS = {
  UserPromptSubmit: "running",
  PreToolUse: "running",
  PostToolUse: "running",
  PermissionRequest: "waiting_approval",
  Stop: "waiting_user",
  StopFailure: "error",
  SessionEnd: "terminated",
}

// session.error names that are the user's own doing, not a failure. A
// double-Escape mid-generation publishes session.error {name:
// "MessageAbortedError"} and then goes idle (live-captured, v1.18.29).
const ABORT_ERROR_NAMES = ["MessageAbortedError"]

// The maximum length of a failure reason written to the state/events files,
// so a huge provider error payload cannot bloat the hook files.
const MAX_ERROR_REASON_CHARS = 200

// How many user message ids to remember for UserPromptSubmit de-duplication.
// `message.updated` re-fires for the same user message at the end of a turn,
// which would otherwise overwrite Stop and pin the agent green.
const MAX_SEEN_MESSAGES = 200

/**
 * A bounded reason string from a session.error payload, or null.
 *
 * v1.18.29 delivers `{name, data: {message, statusCode, ...}}` (live-captured
 * APIError); older shapes with a top-level `message` and bare strings are
 * accepted too.
 */
function failureReason(err) {
  if (err == null) return null
  let text
  if (typeof err === "string") text = err
  else if (typeof err === "object") {
    const data = err.data && typeof err.data === "object" ? err.data : {}
    if (typeof data.message === "string") text = data.message
    else if (typeof err.message === "string") text = err.message
    else {
      try { text = JSON.stringify(err) } catch (e) { return null }
    }
    if (err.name && typeof err.name === "string" && text && !text.startsWith(err.name)) {
      text = `${err.name}: ${text}`
    }
  } else text = String(err)
  if (!text) return null
  return text.length > MAX_ERROR_REASON_CHARS ? text.slice(0, MAX_ERROR_REASON_CHARS) : text
}

/**
 * The event→overcode-event reducer, with the session-scoping and de-duplication
 * state an opencode process needs.
 *
 * Re-verified against opencode v1.18.29 (Sep 21 2026) with the captures in
 * tests/fixtures_opencode_events/; tests/opencode_oracle.py is the
 * executable specification of what each event below must publish.
 */
function createTelemetry(env, options = {}) {
  const writer = options.writer || createWriter(env, options)
  const rootSessionIds = []
  // Child sessions the `task` tool spawns. Live-captured: a child runs a
  // full turn of its own — chat.message, tool.execute.*, session.idle — on
  // the same plugin, while the parent sits inside its Task call. Children
  // are remembered so owns() can NEVER adopt one, even on the adopt path
  // a resumed conversation needs; otherwise the child's idle publishes a
  // Stop for the parent mid-Task and its id pollutes agent_session_ids.
  const childSessionIds = []
  const seenUserMessages = []
  // permission request id → what it was gating. permission.replied carries
  // only `requestID`, so without this the event that clears waiting_approval
  // would have no tool name and the status badge would lose its label.
  const pendingPermissions = new Map()
  // The status of the last event this process published, so an idle signal
  // that arrives twice (session.status {idle} AND the deprecated
  // session.idle) settles once, and the idle that follows a session.error
  // does not overwrite the StopFailure the user needs to see.
  let current = null

  function rememberRoot(sessionId) {
    if (!sessionId) return
    if (rootSessionIds.includes(sessionId)) return
    rootSessionIds.push(sessionId)
    while (rootSessionIds.length > MAX_SESSION_IDS) rootSessionIds.shift()
  }

  function rememberChild(sessionId) {
    if (!sessionId || childSessionIds.includes(sessionId)) return
    childSessionIds.push(sessionId)
    while (childSessionIds.length > MAX_SESSION_IDS) childSessionIds.shift()
  }

  /**
   * True when an event belongs to a session this agent owns.
   *
   * Until a root session is known (a resumed conversation emits no
   * session.created), the first non-child session id seen is adopted —
   * otherwise a resumed agent would publish nothing at all. Child sessions
   * are filtered out first, even on the adopt path.
   */
  function owns(sessionId, { adopt = false } = {}) {
    if (!sessionId) return false
    if (childSessionIds.includes(sessionId)) return false
    if (rootSessionIds.includes(sessionId)) return true
    if (adopt || rootSessionIds.length === 0) {
      rememberRoot(sessionId)
      return true
    }
    return false
  }

  /** A root this agent owns, or a child of one — permission dialogs for a
   *  sub-agent's tool are answered in the parent's TUI, so those (and only
   *  those) child events are the parent's business. */
  function ownsOrChild(sessionId) {
    if (!sessionId) return false
    if (childSessionIds.includes(sessionId)) return true
    return owns(sessionId)
  }

  function activeId(sessionId) {
    return sessionId && rootSessionIds.includes(sessionId) ? sessionId : undefined
  }

  function publish(event, detail = {}) {
    current = EVENT_STATUS[event] || current
    writer.publish(event, detail)
  }

  function settle(sessionId) {
    if (current === "waiting_user" || current === "error" || current === "terminated") return
    publish("Stop", { agentSessionId: activeId(sessionId) })
  }

  function onUserMessage(sessionId, messageId) {
    if (messageId) {
      if (seenUserMessages.includes(messageId)) return
      seenUserMessages.push(messageId)
      while (seenUserMessages.length > MAX_SEEN_MESSAGES) seenUserMessages.shift()
    }
    if (!owns(sessionId, { adopt: true })) return
    publish("UserPromptSubmit", { agentSessionId: activeId(sessionId) })
  }

  function onToolBefore(input, output) {
    const sessionId = input && input.sessionID
    if (!owns(sessionId)) return
    const toolName = canonicalToolName(input && input.tool)
    const toolInput = output && output.args ? output.args : undefined
    publish("PreToolUse", {
      toolName,
      toolInput,
      toolUseId: input && input.callID,
      agentSessionId: activeId(sessionId),
    })
  }

  function onToolAfter(input) {
    const sessionId = input && input.sessionID
    if (!owns(sessionId)) return
    const toolName = canonicalToolName(input && input.tool)
    publish("PostToolUse", {
      toolName,
      toolInput: input && input.args ? input.args : undefined,
      toolUseId: input && input.callID,
      agentSessionId: activeId(sessionId),
    })
  }

  function handleBusEvent(busEvent) {
    if (!busEvent || typeof busEvent !== "object") return
    const type = busEvent.type
    const props = busEvent.properties || {}
    const sessionId = props.sessionID

    switch (type) {
      case "session.created": {
        const info = props.info || {}
        // Child sessions (spawned by the `task` tool) carry a parent id; only
        // root sessions are this agent's conversation.
        if (info.parentID || info.parent_id || props.parentID) {
          rememberChild(sessionId || info.id)
          return
        }
        rememberRoot(sessionId || info.id)
        return
      }

      case "message.updated": {
        const info = props.info || {}
        if (info.role !== "user") return
        onUserMessage(sessionId || info.sessionID, info.id)
        return
      }

      case "permission.asked": {
        if (!ownsOrChild(sessionId)) return
        const toolMeta = props.tool || {}
        const toolName = canonicalToolName(props.permission)
        const toolInput =
          props.metadata && typeof props.metadata === "object" ? props.metadata : undefined
        if (props.id) {
          pendingPermissions.set(props.id, {
            toolName,
            toolInput,
            toolUseId: toolMeta.callID,
          })
          while (pendingPermissions.size > MAX_SESSION_IDS) {
            pendingPermissions.delete(pendingPermissions.keys().next().value)
          }
        }
        publish("PermissionRequest", {
          toolName,
          toolInput,
          toolUseId: toolMeta.callID,
          agentSessionId: activeId(sessionId),
        })
        return
      }

      case "permission.replied": {
        if (!ownsOrChild(sessionId)) return
        // Either way the approval gate is gone and the agent is working again:
        // an allow resumes the tool call (PreToolUse), a reject hands the
        // refusal back to the model (PostToolUse). Both map to `running`, and
        // the idle that ends the turn settles it.
        const rejected = props.reply === "reject"
        const gated = pendingPermissions.get(props.requestID) || {}
        pendingPermissions.delete(props.requestID)
        publish(rejected ? "PostToolUse" : "PreToolUse", {
          toolName: gated.toolName,
          toolInput: gated.toolInput,
          toolUseId: gated.toolUseId,
          agentSessionId: activeId(sessionId),
        })
        return
      }

      case "session.status": {
        // The non-deprecated turn signal (session.idle is marked deprecated
        // in v1.18.29's schema). Idle settles the turn; busy/retry is a
        // safety net that only fires when the turn's own hooks were missed.
        if (!owns(sessionId)) return
        const kind = props.status && props.status.type
        if (kind === "idle") {
          settle(sessionId)
          return
        }
        if (kind === "busy" || kind === "retry") {
          if (current === "running" || current === "waiting_approval") return
          publish("UserPromptSubmit", { agentSessionId: activeId(sessionId) })
        }
        return
      }

      case "session.idle": {
        if (!owns(sessionId)) return
        settle(sessionId)
        return
      }

      case "session.error": {
        // Accept it whether or not it names a session, because an error
        // that ends the turn is worth surfacing either way. A double-Escape
        // also arrives here (MessageAbortedError, verified live) — that is
        // the user's interrupt, not a failure: the idle after it is a Stop.
        if (sessionId && !owns(sessionId)) return
        const err = props.error
        const name = err && typeof err === "object" ? err.name : undefined
        if (ABORT_ERROR_NAMES.includes(name)) return
        publish("StopFailure", {
          agentSessionId: activeId(sessionId),
          error: failureReason(err),
        })
        return
      }

      case "session.deleted": {
        if (sessionId && !owns(sessionId)) return
        publish("SessionEnd", {})
        return
      }

      default:
        return
    }
  }

  /** True for a prompt in this agent's own conversation, never a `task`
   *  child's: only those get overcode's prompt context. */
  function ownsPrompt(sessionId) {
    return owns(sessionId, { adopt: true })
  }

  return {
    handleBusEvent,
    onUserMessage,
    onToolBefore,
    onToolAfter,
    ownsPrompt,
    writer,
    rootSessionIds,
    childSessionIds,
  }
}

/** The user's own words in a chat.message: its non-synthetic text parts. */
function promptText(parts) {
  if (!Array.isArray(parts)) return ""
  return parts
    .filter((p) => p && p.type === "text" && !p.synthetic && typeof p.text === "string")
    .map((p) => p.text)
    .join("\n")
}

/**
 * Add overcode's prompt context (enhanced-context line, rename notice) to a
 * user message, the way Claude's UserPromptSubmit hook stdout reaches the
 * model.
 *
 * Verified live on v1.18.29: chat.message gets the parts array opencode
 * saves after the hook returns, and awaits the hook, so a pushed text part
 * is stored with the message and sent to the model. `synthetic` keeps it
 * out of the TUI's transcript (as opencode's own @file expansions are).
 * The part needs an id of opencode's ascending `prt_` shape.
 */
async function addPromptContext(env, telemetry, input, output, options = {}) {
  if (!output || !Array.isArray(output.parts)) return
  const message = output.message || {}
  if (message.role && message.role !== "user") return
  const sessionId = (input && input.sessionID) || message.sessionID
  if (!telemetry.ownsPrompt(sessionId)) return
  const text = await promptContext(env, promptText(output.parts), options)
  if (!text) return
  output.parts.push({
    id: ascendingId("prt"),
    messageID: message.id,
    sessionID: sessionId,
    type: "text",
    text,
    synthetic: true,
  })
}

/**
 * The plugin factory — and the file's ONLY export.
 *
 * opencode invokes *every* export of a plugin module as a factory, so a helper
 * left exported gets called with the plugin context and throws during load,
 * taking the whole opencode process down with it (verified the hard way
 * against v1.18.19). The helpers above stay module-local and are reachable for
 * tests through `OvercodeTelemetryPlugin.internals`.
 */
export const OvercodeTelemetryPlugin = async () => {
  // Registering nothing is the whole safety story: if this file is ever
  // picked up by an opencode session overcode did not launch, it costs one
  // env lookup and adds no hooks at all.
  if (!core || !isOvercodeSession(process.env)) return {}

  let telemetry
  try {
    telemetry = createTelemetry(process.env)
  } catch (e) {
    return {}
  }

  // Telemetry must never take opencode down: every hook swallows its errors.
  const guard = (fn) => async (...args) => {
    try {
      fn(...args)
    } catch (e) {
      /* ignore */
    }
  }

  const env = process.env
  return {
    event: guard((input) => telemetry.handleBusEvent(input && input.event)),
    "chat.message": async (input, output) => {
      try {
        const message = (output && output.message) || {}
        if (message.role && message.role !== "user") return
        telemetry.onUserMessage((input && input.sessionID) || message.sessionID, message.id)
        await addPromptContext(env, telemetry, input, output)
      } catch (e) {
        /* ignore */
      }
    },
    "tool.execute.before": guard((input, output) => telemetry.onToolBefore(input, output)),
    "tool.execute.after": guard((input, output) => telemetry.onToolAfter(input, output)),
  }
}

// Test seam only — a property, never a second export (see above).
OvercodeTelemetryPlugin.internals = {
  addPromptContext,
  canonicalToolName,
  createTelemetry,
  createWriter,
  isOvercodeSession,
  resolveStateDir,
}
