"""Centralized duration string parsing.

Parses human-friendly duration strings like '5m', '1h', '30s', '90' into seconds.
"""


def parse_duration(s: str) -> float:
    """Parse a duration string like '5m', '1h', '30s', '90' into seconds.

    Args:
        s: Duration string. Supported suffixes: 's' (seconds), 'm' (minutes),
           'h' (hours). Bare numbers are treated as seconds.

    Returns:
        Duration in seconds as a float.

    Raises:
        ValueError: If the string cannot be parsed as a valid duration.
    """
    s = s.strip().lower()
    if s.endswith('s'):
        return float(s[:-1])
    elif s.endswith('m'):
        return float(s[:-1]) * 60
    elif s.endswith('h'):
        return float(s[:-1]) * 3600
    else:
        return float(s)
