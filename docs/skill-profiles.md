# Skill Profiles

Agent CLIs treat installed skills as **always on**. Anything in `~/.claude/skills` is offered to every Claude Code session, and opencode reads the same folder (plus `~/.agents/skills`). So installing a skill for one project switches it on everywhere, in more than one tool.

Skill profiles turn that around:

- **The library** holds skills that are *installed but off*. No agent CLI looks there by itself.
- **A profile** is a named list of skills, such as `research` or `ios`.
- **Launching an agent with a profile** switches its skills on *for that agent only*. Your other personal skills are hidden for that session.
- **A folder can be pinned** to a profile, so new agents started there get it by default.

Nothing is written into your project folders or into `~/.claude`. When the agent ends, its skills go with it.

Supported backends: **Claude Code** and **opencode**. Other backends launch as normal and ignore the profile.

## The library

Library skills come from:

1. `~/.overcode/skills/`: copy skill folders (each with a `SKILL.md`) in here.
2. Any folders you add, such as skill or plugin repos checked out in your code root:

   ```bash
   overcode skills library add ~/Code/team-skills
   ```

overcode searches these folders for `SKILL.md` files. It skips hidden folders and `node_modules`, and goes no more than 6 levels deep. Repos stay where you keep them, and `git pull` updates every profile that uses them. If two folders have a skill with the same name, the first one wins; `overcode skills list` warns about it.

Don't add a folder that an agent CLI already reads, like `~/.claude/skills` or `~/.agents/skills`. Its skills would be always on regardless of profiles, and `overcode skills` warns if you do.

To move always-on skills into the library (it asks first):

```bash
overcode skills adopt shirka pdf
```

## Profiles

```bash
overcode skills profile set research shirka dataviz   # create or replace
overcode skills profile add research pdf
overcode skills profile remove research dataviz
overcode skills profile show research                 # what it adds and hides
overcode skills profile list
overcode skills profile delete research
```

A profile can name a skill by its frontmatter `name` or its folder name.

In the TUI, press **`W`** (or choose *Skill profiles…* in the `/` palette):

- The dialog lists every skill, with the ones agents use most at the top, and a checkbox for the selected profile.
- `space` switches the highlighted skill on or off, `← →` changes profile, `n` makes a new one, and `D D` deletes it.
- `p` pins the profile to the focused agent's folder.
- Always-on skills show which CLIs load them (`on: Claude+opencode`) and are marked *hidden* when the profile leaves them out.

## Which profile an agent gets

The first of these that applies:

1. `overcode launch --skills <profile>`, or the **Skills** field in the new-agent dialog (`n`). `--skills none` means no profile.
2. The parent agent's profile, for child agents (skip it with `--no-inherit`).
3. The profile pinned to the folder, or its nearest pinned parent folder:

   ```bash
   overcode skills pin research ~/Code/papers     # default: the current folder
   overcode skills unpin ~/Code/papers
   ```

   Pins live in `~/.overcode/config.yaml`, not in the folder.
4. `new_agent_defaults.skill_profile` in config, or the *Skill profile* row in the `G` dialog.

An agent without a profile launches exactly as before, with nothing hidden.

The profile is recorded on the agent and applied again on every restart, so edits to a profile take effect the next time an agent starts. The **PRF** column shows each agent's profile.

## What happens at launch

overcode builds `~/.overcode/skill-profiles/<profile>/`, a folder of links to the profile's library skills, and then:

| | Profile skills added | Other personal skills hidden |
|---|---|---|
| **Claude Code** | `--plugin-dir <profile folder>`. The skills appear as `<profile>:<skill>` | `skillOverrides: {"<skill>": "off"}` in the launch `--settings` |
| **opencode** | `OPENCODE_CONFIG_DIR=<profile folder>`, alongside `~/.config/opencode` | `"skill": {"<skill>": "deny"}` in `OPENCODE_PERMISSION`, which removes them from the list the model sees |

What counts as a *personal* skill for each CLI:

- **Claude Code:** `~/.claude/skills/<name>/`.
- **opencode:** everything under `~/.claude/skills`, `~/.agents/skills` and `~/.config/opencode/skill(s)`, including skills synced from claude.ai.

A project's own skills (its `.claude/skills` or `.opencode/skill`) and each CLI's built-in skills are never hidden.

A profile skill that the CLI already loads as a personal skill is left as it is: not linked a second time, and not hidden.

## Config

```yaml
# ~/.overcode/config.yaml
skills:
  library_paths:
    - ~/Code/team-skills
  profiles:
    research: [shirka, dataviz]
    ios: [swift-helper]
  folders:
    ~/Code/papers: research
new_agent_defaults:
  skill_profile: research
```

## Limits

- **Wrappers** (e.g. devcontainer): the profile folder links to paths on the host, so a container only sees them if those paths are mounted.
- **Codex, Grok, Hermes:** not supported yet. The launch goes ahead without the profile.
- **Remote launches** on a sister use that machine's own config.
