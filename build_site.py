#!/usr/bin/env python3
"""Build the public website - no server, no sandbox, no preview link.

This imports the same engine the live app uses, pulls the same live data,
and writes one static HTML file to docs/index.html. GitHub Pages serves
that folder for free, and a scheduled GitHub Action re-runs this script
every 15 minutes so the page keeps itself current with nobody watching.

    python3 build_site.py

Why static instead of hosting Flask: a static page cannot crash, costs
nothing, has no cold start, and loads instantly on a phone. The only
thing it gives up is sub-minute freshness, which a three-tranche buyer
does not need.
"""

import json
import os
import sys
import traceback

import app
import make_report

OUT_DIR = os.environ.get("SITE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs"))

FED_FEED = "https://www.federalreserve.gov/feeds/press_monetary.xml"
WIRE_FEED = ("https://news.google.com/rss/search?"
             "q=gold+price+OR+%22federal+reserve%22+OR+brent+crude"
             "&hl=en-US&gl=US&ceid=US:en")


def safe(fn, fallback, what):
    """Never let one dead feed kill the whole build."""
    try:
        return fn()
    except Exception:                                  # noqa: BLE001
        print("  ! %s failed, continuing without it" % what, file=sys.stderr)
        traceback.print_exc(limit=1, file=sys.stderr)
        return fallback


def main():
    print("building site...")
    snap = app.build_snapshot()

    cfg = app.load_config()
    news = {
        "fed": safe(lambda: app.fetch_rss(FED_FEED, 6), [], "fed feed"),
        "wires": safe(lambda: app.fetch_rss(WIRE_FEED, 10), [], "news feed"),
        "events": safe(lambda: app.event_calendar(cfg), [], "calendar"),
    }

    doc = make_report.render(snap, news)

    os.makedirs(OUT_DIR, exist_ok=True)
    index = os.path.join(OUT_DIR, "index.html")
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(doc)

    # Machine-readable copy, so the page can be consumed by anything else later.
    with open(os.path.join(OUT_DIR, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True, default=str)

    # Stops GitHub Pages running the output through Jekyll.
    open(os.path.join(OUT_DIR, ".nojekyll"), "w").close()

    gold = (snap["indicators"].get("spot") or snap["indicators"].get("gold") or {}).get("price")
    print("  wrote %s (%d bytes)" % (index, len(doc)))
    print("  gold $%s -> %s" % (gold, snap["plain"]["action"]["label"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
