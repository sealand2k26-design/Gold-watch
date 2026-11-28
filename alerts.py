#!/usr/bin/env python3
"""Tell the owner when something actually happens - without them opening the page.

A website only helps if you look at it. Gold can touch your buy level at three in
the morning while you are asleep. This runs on the same hourly robot, checks the
price against the levels in config.json, and when one is crossed it opens an issue
on the repository. GitHub emails the repository owner about new issues, so the
alert lands in the inbox with no mail server, no password and no third party.

It refuses to nag: each rule fires once, and will not fire again until the price
has gone back the other side of the level (plus a buffer), or a week has passed.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "alert_state.json")
TROY_OZ_G = 31.1034768


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                      # noqa: BLE001
        return default


def rules(cfg):
    """Levels to watch. Editable in config.json without touching this file."""
    a = cfg.get("alerts") or {}
    return [
        {
            "id": "buy_second",
            "cmp": "below",
            "level": float(a.get("buy_second", 3450)),
            "title": "Gold hit your second buy level",
            "body": ("Spot gold has fallen to **${price:,.2f}** an ounce, which is at or below "
                     "your second-tranche level of ${level:,.0f}.\n\n"
                     "That is about **{sar:,.2f} SAR a gram** for 24k.\n\n"
                     "Your plan says: buy the second third here. Nothing has to be done this "
                     "minute - but this is the level you wrote down.\n\n"
                     "Check the page before acting: {url}"),
        },
        {
            "id": "stop",
            "cmp": "below",
            "level": float(a.get("invalidate", 3268)),
            "title": "Gold broke below your invalidation level",
            "body": ("Spot gold is **${price:,.2f}**, below the ${level:,.0f} line where the "
                     "whole case for holding was supposed to be wrong.\n\n"
                     "That is about **{sar:,.2f} SAR a gram** for 24k.\n\n"
                     "The plan says stop buying and reassess - do not average down through "
                     "this level.\n\n{url}"),
        },
        {
            "id": "dont_chase",
            "cmp": "above",
            "level": float(a.get("dont_chase", 3750)),
            "title": "Gold is above your do-not-chase level",
            "body": ("Spot gold is **${price:,.2f}**, above the ${level:,.0f} level where the "
                     "plan says not to buy more.\n\n"
                     "That is about **{sar:,.2f} SAR a gram** for 24k.\n\n"
                     "This is not a sell signal. It means the remaining tranches wait.\n\n{url}"),
        },
    ]


def fire(repo, token, title, body):
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/issues" % repo,
        data=json.dumps({"title": title, "body": body, "labels": ["alert"]}).encode(),
        headers={
            "Authorization": "Bearer %s" % token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "gold-watch-alerts",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("number")


def main():
    dry = "--dry-run" in sys.argv
    force = "--test" in sys.argv

    cfg = load(os.path.join(HERE, "config.json"), {})
    data = load(os.path.join(HERE, "docs", "data.json"), {})
    state = load(STATE, {})

    ind = data.get("indicators") or {}
    spot = (ind.get("spot") or {}).get("price") or (ind.get("gold") or {}).get("price")
    if not spot:
        print("no price in docs/data.json - nothing to check")
        return 0
    spot = float(spot)

    fx = ((data.get("local") or {}).get("fx") or {}).get("usdsar") or 3.75
    sar = spot / TROY_OZ_G * fx
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    url = "https://%s.github.io/%s/" % tuple(repo.split("/")) if "/" in repo else ""

    now = time.time()
    print("spot $%.2f  =  %.2f SAR/g 24k" % (spot, sar))

    for r in rules(cfg):
        hit = spot <= r["level"] if r["cmp"] == "below" else spot >= r["level"]
        st = state.get(r["id"]) or {}
        last = float(st.get("fired_at") or 0)

        # reset once the price moves 1.5% back the harmless side of the line
        buf = r["level"] * 0.015
        cleared = (spot > r["level"] + buf) if r["cmp"] == "below" else (spot < r["level"] - buf)
        if cleared and st.get("armed") is False:
            st["armed"] = True
            state[r["id"]] = st
            print("  %-12s re-armed" % r["id"])

        armed = st.get("armed", True)
        stale = (now - last) > 7 * 86400

        if force and r["id"] == "buy_second":
            hit, armed = True, True

        if not hit:
            print("  %-12s quiet (level %s)" % (r["id"], r["level"]))
            continue
        if not (armed or stale):
            print("  %-12s already sent, staying quiet" % r["id"])
            continue

        body = r["body"].format(price=spot, level=r["level"], sar=sar, url=url)
        if force:
            body = ("_This is a test alert, sent on purpose to prove the alarm works. "
                    "The price has not actually hit this level._\n\n---\n\n" + body)
        title = ("TEST - " if force else "") + r["title"]

        if dry or not (repo and token):
            print("  %-12s WOULD FIRE: %s" % (r["id"], title))
            continue

        try:
            n = fire(repo, token, title, body)
            print("  %-12s FIRED -> issue #%s" % (r["id"], n))
            state[r["id"]] = {"armed": False, "fired_at": now}
        except urllib.error.HTTPError as exc:
            print("  %-12s could not post: %s %s" % (r["id"], exc.code, exc.reason))

    if not dry:
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
