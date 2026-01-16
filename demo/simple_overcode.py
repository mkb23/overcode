#!/usr/bin/env python3
"""
Simple Overcode Demo - Supervise multiple Claude instances
"""

import subprocess
import time
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

class ClaudeSession:
    """Represents a supervised Claude session"""

    def __init__(self, name: str, prompt: str, output_dir: Path):
        self.name = name
        self.prompt = prompt
        self.output_dir = output_dir
        self.output_file = output_dir / f"{name}.log"
        self.metadata_file = output_dir / f"{name}.meta.json"
        self.process: Optional[subprocess.Popen] = None
        self.start_time = None
        self.pid = None

    def start(self):
        """Launch the Claude session"""
        print(f"[Overcode] Launching session '{self.name}'...")

        self.start_time = datetime.now()

        # Launch claude in non-interactive mode, writing output to file
        with open(self.output_file, 'w') as f:
            self.process = subprocess.Popen(
                ['claude', self.prompt],
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True
            )

        self.pid = self.process.pid

        # Save metadata
        self._save_metadata()

        print(f"[Overcode] Session '{self.name}' started (PID: {self.pid})")

    def _save_metadata(self):
        """Save session metadata"""
        metadata = {
            'name': self.name,
            'prompt': self.prompt,
            'pid': self.pid,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'output_file': str(self.output_file)
        }

        with open(self.metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

    def is_running(self) -> bool:
        """Check if the session is still running"""
        if self.process is None:
            return False
        return self.process.poll() is None

    def get_output(self, lines: int = 20) -> str:
        """Get the last N lines of output"""
        if not self.output_file.exists():
            return "[No output yet]"

        try:
            with open(self.output_file, 'r') as f:
                all_lines = f.readlines()
                recent_lines = all_lines[-lines:]
                return ''.join(recent_lines)
        except Exception as e:
            return f"[Error reading output: {e}]"

    def get_status(self) -> Dict:
        """Get current session status"""
        status = {
            'name': self.name,
            'running': self.is_running(),
            'pid': self.pid,
            'uptime': None
        }

        if self.start_time:
            uptime_seconds = (datetime.now() - self.start_time).total_seconds()
            status['uptime'] = f"{int(uptime_seconds)}s"

        return status

    def terminate(self):
        """Stop the session"""
        if self.process and self.is_running():
            print(f"[Overcode] Terminating session '{self.name}'...")
            self.process.terminate()
            self.process.wait(timeout=5)


class SimpleOvercode:
    """Simple Overcode supervisor for demo"""

    def __init__(self, output_dir: str = "overcode_output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.sessions: Dict[str, ClaudeSession] = {}

    def launch(self, name: str, prompt: str) -> ClaudeSession:
        """Launch a new supervised Claude session"""
        session = ClaudeSession(name, prompt, self.output_dir)
        session.start()
        self.sessions[name] = session
        return session

    def get_status(self) -> List[Dict]:
        """Get status of all sessions"""
        return [session.get_status() for session in self.sessions.values()]

    def get_output(self, name: str, lines: int = 20) -> str:
        """Get output from a specific session"""
        if name not in self.sessions:
            return f"[Session '{name}' not found]"
        return self.sessions[name].get_output(lines)

    def list_sessions(self) -> List[str]:
        """List all session names"""
        return list(self.sessions.keys())

    def terminate_all(self):
        """Terminate all sessions"""
        for session in self.sessions.values():
            session.terminate()

    def report(self) -> str:
        """Generate a status report"""
        lines = ["=" * 60]
        lines.append("OVERCODE SUPERVISOR STATUS REPORT")
        lines.append("=" * 60)
        lines.append(f"Active Sessions: {len(self.sessions)}")
        lines.append("")

        for session_name in self.sessions:
            session = self.sessions[session_name]
            status = session.get_status()

            lines.append(f"Session: {status['name']}")
            lines.append(f"  Status: {'🟢 Running' if status['running'] else '🔴 Stopped'}")
            lines.append(f"  PID: {status['pid']}")
            lines.append(f"  Uptime: {status['uptime']}")
            lines.append(f"  Recent Output (last 10 lines):")
            lines.append("  " + "-" * 55)

            output = session.get_output(lines=10)
            for line in output.split('\n'):
                if line.strip():
                    lines.append(f"  {line}")

            lines.append("")

        lines.append("=" * 60)
        return '\n'.join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python simple_overcode.py launch    - Launch demo sessions")
        print("  python simple_overcode.py status    - Check session status")
        print("  python simple_overcode.py output <session_name> - Get output from session")
        sys.exit(1)

    command = sys.argv[1]

    supervisor = SimpleOvercode()

    if command == "launch":
        print("\n[Overcode] Starting demo sessions...")
        print()

        # Launch two Claude instances
        supervisor.launch(
            "times-tables",
            "Generate times tables for 2, 5, and 9. For each number, show the multiplication table from 1 to 12. Format it nicely."
        )

        time.sleep(2)  # Stagger the launches

        supervisor.launch(
            "recipes",
            "Come up with 3 creative and unusual recipe ideas. For each recipe, give it a fun name and list the main ingredients."
        )

        print()
        print("[Overcode] Both sessions launched!")
        print("[Overcode] Sessions are running in the background.")
        print()
        print("You can check status with: python simple_overcode.py status")
        print()

    elif command == "status":
        # Read sessions from disk
        output_dir = Path("overcode_output")
        if not output_dir.exists():
            print("[Overcode] No active sessions found.")
            sys.exit(0)

        # Load session metadata
        for meta_file in output_dir.glob("*.meta.json"):
            with open(meta_file) as f:
                metadata = json.load(f)
                session = ClaudeSession(
                    metadata['name'],
                    metadata['prompt'],
                    output_dir
                )
                session.pid = metadata['pid']
                session.start_time = datetime.fromisoformat(metadata['start_time']) if metadata['start_time'] else None
                # Don't recreate process, just track it
                supervisor.sessions[metadata['name']] = session

        print(supervisor.report())

    elif command == "output" and len(sys.argv) >= 3:
        session_name = sys.argv[2]

        # Load session
        output_dir = Path("overcode_output")
        meta_file = output_dir / f"{session_name}.meta.json"

        if not meta_file.exists():
            print(f"[Overcode] Session '{session_name}' not found.")
            sys.exit(1)

        with open(meta_file) as f:
            metadata = json.load(f)

        session = ClaudeSession(
            metadata['name'],
            metadata['prompt'],
            output_dir
        )

        print(f"\n[Overcode] Output from '{session_name}':")
        print("=" * 60)
        print(session.get_output(lines=50))
        print("=" * 60)
