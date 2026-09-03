# Overcode 0.5.2 Release Notes

0.5.2 fixes mouse-wheel and PageUp scrolling for full-screen agent backends in the `overcode tmux` split: opencode draws on the alternate screen and scrolls its own transcript, so the bottom-pane bindings now hand those gestures to the program instead of forcing an empty, frozen tmux copy mode. Claude Code's inline scrollback behaves exactly as before, and a pane left stuck in copy mode is released by the next wheel tick.
