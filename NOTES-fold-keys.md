# fold-keys: ← / → / X as one tree view

Branch `fold-keys` (base 484b000). Tests: `tests/unit/test_fold_keys_tui.py`.

## What happened before (reproduced, 13 of 24 tests failed on 484b000)

"It works some of the time": the keys checked one kind of descendant at a
time, in a fixed order, so the result depended on what else was open.

1. ← on a parent with delegated work open closed only the delegated work;
   its children stayed. A second ← folded them. (Default state in tree
   sort is children open, delegated work closed, so after one → both were
   open and ← looked broken.)
2. → on a parent with folded children unfolded only the children; a second
   → opened the delegated work, a third entered its rows.
3. → on an open parent with no delegated work said "X has not delegated any
   work" instead of moving onto its first child (a real child or a 🌙
   background session).
4. → on a leaf said "not delegated" (not "nothing to expand").
5. Children hidden by the visibility settings (done, killed, sleeping)
   still made a parent foldable: ← on a parent whose only child was a hidden
   done agent "folded" it (▶ marker, persisted) with nothing visible
   changing, where a leaf root should do nothing.
6. X counted such invisible children as open, so with everything visible
   closed it closed again instead of opening.
7. X closing folds while the highlight was on a grandchild clamped the
   highlight to whatever row had the same index (a different root), not
   the folded agent's nearest shown ancestor.
8. X left member detail lines open (`swarm_details`).
9. → on a member whose detail line was already open did nothing, silently.
10. Done, killed and sleeping children shown via the visibility dialog fold
    with their parent (they are in `self.sessions`, nested by the tree
    sort): this already worked once case 1 is out of the way.
11. The ▶ fold marker showed in flat sorts for a parent folded in tree
    sort, though flat sorts never hide children.

Not reproduced: a fold undoing itself on refresh (`collapsed_parents`
survives `_apply_sessions` and a prefs reload); a fold landing on the
wrong parent.

## The model now

- A row is a node. An agent's children are its shown overcode children and
  🌙 background sessions (tree sort only; "shown" means the visibility
  settings would show them) and its delegated work (👥 subagents,
  workflows: virtual rows). A virtual member's child is its detail line.
- → on a node with anything closed opens all of it; on a fully open node
  moves onto its first child row (virtual rows come first, as drawn); on a
  leaf: "Nothing to expand".
- ← on a node with anything open closes all of it; else moves to the
  parent (a real child or 🌙 row → its agent, in tree sort; a virtual row →
  its owner); at a root, nothing.
- X: anything open anywhere (shown folds, delegated work, detail lines) →
  close everything; else open everything (folds first, so agents inside
  them get their delegated work opened too).
- A hidden chosen agent: its nearest shown ancestor stands in (then the
  old neighbour rule); it gets the highlight back when shown.
- Flat sorts: children are grouped under their parent but never hidden, so
  only delegated work opens and closes; ← on a child does not move to its
  parent (as before).
- `toggle_collapse_children` (palette "Fold / unfold row", no default key)
  stays: it toggles the focused node, all kinds, and on a leaf child its
  parent, as before.
- Folds persist in `collapsed_parents` as before; delegated work keeps its
  rule + per-agent override persistence.

## Decisions

- → / ← and Fold / unfold row no longer pop "Expanded/Collapsed children
  of X" notices (the ▶ marker and the rows show it); X still says
  "Expanded all" / "Collapsed all".
- A node that is half open (the tree-sort default: children shown,
  delegated work closed) counts as closed for → (it opens the rest) and
  open for ← (it closes everything).
- X closing also drops per-agent "open" overrides of agents folded out of
  sight, so nothing reopens by itself when they are unfolded (the `f` rule
  still applies).
- Palette/help titles: "Expand row / step in", "Collapse row / to parent",
  "Expand / collapse all", "Fold / unfold row". No action renamed, so
  nothing goes in `REMOVED_ACTIONS`.
- Killed children: they live in `self.sessions` with status
  "terminated", so they nest and fold like the rest (no change needed).

## Changed

- `tui_actions/swarm.py`: the tree model (`_node_state`, `_set_node`,
  `_first_child`, `_foldable_parents`), → / ← / X rewritten on it.
- `tui_actions/view.py`: `toggle_collapse_children` toggles a node;
  `_fold_children(..., notify=)`.
- `tui.py`: nearest shown ancestor stands in for a folded-away agent; ▶
  only in tree sort and only where a fold hides something shown; binding
  descriptions.
- `command_palette.py`, docs (tui-guide Folding section and keys table,
  advanced-features, the 0.6.1 design doc's Keys note).
- Tests: `tests/unit/test_fold_keys_tui.py` (31 pilot tests); the
  mock tests of the old action in `test_tui_actions_view.py` now cover
  `_fold_children`.
