#!/usr/bin/env python3
"""Regenerate src/overcode/data/claude_prices.json from the AWS Price List.

    python scripts/refresh_price_table.py --from AmazonBedrockFoundationModels-use1.json
    python scripts/refresh_price_table.py --from ... --check   # diff only, no write

Get the offer file with
``curl -O https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrockFoundationModels/current/us-east-1/index.json``.
Its ``global`` column is Anthropic's list price (the Anthropic API charges
the same), the plain column the geographic / in-region one. The parser,
the overlay (what the AWS list does not say, each with its source) and the
builder live in ``overcode.price_table`` (unit-tested), shared with
``overcode models refresh --prices``, which builds a user's local override
from the same file. Commit the regenerated file: it ships inside the wheel.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from overcode.price_table import (  # noqa: E402
    DATA_PATH,
    build_table,
    diff_tables,
    load_offer,
    write_table,
)

GENERATED_BY = "scripts/refresh_price_table.py"


def build(offer: dict) -> dict:
    """The vendored table from an offer document."""
    return build_table(offer, generated_by=GENERATED_BY)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", required=True,
                        help="AmazonBedrockFoundationModels offer file (JSON)")
    parser.add_argument("--check", action="store_true", help="print the diff, write nothing")
    args = parser.parse_args()
    new = build(load_offer(Path(args.source)))
    try:
        old = json.loads(DATA_PATH.read_text())
    except (OSError, ValueError):
        old = {"rows": []}
    for line in diff_tables(old, new):
        print(line)
    if not args.check:
        write_table(new, DATA_PATH)
        print(f"wrote {DATA_PATH.relative_to(REPO)} ({len(new['rows'])} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
