"""
LLM API client for agent summarization.

Supports OpenAI Chat Completions and Anthropic Messages API backends.

Configuration via ~/.overcode/config.yaml (preferred) or environment variables (fallback):

Config file format (OpenAI, default):
    summarizer:
      api_type: openai
      api_url: https://api.openai.com/v1/chat/completions
      model: gpt-4o-mini
      api_key_var: OPENAI_API_KEY

Config file format (Anthropic):
    summarizer:
      api_type: anthropic
      api_url: https://api.anthropic.com/v1/messages
      model: claude-haiku-4-5-20250929
      api_key_var: ANTHROPIC_API_KEY

Environment variable fallbacks:
    OVERCODE_SUMMARIZER_API_TYPE
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

# Short summary prompt - focuses on IMMEDIATE ACTION (verb-first, what's happening this second)
SUMMARIZE_PROMPT_SHORT = """What is the agent doing RIGHT NOW? Answer with the immediate action only.

## Terminal (last {lines} lines):
{pane_content}

## Previous:
{previous_summary}

FORMAT: Start with a verb. Examples:
- "reading src/auth.py"
- "running pytest -v"
- "waiting for approval"
- "writing migration file"
- "editing line 45"

RULES:
- Verb first, always (reading/writing/running/waiting/editing/fixing)
- Name the specific file or command if visible
- Max 40 chars
- If unchanged: UNCHANGED"""

# Context summary prompt - focuses on THE TASK (noun-first, the feature/bug/goal)
SUMMARIZE_PROMPT_CONTEXT = """What TASK or FEATURE is being worked on? Not the current action - the goal.

## Terminal (last {lines} lines):
{pane_content}

## Previous:
{previous_summary}

FORMAT: Describe the task/feature/bug. Examples:
- "JWT auth migration"
- "user search pagination"
- "fix: race condition in queue"
- "PR #42 review comments"
- "new settings dark mode"

RULES:
- Noun/task first (not a verb like "implementing")
- Include ticket/PR numbers if mentioned
- Focus on WHAT is being built/fixed, not HOW
- Max 60 chars
- If unchanged: UNCHANGED"""


class SummarizerClient:
    """Client for LLM API to generate agent summaries.

    Supports OpenAI and Anthropic backends via config file or env vars.
    """

    api_type: str = "openai"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the client.

        Args:
            api_key: API key. If None, reads from config file or env var.
        """
        config = get_summarizer_config()
        self.api_url = config["api_url"]
        self.model = config["model"]
        self.api_key = api_key or config["api_key"]
        self.api_type = config.get("api_type", "openai")
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
        mode: str = "short",
    ) -> Optional[str]:
        """Get summary from GPT-4o-mini.

        Args:
            pane_content: Terminal pane content to summarize
            previous_summary: Previous summary for anti-oscillation
            current_status: Current agent status (running, waiting_user, etc.)
            lines: Number of lines being summarized (for prompt context)
            max_tokens: Maximum tokens in response
            mode: "short" for current activity, "context" for wider context

        Returns:
            New summary text, "UNCHANGED" if no update needed, or None on error
        """
        if not self.available:
            return None

        # Select prompt based on mode
        prompt_template = SUMMARIZE_PROMPT_CONTEXT if mode == "context" else SUMMARIZE_PROMPT_SHORT

        prompt = prompt_template.format(
            lines=lines,
            pane_content=pane_content,
            status=current_status,
            previous_summary=previous_summary or "(no previous summary)",
        )

        if self.api_type == "anthropic":
            return self._call_anthropic(prompt, max_tokens)
        else:
            return self._call_openai(prompt, max_tokens)

    def _call_anthropic(self, prompt: str, max_tokens: int) -> Optional[str]:
        """Call the Anthropic Messages API."""
        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        req = urllib.request.Request(
            self.api_url, data=payload, headers=headers, method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15.0) as response:
                if response.status == 200:
                    result = json.loads(response.read().decode("utf-8"))
                    content = result["content"][0]["text"]
                    return content.strip()
                else:
                    logger.warning(f"Summarizer API error: {response.status}")
                    return None
        except urllib.error.URLError as e:
            logger.warning(f"Summarizer API error: {e.reason}")
            return None
        except TimeoutError:
            logger.warning("Summarizer API timeout")
            return None
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Summarizer API error: {e}")
            return None

    def _call_openai(self, prompt: str, max_tokens: int) -> Optional[str]:
        """Call the OpenAI Chat Completions API."""
        payload = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.3,
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
        except (json.JSONDecodeError, KeyError, ValueError) as e:
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
