# status-themes: status colour themes, questions as Approval, idle stages

## What changed

- **Status themes** (`status_theme.py`). Each light in `LIGHT_LEGEND` (now with a `key`
  and an emoji-free `ascii`) gets a glyph from a theme; the glyph decides the text
  colour. Presets `classic` (default, unchanged look), `calm` (input 🔵, approval 🔴),
  `colorblind` (blue/orange + shapes, justified in a comment), `shapes` (monochrome
  ● ◐ ▲ ■ ○ ·). `config.yaml status_theme: {preset, overrides}` (or a bare preset name);
  overrides take a glyph or its name; unknown preset/light/glyph warn and are skipped.
- Theme-aware: agent rows, subagent rows and SubAgents counts, the DTL badge colour, the
  timeline (bars, heartbeat mark, swarm strips), the `?` legend (active glyphs + a
  "Status colours: <preset>" line), the spin line, `overcode list`, `config show`.
  `get_status_symbol/emoji/color` read the active theme (one stat of config.yaml).
  `STATUS_SYMBOLS`/`STATUS_EMOJIS`/`STATUS_COLORS` stay as the classic tables.
- Palette: **Status colours** (Display) cycles presets, keeping changed lights (state
  shows `calm+` when lights are changed); **Status colours…** (Settings) dialog: preset
  row, one row per light and per idle badge, ←/→ steps, `r` resets, sample fleet, clash
  warning. Both save to config.yaml.
- **Questions count as Approval**: `rule_question` → orange / `waiting_approval` with
  the ❓ badge and activity "Question: waiting for your answer"; polling synthesis keeps
  ❓ for that activity; Claude's pane-scraped question menu ("Enter to select",
  `StatusPatterns.question_patterns`, Phase 4b) is an approval too. Other backends have
  no question menu in their corpus (`question_patterns=[]`).
- **Idle stages** (`idle_stages.py`, Mike's addition): the engine publishes
  `idle_since` for a plain idle (waiting_user + `awaiting_input` lead badge); views
  turn 📥 into a dim 😴 after `idle.drowsy_after` (default 1h); after
  `idle.sleep_after` (default never) the daemon sleeps a root agent with
  `asleep_auto: true` and wakes it on green or approval; `z`/CLI/web sleeps and wakes
  clear `asleep_auto`. TUI follows an engine sleep at once with a toast; daemon log
  line on sleep and wake. Agent visibility dialog: "Idle: drowsy after"
  (10m/30m/1h/2h/never) and "Idle: asleep after" (never/6h/1d/3d/7d). Durations take
  s/m/h/d.

## Decisions I made

- Emoji-free mode keeps each *light's* letter (`[W]` needs input, `[A]` approval…) in
  the theme's colour, rather than an ASCII per glyph: a glyph's letter would lie once
  a theme moves it to another light (calm's 🔴 is approval). The glyph table has
  colour + name instead of an ASCII.
- The theme lives only in config.yaml (not tui_preferences): `overcode list` must read
  it, and one source avoids two disagreeing.
- No key for the cycler: free plain keys (g, v, D, N, O, P, Q) have no mnemonic and
  neighbouring branches may claim them.
- The web API and the supervisor's prompt keep classic glyphs (an API contract; the
  prompt talks of RED/ORANGE).
- Swarm idle rows now use the at-rest light's colour (grey85, was grey70); idle swarm
  strips keep grey50.
- Idle badges themed: classic/calm/colorblind 📥/😴, shapes ◇/z; emoji-free "In"/"zz".
- Auto-wake also on `waiting_approval` (an asleep agent asking would be hidden).
- The published `status_since` is now this tick's staged value (it lagged one tick on
  a change); needed so a fresh idle isn't dated by the previous status.
- Agent visibility dialog order follows Mike's lists (drowsy 10m…never, sleep never…7d).

## Side effects worth knowing

- A question open now blocks a pre-0.6 rename without force and defers auto-naming, as
  a permission does (`RENAME_BUSY_STATUSES`, namer).
- Theme changes repaint at once from the TUI; a hand edit of config.yaml shows as rows
  next redraw (rows tick every 250 ms for clock cells; others on their next input).
- Unit tests that write config.yaml must use their own OVERCODE_DIR (it is shared by
  the run); the new test modules do (autouse `own_config`).

## Not done

- Notifications carry no glyphs and there is no tmux status line, so nothing to theme.
- The SUBA column description text (registry) still names classic glyphs.
