"""
Engine behind the summary prompt lab (#491).

The lab runs a draft summarizer prompt against a handful of live agents'
captured panes, every couple of seconds, so a prompt can be tuned by
watching what it produces. This module is the UI-free part: which agents
to sample, what to re-run, and the calls themselves. The dialog
(tui_widgets/summary_prompt_lab.py) owns the timer and the drawing.

A call is only made when its inputs changed — the draft, the prompt mode,
the agent's pane text or its previous summary — so an idle lab with a
settled draft costs nothing, while an agent that is working keeps getting
fresh results at the lab's cadence.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

MAX_SAMPLES = 6

# Response budget per mode — the same the live summarizer uses
# (SummarizerComponent._update_summary).
MAX_TOKENS = {"short": 50, "context": 75}


@dataclass
class LabSample:
    """One agent the draft is tried against."""

    session_id: str
    name: str
    window: str
    status: str = "unknown"
    # The live summarizer's current text for this mode — fed to the prompt
    # as {previous_summary} (as the live one would be) and shown alongside
    # the draft's result for comparison.
    live: str = ""


@dataclass
class LabResult:
    """The draft's latest answer for one agent."""

    text: Optional[str] = None  # None until the first call returns
    error: bool = False
    running: bool = False
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    key: Optional[Tuple] = None  # inputs the text was produced from


def pick_samples(sessions: Sequence[Any], focused_id: Optional[str], limit: int = MAX_SAMPLES) -> List[Any]:
    """Up to ``limit`` agents to try a prompt on: the focused one first,
    then the rest in display order. Terminated agents have nothing to
    summarise and are skipped."""
    def live(s: Any) -> bool:
        stats = getattr(s, "stats", None)
        return getattr(stats, "current_state", None) != "terminated"

    ordered = [s for s in sessions if getattr(s, "id", None) == focused_id]
    ordered += [s for s in sessions if getattr(s, "id", None) != focused_id]
    return [s for s in ordered if live(s)][:limit]


class PromptLabRunner:
    """Runs a draft prompt against samples; thread-safe, one run at a time.

    ``capture(window)`` returns an agent's pane text (or None);
    ``make_client()`` returns a fresh SummarizerClient-like object (one per
    call, so concurrent calls don't share its last-token counters);
    ``on_cost(usd)`` is told what each call cost, so the lab's spend counts
    against the summarizer's cost cap.
    """

    def __init__(
        self,
        capture: Callable[[str], Optional[str]],
        make_client: Callable[[], Any],
        lines: int,
        on_cost: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._capture = capture
        self._make_client = make_client
        self.lines = lines
        self._on_cost = on_cost
        self._lock = threading.Lock()
        self._busy = False
        self.results: Dict[str, LabResult] = {}
        self.calls = 0
        self.spent_usd = 0.0

    @property
    def busy(self) -> bool:
        return self._busy

    def reset(self) -> None:
        """Forget results (a mode switch makes them meaningless)."""
        with self._lock:
            self.results = {}

    def run(
        self,
        mode: str,
        template: str,
        samples: Sequence[LabSample],
        on_update: Optional[Callable[[], None]] = None,
    ) -> int:
        """One lab pass: re-run every sample whose inputs changed, in
        parallel, calling ``on_update`` as each result lands. Returns the
        number of calls made; 0 (without waiting) when a pass is already in
        flight. Blocking — call it from a worker thread."""
        with self._lock:
            if self._busy:
                return 0
            self._busy = True
        try:
            jobs = []
            for sample in samples:
                content = self._capture(sample.window)
                if not content:
                    continue
                key = (mode, template, hash(content), sample.live, sample.status)
                with self._lock:
                    result = self.results.setdefault(sample.session_id, LabResult())
                    if result.key == key and not result.error:
                        continue
                    result.running = True
                jobs.append((sample, content, key))
            if not jobs:
                return 0
            if on_update:
                on_update()
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                for sample, content, key in jobs:
                    pool.submit(self._call, mode, template, sample, content, key, on_update)
            return len(jobs)
        finally:
            with self._lock:
                self._busy = False

    def _call(self, mode, template, sample, content, key, on_update) -> None:
        client = self._make_client()
        start = time.monotonic()
        try:
            text = client.summarize(
                pane_content=content,
                previous_summary=sample.live,
                current_status=sample.status,
                lines=self.lines,
                max_tokens=MAX_TOKENS.get(mode, 75),
                mode=mode,
                prompt_template=template,
            )
        except Exception:
            text = None
        elapsed = time.monotonic() - start
        in_tok = getattr(client, "last_input_tokens", 0) or 0
        out_tok = getattr(client, "last_output_tokens", 0) or 0
        cost = 0.0
        model = getattr(client, "model", None)
        if (in_tok or out_tok) and isinstance(model, str):
            from .pricing import estimate_cost
            cost = estimate_cost(model, in_tok, out_tok)
        with self._lock:
            self.calls += 1
            self.spent_usd += cost
            result = self.results.setdefault(sample.session_id, LabResult())
            result.text = text.strip() if text else None
            result.error = text is None
            result.running = False
            result.seconds = elapsed
            result.input_tokens = in_tok
            result.output_tokens = out_tok
            result.key = key
        if cost and self._on_cost:
            self._on_cost(cost)
        if on_update:
            on_update()
