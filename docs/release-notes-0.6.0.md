# Overcode 0.6.0 Release Notes

- **The daemon is the engine.** It alone computes status, stats, git, burn and episodes, and pushes changes to the TUI over a local socket; hook events show on screen within half a second. Unwatched, it drops to near-zero CPU but keeps recording. See [the design](design/engine-0.6.md).
- **Lights you can trust (#507).** ❔ while an agent starts, ⚪ for a child at rest, 🟡 for a parent waiting on its children; a new Result column (📭 ✅ ❌ ⌛) says how each child ended; `?` shows the legend. Blips under 20 s merge, and the bell rings once per real stall.
- **Billing codes and permanent history.** Every agent has a code (its name unless set; children inherit it), switchable from the TUI or `overcode rebill`; `overcode billing` reports spend by code from a ledger kept forever. Finished agents are packed into compressed day bundles, so 15K agents a quarter stay fast. Existing state migrates on first start.
- **Simpler.** The web dashboard, Cloudflare relay and standalone TUI are gone: `overcode` opens the tmux split (#523), and the API server remains for sisters. About 7,000 fewer lines of source.
- **Backends.** codex, grok, opencode and hermes stats read only what changed (#517, #524–#527); opencode is first-class; Claude gets its launch prompt on the command line, not as pasted text it won't act on.
- **Energy in watts (#521, #522).** Estimated from tokens on a guessed GPU replica rather than dollars, configurable under `energy:` in `config.yaml`.
