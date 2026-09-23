/**
 * overcode-telemetry-core — shared writer/reducer for overcode's opencode
 * telemetry plugins.
 *
 * OVERCODE MANAGED FILE. Overcode copies this into
 * `<project>/.opencode/plugins/overcode-telemetry-v2/` alongside the v2
 * plugin's tui.js entry. Delete freely — overcode recreates it.
 * OVERCODE-PLUGIN-MARKER: overcode-telemetry-v2
 *
 * A plain ESM module holding the telemetry writer/reducer helpers, so
 * the v2 plugin's tui.js can import them — v2's plugin shape forbids
 * arbitrary exports, so the helpers cannot live in the plugin file
 * itself.
 *
 * Verified live against opencode2 v0.0.0-dev-19272 (Sep 15 2026):
 *
 *  * `handleBusEvent` normalizes three payload shapes: v1's
 *    `{type, properties:{...}}`, flat payloads (the opencode2 `event` SQLite
 *    table), and the SSE `/event` envelopes the TUI actually receives —
 *    `{id, created, type, location, data:{...}}`.
 *  * The v2 turn vocabulary (from the SSE stream): `session.created`,
 *    `session.inbox.enqueued` (user prompt), `session.execution.started`,
 *    `session.tool.input.started` (tool name) / `session.tool.called` /
 *    `session.tool.success` (→ PostToolUse) / `session.tool.failed`
 *    (→ PostToolUseFailure — a failure is not a success event),
 *    `session.execution.succeeded` / `.interrupted` (and `.failed`,
 *    defensively). `session.idle` / `message.updated` from v1 never fire on
 *    the v2 stream but stay handled for shape tolerance.
 *  * Child sessions (live-captured Sep 15 2026): the `subagent` tool
 *    spawns them; their `session.created` carries `parentID` FLAT in the
 *    payload, and the child then runs a full turn of its own
 *    (`session.inbox.enqueued` … `session.execution.succeeded`) on the
 *    same bus. Children are remembered by id and rejected by `owns()`
 *    even on its adopt path — without that, the child's turn publishes
 *    UserPromptSubmit/Stop for the parent mid-tool-call.
 *  * v2 permissions arrive as legacy-typed `permission.asked` /
 *    `permission.replied` with a flat `action` string ("shell"), the command
 *    in `resources`, and the tool call id in `source.id`. The v1 string
 *    `permission` field, the nested `{permission: {action}}` form and the
 *    `permission.v2.*` type aliases are all still accepted.
 *  * v2 names its shell tool "shell", which canonicalizes to Claude's "Bash".
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

// How many in-flight tool calls to remember (call id -> {toolName,
// toolInput}). Calls are cleared on success/failed, so this only bounds a
// stream that never completes its calls; it is deliberately NOT the
// session-id bound — the two populations are unrelated.
const MAX_ACTIVE_TOOL_CALLS = 20

// How many user message ids to remember for UserPromptSubmit de-duplication.
// The user-prompt events re-fire for the same message, which would otherwise
// overwrite Stop and pin the agent green.
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
  shell: "Bash",
  skill: "Skill",
  subagent: "Task", // v2's task tool is named "subagent" (live-captured Sep 15 2026)
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

function computeForeground(event, toolName, toolInput) {
  if (event !== "PreToolUse" || !toolName) return null
  const fg = { kind: "tool", tool: toolName }
  if (toolName === "Bash" && toolInput && typeof toolInput === "object") {
    const blockedOn = classifyBlockedOn(toolInput.command)
    if (blockedOn) fg.blocked_on = blockedOn
  }
  return fg
}

// The maximum length of a failure reason written to the state/events
// files, so a huge error payload cannot bloat the hook files.
const MAX_ERROR_REASON_CHARS = 200

// What each published event means for the agent, mirrored from
// hook_status_detector._HOOK_STATUS_MAP. The reducer keeps the last one so
// a repeated turn-end signal settles once and a StopFailure survives the
// idle that follows it (#474; verified on v1, ported here by shape).
const EVENT_STATUS = {
  UserPromptSubmit: "running",
  PreToolUse: "running",
  PostToolUse: "running",
  PostToolUseFailure: "running",
  PermissionRequest: "waiting_approval",
  Stop: "waiting_user",
  StopFailure: "error",
  SessionEnd: "terminated",
}

// session.error names that are the user's own doing, not a failure (v1's
// double-Escape publishes {name: "MessageAbortedError"} before going idle).
const ABORT_ERROR_NAMES = ["MessageAbortedError"]

/**
 * A bounded reason string from a failure event, or null.
 *
 * The exact v2 failure vocabulary is not pinned live, so the common
 * spellings are tried defensively: `props.error` (a string, an
 * `{message}` object, or any other object stringified), the nested
 * `props.data.error`, or `props.reason`. Capped so a huge error payload
 * cannot bloat the hook files.
 */
function failureReason(props) {
  if (!props || typeof props !== "object") return null
  let raw
  if (props.error != null) raw = props.error
  else if (props.data && props.data.error != null) raw = props.data.error
  else if (props.reason != null) raw = props.reason
  else return null
  let text
  if (typeof raw === "string") text = raw
  else if (typeof raw === "object" && typeof raw.message === "string") text = raw.message
  else if (typeof raw === "object") {
    try { text = JSON.stringify(raw) } catch (e) { return null }
  } else text = String(raw)
  if (!text) return null
  return text.length > MAX_ERROR_REASON_CHARS
    ? text.slice(0, MAX_ERROR_REASON_CHARS)
    : text
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
      const skill = detail.toolInput.skill || detail.toolInput.name || detail.toolInput.id
      if (skill && !loadedSkills.includes(skill)) loadedSkills = loadedSkills.concat([skill])
    }

    const state = { event, timestamp }
    if (detail.toolName != null) state.tool_name = detail.toolName
    if (detail.toolInput != null) state.tool_input = detail.toolInput
    if (detail.toolUseId != null) state.tool_use_id = detail.toolUseId
    // Optional failure reason (StopFailure). The Python detector's
    // `_read_hook_state` validates only `event` + `timestamp` and passes
    // the dict through, so an extra unknown field is tolerated.
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

function normalize(busEvent) {
  // Three payload shapes, all verified: v1 delivered {type, properties};
  // the opencode2 `event` SQLite table stores flat rows; the SSE /event
  // stream the TUI plugin consumes wraps payloads as {type, data}.
  // Accept all three — the reducer speaks only in terms of {type, props}.
  if (!busEvent || typeof busEvent !== "object") return null
  let props = busEvent
  if (busEvent.properties && typeof busEvent.properties === "object") {
    props = busEvent.properties
  } else if (busEvent.data && typeof busEvent.data === "object") {
    props = busEvent.data
  }
  return { type: busEvent.type, props }
}

/** The v1 permission field is a tool-name string; v2 uses a flat `action`. */
function permissionToolKey(props) {
  const raw = props.permission
  const name = typeof raw === "string" ? raw : (raw && raw.action) || props.action || ""
  return name === "shell" ? "bash" : name
}

function permissionToolInput(props) {
  if (props.metadata && typeof props.metadata === "object") return props.metadata
  // v2 carries the command in `resources` (live capture: ["echo hiperm2"]).
  if (Array.isArray(props.resources) && typeof props.resources[0] === "string") {
    return { command: props.resources[0] }
  }
  return undefined
}

/**
 * The event→overcode-event reducer, with the session-scoping and de-duplication
 * state an opencode process needs.
 */
function createTelemetry(env, options = {}) {
  const writer = options.writer || createWriter(env, options)
  const rootSessionIds = []
  // Child sessions spawned mid-turn (live-captured: the `subagent` tool).
  // Children are remembered by id so owns() can NEVER adopt one — a
  // child's inbox.enqueued arrives with the adopt flag set (resumed
  // conversations need it), and without this list the child's turn would
  // publish UserPromptSubmit/Stop for the parent mid-tool-call.
  const childSessionIds = []
  const seenUserMessages = []
  // permission request id → what it was gating. permission.replied carries
  // only `requestID`, so without this the event that clears waiting_approval
  // would have no tool name and the status badge would lose its label.
  const pendingPermissions = new Map()
  // tool call id → {toolName, toolInput}: v2's session.tool.success/failed
  // events carry no tool name, only the call id.
  const activeToolCalls = new Map()
  // Status of the last event this process published (see EVENT_STATUS).
  let current = null

  function publish(event, detail = {}) {
    current = EVENT_STATUS[event] || current
    writer.publish(event, detail)
  }

  function settle(sessionId) {
    if (current === "waiting_user" || current === "error" || current === "terminated") return
    publish("Stop", { agentSessionId: activeId(sessionId) })
  }

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
   * session.created), the first session id seen is adopted — otherwise a
   * resumed agent would publish nothing at all. Child sessions spawned by
   * the `subagent` tool are filtered out first, even on the adopt path, so
   * their turn events cannot mark the parent as finished or interrupt it
   * mid-tool-call.
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

  /** A root this agent owns, or a child of one. A sub-agent's permission
   *  dialog is answered in the parent's TUI (verified live on v1, #474),
   *  so a child's permission.asked/replied — and only those — are the
   *  parent's business. */
  function ownsOrChild(sessionId) {
    if (!sessionId) return false
    if (childSessionIds.includes(sessionId)) return true
    return owns(sessionId)
  }

  function activeId(sessionId) {
    return sessionId && rootSessionIds.includes(sessionId) ? sessionId : undefined
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
    const normalized = normalize(busEvent)
    if (!normalized) return
    const type = normalized.type
    const props = normalized.props
    const sessionId = props.sessionID

    switch (type) {
      case "session.created": {
        const info = props.info || {}
        // Live-captured child shape (v0.0.0-dev-19272, Sep 15 2026): the
        // `subagent` tool spawns a child whose session.created carries
        // `parentID` FLAT in the payload — {sessionID, parentID, slug,
        // version, ...}. Remember the child so owns() can never adopt it;
        // the v1-nested {info: {parentID}} form stays accepted
        // defensively. Only root sessions are this agent's conversation.
        const parentId =
          info.parentID || info.parent_id || props.parentID || props.parent_id
        if (parentId) {
          rememberChild(sessionId || info.id)
          return
        }
        rememberRoot(sessionId || info.id)
        return
      }

      case "session.inbox.enqueued": {
        // v2's user-prompt event: the inbox item is what the model answers.
        if (!props.item || props.item.type !== "user") return
        onUserMessage(sessionId, props.inboxID)
        return
      }

      case "message.updated": {
        const info = props.info || {}
        if (info.role !== "user") return
        onUserMessage(sessionId || info.sessionID, info.id)
        return
      }

      case "session.tool.input.started": {
        // v2 emits the tool NAME here (live-captured data: {sessionID,
        // assistantMessageID, id, name} with lowercase tool names like
        // "skill"/"subagent"); session.tool.called carries only
        // {id, input, executed} — remember the name by call id. Gate on
        // owns() so a foreign session sharing the bus cannot populate the
        // map; events that carry no session id (shape tolerated
        // defensively) still register, keeping the callID→name map
        // working for owned sessions.
        if (sessionId && !owns(sessionId)) return
        if (!props.id || !props.name) return
        const call = activeToolCalls.get(props.id) || {}
        call.toolName = canonicalToolName(props.name)
        activeToolCalls.set(props.id, call)
        while (activeToolCalls.size > MAX_ACTIVE_TOOL_CALLS) {
          activeToolCalls.delete(activeToolCalls.keys().next().value)
        }
        return
      }

      case "session.tool.called": {
        if (!owns(sessionId)) return
        const known = activeToolCalls.get(props.id) || {}
        const toolName = canonicalToolName(props.name) || known.toolName
        const toolInput =
          props.input && typeof props.input === "object" ? props.input : undefined
        if (props.id) {
          activeToolCalls.set(props.id, { toolName, toolInput })
          while (activeToolCalls.size > MAX_ACTIVE_TOOL_CALLS) {
            activeToolCalls.delete(activeToolCalls.keys().next().value)
          }
        }
        publish("PreToolUse", {
          toolName,
          toolInput,
          toolUseId: props.id,
          agentSessionId: activeId(sessionId),
        })
        return
      }

      case "session.tool.success":
      case "session.tool.failed": {
        if (!owns(sessionId)) return
        const call = activeToolCalls.get(props.id) || {}
        activeToolCalls.delete(props.id)
        // A failed call is not a success: PostToolUseFailure is the
        // event the status detector reads for failed tools
        // (hook_status_detector.py accepts both).
        const event = type === "session.tool.failed" ? "PostToolUseFailure" : "PostToolUse"
        publish(event, {
          toolName: call.toolName,
          toolInput: call.toolInput,
          toolUseId: props.id,
          agentSessionId: activeId(sessionId),
        })
        return
      }

      case "permission.asked":
      case "permission.v2.asked": {
        if (!ownsOrChild(sessionId)) return
        const toolName = canonicalToolName(permissionToolKey(props))
        const toolInput = permissionToolInput(props)
        const toolUseId =
          (props.tool && props.tool.callID) ||
          (props.source && props.source.type === "tool" ? props.source.id : undefined)
        if (props.id) {
          pendingPermissions.set(props.id, {
            toolName,
            toolInput,
            toolUseId,
          })
          while (pendingPermissions.size > MAX_SESSION_IDS) {
            pendingPermissions.delete(pendingPermissions.keys().next().value)
          }
        }
        publish("PermissionRequest", {
          toolName,
          toolInput,
          toolUseId,
          agentSessionId: activeId(sessionId),
        })
        return
      }

      case "permission.replied":
      case "permission.v2.replied": {
        if (!ownsOrChild(sessionId)) return
        // Either way the approval gate is gone and the agent is working again:
        // an allow resumes the tool call (PreToolUse), a reject hands the
        // refusal back to the model (PostToolUse). Both map to `running`, and
        // the turn-end event settles it a moment later.
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

      case "session.execution.succeeded":
      case "session.execution.interrupted": {
        // The turn ended — normally or by user interrupt; either way the
        // agent is back to waiting for input. Settles once, and never
        // over a StopFailure the user has not seen yet.
        if (!owns(sessionId)) return
        settle(sessionId)
        return
      }

      case "session.execution.failed": {
        // Not observed live (kept defensively): a failed turn should surface
        // as an error the same way v1's session.error did, carrying a
        // bounded reason when the event has one.
        if (sessionId && !owns(sessionId)) return
        publish("StopFailure", {
          agentSessionId: activeId(sessionId),
          error: failureReason(props),
        })
        return
      }

      case "session.idle": {
        // v1's turn-end event; never fires on the v2 stream but stays handled.
        if (!owns(sessionId)) return
        settle(sessionId)
        return
      }

      case "session.error": {
        // Shape unconfirmed — accept it whether or not it names a session,
        // because an error that ends the turn is worth surfacing either way.
        // v1's user interrupt arrives here too (MessageAbortedError): not a
        // failure, the idle after it is a Stop.
        if (sessionId && !owns(sessionId)) return
        const errName = props.error && typeof props.error === "object" ? props.error.name : undefined
        if (ABORT_ERROR_NAMES.includes(errName)) return
        publish("StopFailure", {
          agentSessionId: activeId(sessionId),
          error: failureReason(props),
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
  }
}

/** True when the environment identifies an overcode-launched agent. */
function isOvercodeSession(env) {
  return Boolean(env && env.OVERCODE_SESSION_NAME && env.OVERCODE_TMUX_SESSION)
}

export {
  BLOCKED_ON_PATTERNS,
  canonicalToolName,
  createTelemetry,
  createWriter,
  isOvercodeSession,
  normalize,
  resolveStateDir,
}
