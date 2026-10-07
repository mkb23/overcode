# Overcode 0.6.0 Release Notes

- **The daemon is the engine.** It alone computes status, stats, git, burn and episodes, and pushes changes to the TUI over a local socket; hook events show on screen within half a second. Unwatched, it drops to near-zero CPU but keeps recording. See [the design](design/engine-0.6.md).
- **Trustworthy colours (#507).** Blips under 20 s merge into the surrounding episode, background work shows yellow, answered permission prompts go green, and the bell rings once per stall.
- **Simpler.** The web dashboard, Cloudflare relay and standalone TUI are gone: `overcode` opens the tmux split (#523), and the API server remains for sisters. About 7,000 fewer lines of source.
- **Fleet-scale CPU.** codex, grok, opencode and hermes stats read only what changed (#517, #524–#527).
- **Energy in watts (#521, #522).** Estimated from tokens on a guessed GPU replica rather than dollars, configurable under `energy:` in `config.yaml`.
- **opencode first-class.** A parity audit ([doc](design/opencode-parity.md)), colour-level replay tests, and a plugin-less agent is now read from its pane instead of showing red.
