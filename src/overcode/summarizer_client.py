"""
OpenAI API client for agent summarization.

Uses GPT-4o-mini for cost-effective, high-frequency summaries.

Configuration via ~/.overcode/config.yaml (preferred) or environment variables (fallback):

Config file format:
    summarizer:
      api_url: https://api.openai.com/v1/chat/completions
      model: gpt-4o-mini
      api_key_var: OPENAI_API_KEY

Environment variable fallbacks:
    OVERCODE_SUMMARIZER_API_URL
    OVERCODE_SUMMARIZER_MODEL
    OVERCODE_SUMMARIZER_API_KEY_VAR
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from .config import get_summarizer_config

logger = logging.getLogger(__name__)

# Anti-oscillation prompt template
SUMMARIZE_PROMPT = """Summarize a Claude Code agent's terminal output.

## Terminal Content (last {lines} lines):
{pane_content}

## Status: {status}

## Previous Summary:
{previous_summary}

## Instructions:
Write a terse 1-sentence summary. Be direct and action-focused.

Style guide:
- NO "The agent is/has..." or "Claude is..." phrasing
- Start with lowercase verb or noun: "implementing...", "reading...", "tests passing"
- Use shorthand: "fixed bug, running tests" not "The agent has fixed the bug and is now running tests"
- Examples: "adding auth middleware, 3 files modified" / "waiting for user approval on delete operation" / "tests green, pushing to remote"

If nothing meaningful changed from previous summary, respond exactly: UNCHANGED

Max 60 chars when possible. Focus on: current action, what's being built/fixed, blockers if halted."""


class SummarizerClient:
    """Client for OpenAI-compatible API to generate agent summaries.

    Supports custom API endpoints for corporate gateways via config file or env vars.
    """

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: API key. If None, reads from config file or env var.
        """
        config = get_summarizer_config()
        self.api_url = config["api_url"]
        self.model = config["model"]
        self.api_key = api_key or config["api_key"]
        self._available = bool(self.api_key)

    @property
    def available(self) -> bool:
        """Check if the client is available (API key present)."""
        return self._available

    def summarize(
        self,
        pane_content: str,
        previous_summary: str,
        current_status: str,
        lines: int = 200,
        max_tokens: int = 150,
    ) -> Optional[str]:
        """Get summary from GPT-4o-mini.

        Args:
            pane_content: Terminal pane content to summarize
            previous_summary: Previous summary for anti-oscillation
            current_status: Current agent status (running, waiting_user, etc.)
            lines: Number of lines being summarized (for prompt context)
            max_tokens: Maximum tokens in response

        Returns:
            New summary text, "UNCHANGED" if no update needed, or None on error
        """
        if not self.available:
            return None

        prompt = SUMMARIZE_PROMPT.format(
            lines=lines,
            pane_content=pane_content,
            status=current_status,
            previous_summary=previous_summary or "(no previous summary)",
        )

        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.3,  # Low temperature for consistent summaries
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            self.api_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15.0) as response:
                if response.status == 200:
                    result = json.loads(response.read().decode("utf-8"))
                    content = result["choices"][0]["message"]["content"]
                    return content.strip()
                else:
                    logger.warning(
                        f"Summarizer API error: {response.status}"
                    )
                    return None

        except urllib.error.URLError as e:
            logger.warning(f"Summarizer API error: {e.reason}")
            return None
        except TimeoutError:
            logger.warning("Summarizer API timeout")
            return None
        except Exception as e:
            logger.warning(f"Summarizer API error: {e}")
            return None

    def close(self) -> None:
        """Clean up resources (no-op for urllib)."""
        pass

    @staticmethod
    def is_available() -> bool:
        """Check if API key is available (from config or environment)."""
        config = get_summarizer_config()
        return bool(config["api_key"])
