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

**Upgrading:** nothing to convert. The two new settings (transcript archive,
prefer SendMessage) are off by default; to keep Claude's transcripts, set
`archive.transcripts.enabled: true`. A saved "show" for finished subagents
stays as it was: pick **recent** in the `d` dialog to switch.
