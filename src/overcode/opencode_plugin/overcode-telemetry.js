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
 *   <state-dir>/<tmux-session>/hook_state_<agent>.json    (latest snapshot)
 *   <state-dir>/<tmux-session>/hook_events_<agent>.jsonl  (append-only log)
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
 */

import fs from "node:fs"
import os from "node:os"
import path from "node:path"

// Must match hook_handler._EVENT_LOG_ROTATE_BYTES / _EVENT_LOG_KEEP_LINES.
const EVENT_LOG_ROTATE_BYTES = 100 * 1024
const EVENT_LOG_KEEP_LINES = 200

// How many root session ids to remember. The stats reader sums every id it is
// given, so this bounds a long-lived agent that keeps hitting /new.
const MAX_SESSION_IDS = 20

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

// opencode names its tools in lowercase; overcode's detector (and the badge
// vocabulary) speaks Claude's CamelCase. Unmapped names are title-cased so a
// new opencode tool still renders sensibly instead of vanishing.
const TOOL_NAME_MAP = {
  bash: "Bash",
  edit: "Edit",
  glob: "Glob",
  grep: "Grep",
  list: "List",
  ls: "List",
  patch: "Edit",
  read: "Read",
  skill: "Skill",
  task: "Task",
  todoread: "TodoRead",
  todowrite: "TodoWrite",
  webfetch: "WebFetch",
  websearch: "WebSearch",
  write: "Write",
}

// Mirrors hook_handler._BLOCKED_ON_PATTERNS so the status-detail column can
// say *why* a foreground Bash looks stalled.
const BLOCKED_ON_PATTERNS = [
  ["ci", /\b(gh\s+run\s+watch|gh\s+pr\s+checks\s+--watch)\b/],
  ["process", /\b(tail\s+-[fF]|kubectl\s+wait|docker\s+wait|wait-on)\b/],
  ["sleep", /^\s*sleep\s+\d/],
]

function canonicalToolName(name) {
  if (!name || typeof name !== "string") return null
  const mapped = TOOL_NAME_MAP[name.toLowerCase()]
  if (mapped) return mapped
  return name.charAt(0).toUpperCase() + name.slice(1)
}

function classifyBlockedOn(command) {
  if (typeof command !== "string" || !command) return null
  for (const [kind, pattern] of BLOCKED_ON_PATTERNS) {
    if (pattern.test(command)) return kind
  }
  return null
}

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

function computeForeground(event, toolName, toolInput) {
  if (event !== "PreToolUse" || !toolName) return null
  const fg = { kind: "tool", tool: toolName }
  if (toolName === "Bash" && toolInput && typeof toolInput === "object") {
    const blockedOn = classifyBlockedOn(toolInput.command)
    if (blockedOn) fg.blocked_on = blockedOn
  }
  return fg
}

/**
 * Resolve the hook-state directory the same way hook_handler._get_hook_state_path
 * does: OVERCODE_STATE_DIR when set, else ~/.overcode/sessions.
 */
function resolveStateDir(env, homedir) {
  const base = env.OVERCODE_STATE_DIR || path.join(homedir, ".overcode", "sessions")
  return path.join(base, env.OVERCODE_TMUX_SESSION)
}

/**
 * The pure-ish writer.
 *
 * `env` needs OVERCODE_SESSION_NAME + OVERCODE_TMUX_SESSION (and optionally
 * OVERCODE_STATE_DIR). `now` is injectable for deterministic tests.
 */
function createWriter(env, options = {}) {
  const homedir = options.homedir || os.homedir()
  const now = options.now || (() => Date.now() / 1000)
  const dir = resolveStateDir(env, homedir)
  const agent = env.OVERCODE_SESSION_NAME
  const statePath = path.join(dir, `hook_state_${agent}.json`)
  const logPath = path.join(dir, `hook_events_${agent}.jsonl`)

  function readPrevious() {
    try {
      const parsed = JSON.parse(fs.readFileSync(statePath, "utf8"))
      return parsed && typeof parsed === "object" ? parsed : {}
    } catch (e) {
      return {}
    }
  }

  function writeState(event, detail = {}) {
    const prev = readPrevious()
    const timestamp = now()

    let loadedSkills = Array.isArray(prev.loaded_skills) ? prev.loaded_skills.slice() : []
    if (detail.toolName === "Skill" && detail.toolInput && typeof detail.toolInput === "object") {
      const skill = detail.toolInput.skill || detail.toolInput.name
      if (skill && !loadedSkills.includes(skill)) loadedSkills = loadedSkills.concat([skill])
    }

    const state = { event, timestamp }
    if (detail.toolName != null) state.tool_name = detail.toolName
    if (detail.toolInput != null) state.tool_input = detail.toolInput
    if (detail.toolUseId != null) state.tool_use_id = detail.toolUseId
    // StopFailure only: the Python detector validates `event` + `timestamp`
    // and passes the dict through, so the extra field is tolerated.
    if (detail.error != null) state.error = detail.error
    if (loadedSkills.length) state.loaded_skills = loadedSkills

    // Obligations are Claude-tool concepts (ScheduleWakeup, CronCreate,
    // Monitor, background Bash) with no opencode analogue, so the list is only
    // carried forward — never armed here — and cleared when the session ends.
    const obligations = event === "SessionEnd"
      ? []
      : (Array.isArray(prev.pending_obligations) ? prev.pending_obligations : [])
    if (obligations.length) state.pending_obligations = obligations

    const foreground = computeForeground(event, detail.toolName, detail.toolInput)
    if (foreground) state.foreground = foreground

    // opencode mints its own `ses_…` ids; recording them here is what lets the
    // stats reader find the right SQLite rows without guessing by directory.
    const ids = Array.isArray(prev.agent_session_ids) ? prev.agent_session_ids.slice() : []
    if (detail.agentSessionId && !ids.includes(detail.agentSessionId)) {
      ids.push(detail.agentSessionId)
      while (ids.length > MAX_SESSION_IDS) ids.shift()
    }
    if (ids.length) state.agent_session_ids = ids
    const activeId = detail.agentSessionId || prev.agent_session_id
    if (activeId) state.agent_session_id = activeId

    fs.mkdirSync(dir, { recursive: true })
    const tmp = `${statePath}.${process.pid}.tmp`
    fs.writeFileSync(tmp, JSON.stringify(state))
    fs.renameSync(tmp, statePath)
    return state
  }

  function rotateLog() {
    try {
      if (fs.statSync(logPath).size <= EVENT_LOG_ROTATE_BYTES) return
      const lines = fs.readFileSync(logPath, "utf8").split("\n").filter((l) => l !== "")
      if (lines.length <= EVENT_LOG_KEEP_LINES) return
      const tmp = `${logPath}.${process.pid}.tmp`
      fs.writeFileSync(tmp, lines.slice(-EVENT_LOG_KEEP_LINES).join("\n") + "\n")
      fs.renameSync(tmp, logPath)
    } catch (e) {
      /* best-effort, same as hook_handler._rotate_event_log */
    }
  }

  function appendEvent(event, detail = {}) {
    const entry = { event, timestamp: now() }
    if (detail.toolName != null) entry.tool_name = detail.toolName
    if (detail.toolInput != null) entry.tool_input = detail.toolInput
    if (detail.error != null) entry.error = detail.error
    fs.mkdirSync(dir, { recursive: true })
    fs.appendFileSync(logPath, JSON.stringify(entry) + "\n")
    rotateLog()
  }

  function publish(event, detail = {}) {
    writeState(event, detail)
    appendEvent(event, detail)
  }

  return { dir, statePath, logPath, readPrevious, writeState, appendEvent, publish }
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

  return {
    handleBusEvent,
    onUserMessage,
    onToolBefore,
    onToolAfter,
    writer,
    rootSessionIds,
    childSessionIds,
  }
}

/** True when the environment identifies an overcode-launched agent. */
function isOvercodeSession(env) {
  return Boolean(env && env.OVERCODE_SESSION_NAME && env.OVERCODE_TMUX_SESSION)
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
  if (!isOvercodeSession(process.env)) return {}

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

  return {
    event: guard((input) => telemetry.handleBusEvent(input && input.event)),
    "chat.message": guard((input, output) => {
      const message = (output && output.message) || {}
      if (message.role && message.role !== "user") return
      telemetry.onUserMessage((input && input.sessionID) || message.sessionID, message.id)
    }),
    "tool.execute.before": guard((input, output) => telemetry.onToolBefore(input, output)),
    "tool.execute.after": guard((input, output) => telemetry.onToolAfter(input, output)),
  }
}

// Test seam only — a property, never a second export (see above).
OvercodeTelemetryPlugin.internals = {
  canonicalToolName,
  createTelemetry,
  createWriter,
  isOvercodeSession,
  resolveStateDir,
}
