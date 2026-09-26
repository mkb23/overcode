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
from .summarizer_prompts import (
    DEFAULT_PROMPT_CONTEXT,
    DEFAULT_PROMPT_SHORT,
    load_prompt,
    render_prompt,
)

logger = logging.getLogger(__name__)

# The prompts live in summarizer_prompts (editable in ~/.overcode/prompts/,
# #491); these names are the built-in defaults, kept for importers.
SUMMARIZE_PROMPT_SHORT = DEFAULT_PROMPT_SHORT
SUMMARIZE_PROMPT_CONTEXT = DEFAULT_PROMPT_CONTEXT


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
        # Token usage from the most recent API call (for cost tracking)
        self.last_input_tokens: int = 0
        self.last_output_tokens: int = 0

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
        prompt_template: Optional[str] = None,
    ) -> Optional[str]:
        """Get a summary from the configured model.

        Args:
            pane_content: Terminal pane content to summarize
            previous_summary: Previous summary for anti-oscillation
            current_status: Current agent status (running, waiting_user, etc.)
            lines: Number of lines being summarized (for prompt context)
            max_tokens: Maximum tokens in response
            mode: "short" for current activity, "context" for wider context
            prompt_template: Use this template instead of the saved/default
                prompt for ``mode`` (the prompt lab's unsaved draft)

        Returns:
            New summary text, "UNCHANGED" if no update needed, or None on error
        """
        if not self.available:
            return None

        if prompt_template is None:
            prompt_template = load_prompt("context" if mode == "context" else "short")

        prompt = render_prompt(
            prompt_template,
            lines=lines,
            pane_content=pane_content,
            status=current_status,
            previous_summary=previous_summary,
        )

        if self.api_type == "anthropic":
            return self._call_anthropic(prompt, max_tokens)
        else:
            return self._call_openai(prompt, max_tokens)

    def _call_anthropic(self, prompt: str, max_tokens: int) -> Optional[str]:
        """Call the Anthropic Messages API."""
        self.last_input_tokens = 0
        self.last_output_tokens = 0

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
                    usage = result.get("usage", {})
                    self.last_input_tokens = usage.get("input_tokens", 0)
                    self.last_output_tokens = usage.get("output_tokens", 0)
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
        self.last_input_tokens = 0
        self.last_output_tokens = 0

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
                    usage = result.get("usage", {})
                    self.last_input_tokens = usage.get("prompt_tokens", 0)
                    self.last_output_tokens = usage.get("completion_tokens", 0)
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
