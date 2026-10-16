#!/usr/bin/env python3
"""Regenerate src/overcode/data/claude_prices.json from the AWS Price List.

    python scripts/refresh_price_table.py --from AmazonBedrockFoundationModels-use1.json
    python scripts/refresh_price_table.py --from ... --check   # diff only, no write

Get the offer file with
``curl -O https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrockFoundationModels/current/us-east-1/index.json``.
Its ``global`` column is Anthropic's list price (the Anthropic API charges
the same), the plain column the geographic / in-region one. The parser
lives in ``overcode.price_table`` (unit-tested); ``OVERLAY`` below adds what
the AWS list does not say, each with its source. Commit the regenerated
file: it ships inside the wheel.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from overcode.price_table import DATA_PATH, parse_aws_price_list  # noqa: E402

AWS_SOURCE = ("AWS Price List, AmazonBedrockFoundationModels us-east-1 ({published}): "
              "global column = list price; regional = plain column / global")

# What the AWS foundation-model list does not carry. Each entry is merged
# into the generated row and its note appended to the row's source.
OVERLAY = {
    "claude-haiku-5-5": {
        "long_context": {"above": 100_000},
        "note": ("long-context tier applies to prompts over 100K tokens: Claude Code "
                 "changelog (claude-haiku-5-5: $0.50/$2.50 for prompts over 100K) and "
                 "model-config docs ('costs more on prompts longer than 100K tokens')"),
    },
    "claude-sonnet-5-5": {
        "note": ("cache read $0.10 from 2026-10-07 (was $0.20; cost-model-0.6.1 audit); "
                 "tokens read before this table shipped stay at the rate they were priced at"),
    },
    "claude-sonnet-4": {
        # Listed in the AmazonBedrockService offer (per 1K tokens), not the
        # foundation-model one: global long-context rates
        "long_context": {"above": 200_000, "input": 6.0, "output": 22.5,
                         "cache_write": 7.5, "cache_read": 0.6, "cache_write_1h": None},
        "note": ("long-context rates: AWS Price List AmazonBedrockService us-east-1 "
                 "(2026-10-14), Claude4Sonnet *-long-context-cross-region-global; "
                 "threshold 200K input tokens per Anthropic's long-context pricing "
                 "(not restated by AWS); 1-hour long-context cache write not listed, "
                 "taken as 2x input"),
    },
}


def build(offer: dict) -> dict:
    published = str(offer.get("publicationDate") or "")[:10]
    rows = parse_aws_price_list(offer)
    out = []
    for model in sorted(rows):
        row = dict(rows[model])
        source = AWS_SOURCE.format(published=published)
        if row.get("cache_write_1h") is None:
            source += "; 1-hour cache write not listed, taken as 2x input"
        extra = OVERLAY.get(model)
        if extra:
            if "long_context" in extra:
                row["long_context"] = {**(row.get("long_context") or {}), **extra["long_context"]}
            source += "; " + extra["note"]
        if isinstance(row.get("long_context"), dict) and row["long_context"].get("above") is None:
            # A long-context column with no known threshold cannot be applied
            del row["long_context"]
            source += "; long-context rates listed but threshold unknown: not applied"
        row["source"] = source
        row["as_of"] = published
        out.append(row)
    return {
        "as_of": published,
        "generated_by": "scripts/refresh_price_table.py",
        "rows": out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", required=True,
                        help="AmazonBedrockFoundationModels offer file (JSON)")
    parser.add_argument("--check", action="store_true", help="print the diff, write nothing")
    args = parser.parse_args()
    new = build(json.loads(Path(args.source).read_text()))
    try:
        old = json.loads(DATA_PATH.read_text())
    except (OSError, ValueError):
        old = {"rows": []}
    old_rows = {r["model"]: r for r in old.get("rows", [])}
    new_rows = {r["model"]: r for r in new["rows"]}
    for model in sorted(set(old_rows) | set(new_rows)):
        before, after = old_rows.get(model), new_rows.get(model)
        if before == after:
            continue
        if before is None:
            print(f"+ {model}")
        elif after is None:
            print(f"- {model}")
        else:
            changed = sorted(k for k in set(before) | set(after)
                             if k not in ("source", "as_of") and before.get(k) != after.get(k))
            if changed:
                print(f"~ {model}: " + ", ".join(
                    f"{k} {before.get(k)} -> {after.get(k)}" for k in changed))
    if not args.check:
        DATA_PATH.write_text(json.dumps(new, indent=1) + "\n")
        print(f"wrote {DATA_PATH.relative_to(REPO)} ({len(new['rows'])} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
