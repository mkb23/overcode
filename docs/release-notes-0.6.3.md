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

**Upgrading:** nothing to convert. Both new settings are off by default; to
keep Claude's transcripts, set `archive.transcripts.enabled: true`.
