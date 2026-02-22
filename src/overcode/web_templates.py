"""
HTML/CSS/JS templates for web dashboard.

Templates are loaded from external HTML files for maintainability.
Mobile-first responsive design with dark theme matching the TUI aesthetic.
"""

from functools import lru_cache
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "web" / "templates"


@lru_cache(maxsize=None)
def get_dashboard_html() -> str:
    """Return the complete dashboard HTML page."""
    return (_TEMPLATE_DIR / "dashboard.html").read_text()


@lru_cache(maxsize=None)
def get_analytics_html() -> str:
    """Return the complete analytics dashboard HTML page."""
    return (_TEMPLATE_DIR / "analytics.html").read_text()
