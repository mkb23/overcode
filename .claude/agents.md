# Agent Development Guidelines

Guidelines and gotchas for AI agents working on the overcode codebase.

## TUI Development

### CSS max-height Constraint

**Problem**: When adding new detail levels or increasing the amount of content shown in the expanded SessionSummary widget, the content may appear truncated from the bottom (showing old content like welcome banners instead of recent activity).

**Root cause**: The `SessionSummary.expanded` CSS class has a `max-height` constraint that limits how many lines can be displayed. If content exceeds this, the Textual renderer shows from the top and truncates the bottom.

**Location**: `src/overcode/tui.py` in the CSS string, look for:
```css
SessionSummary.expanded {
    height: auto;
    min-height: 2;
    max-height: 55;  /* Support up to 50 lines detail + header/instructions */
    ...
}
```

**Fix**: When adding new detail levels (e.g., increasing from 20 to 50 lines), update the `max-height` value to accommodate the new maximum plus a few extra lines for headers/standing instructions.

### Detail Level Constants

The detail levels for the `v` toggle are defined in `DETAIL_LEVELS` array:
```python
DETAIL_LEVELS = [5, 10, 20, 50]
```

When changing these values, also update:
1. The `max-height` CSS constraint (see above)
2. The pane content buffer in `update_status()` - `num_lines` parameter and the `[-50:]` slice
3. The docstring in `action_cycle_detail()`

### Pane Content Buffer

The pane content capture flow:
1. `get_pane_content(num_lines=60)` captures from tmux
2. `update_status()` stores `lines[-50:]` in `self.pane_content`
3. Render takes `pane_content[-lines_to_show:]`

Ensure the buffer sizes are >= the maximum detail level.

### Summary Line Alignment

When adding new fields to the session summary line (`SessionSummary.render()` in `tui.py`):

**Always use fixed-width formatting** to maintain alignment across all sessions.

### Pattern

```python
# GOOD: Fixed width with padding
content.append(f" {value:>6}", style=...)  # Right-align in 6 chars
content.append(f" {value:<8}", style=...)  # Left-align in 8 chars

# BAD: Variable width
content.append(f" {value}", style=...)  # Width varies with content
```

### Key Rules

1. **Determine maximum width** - What's the largest value this field could reasonably display?

2. **Use format specifiers** - `:>N` for right-align, `:<N` for left-align, where N is the fixed width

3. **Handle all states consistently**:
   - Normal value: `f" Δ{files:>2}"`
   - Zero value: Same width as normal
   - No data/placeholder: Same width (e.g., `"  Δ-"`)
   - Different detail levels (low/med/full) may need different widths

4. **Add a comment** documenting the width:
   ```python
   # ALIGNMENT: Use fixed widths - low/med: 4 chars "Δnn", full: 15 chars "Δnn +nnnn -nnn"
   ```

5. **Test visually** with multiple sessions having different values (0, small, large numbers)

### Example

```python
# Git diff stats with proper alignment
if self.git_diff_stats:
    files, ins, dels = self.git_diff_stats
    if self.summary_detail == "full":
        # Full: 15 chars total "Δnn +nnnn -nnn"
        content.append(f" Δ{files:>2}", style=f"bold magenta{bg}")
        content.append(f" +{ins:>4}", style=f"bold green{bg}")
        content.append(f" -{dels:>3}", style=f"bold red{bg}")
    else:
        # Compact: 4 chars "Δnn"
        content.append(f" Δ{files:>2}", style=...)
else:
    # Placeholder with matching width
    if self.summary_detail == "full":
        content.append("  Δ-  +   -  -", style=f"dim{bg}")
    else:
        content.append("  Δ-", style=f"dim{bg}")
```

## Git Guidelines

### Never Force Push to Shared Branches

**Never use `git push --force` or `git push --force-with-lease` unless explicitly asked by the user.**

Force pushing is destructive and can blow away other people's or agents' work. If you find yourself wanting to force push, it usually means something has gone wrong with your assumptions:

- Another agent may be working on the same branch
- The branch may have commits you don't know about
- Your local state may be out of sync

**What to do instead:**
1. Stop and reassess the situation
2. Use `git fetch` and `git log origin/branch` to see what's on remote
3. Ask the user how they want to proceed
4. Consider creating a new branch instead of modifying the existing one

**The only acceptable force push scenarios:**
- User explicitly requests it
- You created the branch yourself in this session AND no one else could have touched it
- Rebasing your own feature branch that you know is not shared

When in doubt, don't force push. Create a new branch instead.

## Other Guidelines

### Never Commit API Keys or Secrets

**Never hardcode API keys, tokens, or secrets in any file that gets committed to git.**

- Use environment variables: `${OPENAI_API_KEY}` or `os.environ.get("API_KEY")`
- Use `.env` files (which should be in `.gitignore`)
- If you accidentally commit a secret, alert the user immediately so they can revoke/rotate it

Even after removing a secret from a file, it remains in git history forever unless the history is rewritten.

(Add more guidelines here as patterns emerge)
