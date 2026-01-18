"""
Real implementations of protocol interfaces.

These are production implementations that make actual subprocess calls
to tmux, perform real file I/O, etc.
"""

import json
import subprocess
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any


class RealTmux:
    """Production implementation of TmuxInterface using subprocess"""

    def capture_pane(self, session: str, window: int, lines: int = 100) -> Optional[str]:
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", f"{session}:{window}",
                 "-p", "-S", f"-{lines}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout
            return None
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return None

    def send_keys(self, session: str, window: int, keys: str, enter: bool = True) -> bool:
        try:
            # For Claude Code: text and Enter must be sent as SEPARATE commands
            # with a small delay, otherwise Claude Code doesn't process the Enter.
            target = f"{session}:{window}"

            # Send text first (if any)
            if keys:
                result = subprocess.run(
                    ["tmux", "send-keys", "-t", target, keys],
                    timeout=5, capture_output=True
                )
                if result.returncode != 0:
                    return False
                # Small delay for Claude Code to process text
                time.sleep(0.1)

            # Send Enter separately
            if enter:
                result = subprocess.run(
                    ["tmux", "send-keys", "-t", target, "Enter"],
                    timeout=5, capture_output=True
                )
                if result.returncode != 0:
                    return False

            return True
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return False

    def has_session(self, session: str) -> bool:
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", session],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return False

    def new_session(self, session: str) -> bool:
        try:
            result = subprocess.run(
                ["tmux", "new-session", "-d", "-s", session],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return False

    def new_window(self, session: str, name: str, command: Optional[List[str]] = None,
                   cwd: Optional[str] = None) -> Optional[int]:
        try:
            cmd = ["tmux", "new-window", "-t", session, "-n", name, "-P", "-F", "#{window_index}"]
            if cwd:
                cmd.extend(["-c", cwd])
            if command:
                cmd.append(" ".join(command))

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return int(result.stdout.strip())
            return None
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
            return None

    def kill_window(self, session: str, window: int) -> bool:
        try:
            result = subprocess.run(
                ["tmux", "kill-window", "-t", f"{session}:{window}"],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return False

    def kill_session(self, session: str) -> bool:
        try:
            result = subprocess.run(
                ["tmux", "kill-session", "-t", session],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return False

    def list_windows(self, session: str) -> List[Dict[str, Any]]:
        try:
            result = subprocess.run(
                ["tmux", "list-windows", "-t", session, "-F",
                 "#{window_index}:#{window_name}:#{window_active}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return []

            windows = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split(':')
                    if len(parts) >= 3:
                        windows.append({
                            'index': int(parts[0]),
                            'name': parts[1],
                            'active': parts[2] == '1'
                        })
            return windows
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return []

    def attach(self, session: str) -> None:
        os.execlp("tmux", "tmux", "attach-session", "-t", session)

    def select_window(self, session: str, window: int) -> bool:
        """Select a window in a tmux session (for external pane sync)."""
        try:
            result = subprocess.run(
                ["tmux", "select-window", "-t", f"{session}:{window}"],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return False


class RealFileSystem:
    """Production implementation of FileSystemInterface"""

    def read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            if not path.exists():
                return None
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def write_json(self, path: Path, data: Dict[str, Any]) -> bool:
        try:
            # Write atomically via temp file
            temp_path = path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)
            temp_path.replace(path)
            return True
        except IOError:
            return False

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir(self, path: Path, parents: bool = True) -> bool:
        try:
            path.mkdir(parents=parents, exist_ok=True)
            return True
        except IOError:
            return False

    def read_text(self, path: Path) -> Optional[str]:
        try:
            return path.read_text()
        except IOError:
            return None

    def write_text(self, path: Path, content: str) -> bool:
        try:
            path.write_text(content)
            return True
        except IOError:
            return False


class RealSubprocess:
    """Production implementation of SubprocessInterface"""

    def run(self, cmd: List[str], timeout: Optional[int] = None,
            capture_output: bool = True) -> Optional[Dict[str, Any]]:
        try:
            result = subprocess.run(
                cmd, timeout=timeout, capture_output=capture_output, text=True
            )
            return {
                'returncode': result.returncode,
                'stdout': result.stdout if capture_output else '',
                'stderr': result.stderr if capture_output else ''
            }
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return None

    def popen(self, cmd: List[str], cwd: Optional[str] = None) -> Any:
        try:
            return subprocess.Popen(cmd, cwd=cwd)
        except subprocess.SubprocessError:
            return None
