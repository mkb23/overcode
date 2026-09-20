# Overcode 0.5.2 Release Notes

0.5.2 fixes mouse-wheel and PageUp scrolling for full-screen agent backends in the `overcode tmux` split: opencode draws on the alternate screen and scrolls its own transcript, so the bottom-pane bindings now hand those gestures to the program instead of forcing an empty, frozen tmux copy mode. Claude Code's inline scrollback behaves exactly as before, and a pane left stuck in copy mode is released by the next wheel tick.

The fixes for #471, #464, #473, #469, #470, #468 and #465 were written up
here for a while but landed after this release was cut; they ship in 0.5.3
and are described in its release notes.
