import json
import os

here = os.path.dirname(os.path.abspath(__file__))
events = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
          "PostToolUseFailure", "Notification", "Stop", "StopFailure", "SubagentStart",
          "SubagentStop", "PreCompact", "PostCompact", "PermissionRequest", "PermissionDenied",
          "TaskCreated", "TaskCompleted", "TeammateIdle", "WorktreeCreate", "WorktreeRemove",
          "InstructionsLoaded", "ConfigChange", "CwdChanged", "Elicitation",
          "ElicitationResult", "UserPromptExpansion", "PostToolBatch", "Setup"]
hooks = {}
for e in events:
    hooks[e] = [{"hooks": [{"type": "command",
                            "command": f"python3 {here}/hooklog.py {e}"}]}]
    if e in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied"):
        hooks[e][0]["matcher"] = "*"
json.dump({"hooks": hooks}, open(os.path.join(here, "settings.json"), "w"))
print(len(events))
