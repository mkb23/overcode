# overcode 0.6.3

- **Transcript archive.** Claude Code deletes its transcripts after 30 days;
  with `archive.transcripts.enabled: true` (or "Transcript archive" in the
  palette) overcode keeps a byte-exact, compressed copy of every one under
  `~/.overcode/transcripts`, tagged with repo, agent and billing code.
  `overcode archive find` / `cat` / `status` / `import` / `sweep` read and
  fill it. Off by default: nothing is captured until you turn it on.
- **Claude agents can be pointed at SendMessage.** With
  `send.prefer_claude_messaging: true` (or "Prefer SendMessage" in the
  palette), `overcode send` from one Claude Code agent to another is refused,
  unless `--force`, and tells it to use Claude's own SendMessage tool.
  Gestures and `--no-enter` keys still go through (#553).
- **`!` opens a new shell.** Each press launches a shell row in your home
  directory, `shell`, then `shell-2`, `shell-3`, … and focuses it.
- **Finished subagents count "recent" by default.** ✅ in the SubAgents
  cell counts what finished in the last 6 hours
  (`observability.recent_finished_hours`); ❌ still counts every failure,
  and the hover gives the whole number. **all** in the `d` dialog brings
  back the lifetime count.
- **Hover popups hold still.** A resting mouse no longer flickers them, and
  a tall one no longer vanishes in the short top pane.
- **Background shells told apart (#515).** 🏭 batch, 🔭 watcher and 📻 service
  replace 🔌; a watcher still polling an agent or job that ended shows 🧟.
  `overcode shells [--kill]` lists or stops them; `overcode jobs wait` waits
  on a job without ever outliving it. A `'` in a job's command no longer
  stops it running.
- **`overcode release <child>` (#520)** frees a child from its parent: it
  becomes a top-level agent with no report due, and what it spent so far
  stays on the parent's SUB$.
- **Workflow progress (#498).** A WF cell shows a running Claude Workflow's
  phase and agents done, e.g. `🧩 review · 2/3 · 7/12✅`; the hover splits it
  by phase.
- **`--effort` on launch and restart (#537)**, mapped to each backend's flag.
  opencode children no longer ask before `overcode report` (#531).
- **Tags from the TUI (#538).** `#` adds a tag to the highlighted agent,
  "Remove tag…" in the palette takes one off, and `S` sorts by tag.

**Upgrading:** nothing to convert. The two new settings (transcript archive,
prefer SendMessage) are off by default; to keep Claude's transcripts, set
`archive.transcripts.enabled: true`. A saved "show" for finished subagents
stays as it was: pick **recent** in the `d` dialog to switch.
