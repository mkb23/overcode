/**
 * overcode-spy — records EVERY plugin hook invocation opencode makes, verbatim,
 * to a JSONL file. Investigation aid for overcode#474; never shipped.
 *
 * The log path is baked in at copy time (see capture.py) because the opencode
 * process env is built by overcode's launcher, not by us.
 */
import fs from "node:fs"

const LOG_PATH = "__SPY_LOG_PATH__"

// Very high-volume stream events: keep only the routing fields.
const COMPACT_TYPES = new Set(["message.part.updated", "message.part.delta"])

function write(record) {
  try {
    fs.appendFileSync(LOG_PATH, JSON.stringify(record) + "\n")
  } catch (e) {
    /* never take opencode down */
  }
}

function now() {
  return Date.now() / 1000
}

function trimOutput(output) {
  if (!output || typeof output !== "object") return output
  const copy = { ...output }
  if (typeof copy.output === "string" && copy.output.length > 300) {
    copy.output = copy.output.slice(0, 300) + `…[${copy.output.length} chars]`
  }
  return copy
}

function compactEvent(event) {
  if (!event || typeof event !== "object") return event
  const type = event.type
  const props = event.properties || {}
  if (COMPACT_TYPES.has(type)) {
    const part = props.part || {}
    return {
      type,
      properties: {
        sessionID: props.sessionID || part.sessionID,
        messageID: props.messageID || part.messageID,
        partID: part.id,
        partType: part.type,
        ...(part.type === "tool" ? { tool: part.tool, callID: part.callID, status: part.state && part.state.status } : {}),
        ...(props.field ? { field: props.field } : {}),
      },
    }
  }
  return event
}

export const OvercodeSpy = async (ctx) => {
  write({ t: now(), hook: "__load__", input: { directory: ctx && ctx.directory, worktree: ctx && ctx.worktree } })
  const log = (hook) => async (input, output) => {
    write({ t: now(), hook, input, output })
  }
  return {
    event: async (input) => {
      write({ t: now(), hook: "event", event: compactEvent(input && input.event) })
    },
    "chat.message": log("chat.message"),
    "chat.params": async (input, output) => {
      write({
        t: now(),
        hook: "chat.params",
        input: { sessionID: input.sessionID, agent: input.agent, messageID: input.message && input.message.id },
      })
    },
    "permission.ask": log("permission.ask"),
    "command.execute.before": log("command.execute.before"),
    "tool.execute.before": log("tool.execute.before"),
    "tool.execute.after": async (input, output) => {
      write({ t: now(), hook: "tool.execute.after", input, output: trimOutput(output) })
    },
    "experimental.session.compacting": log("experimental.session.compacting"),
    "experimental.compaction.autocontinue": async (input, output) => {
      write({
        t: now(),
        hook: "experimental.compaction.autocontinue",
        input: { sessionID: input.sessionID, agent: input.agent, overflow: input.overflow, messageID: input.message && input.message.id },
        output,
      })
    },
    "experimental.text.complete": async (input) => {
      write({ t: now(), hook: "experimental.text.complete", input })
    },
    "shell.env": async (input) => {
      write({ t: now(), hook: "shell.env", input })
    },
  }
}
