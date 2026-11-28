#!/usr/bin/env python3
"""
Gold Macro Watch
----------------
Live indicator tracker + scenario engine for the gold thesis locked on 2026-11-27.

Data sources (all free, no API keys):
  - Yahoo Finance chart API  -> spot/futures prices, equity indices
  - FRED fredgraph.csv       -> Treasury yields, real yields, Fed funds, core PCE

Anything with no free feed (CDS spreads, central-bank tonnage, ETF tonnage)
lives in config.json and is edited by hand, with a visible "stale" marker.
"""

import csv
import io
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

# Market prices move intraday; FRED series are daily/monthly, so they get a
# much longer TTL. FRED also throttles bursts, so those calls are serialized.
TTL_MARKET = 120     # 2 min — matches the 60s page poll so refreshes feel live
TTL_MACRO = 3600     # 1 hour

app = Flask(__name__, static_folder=os.path.join(APP_DIR, "static"))

# Yahoo requires a browser User-Agent or it returns 403.
# FRED does the exact opposite: its WAF drops browser UA strings coming from
# non-browser TLS clients, but serves a plain curl UA instantly. Each host
# therefore gets its own identity.
UA_BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
UA_PLAIN = {"User-Agent": "curl/8.5.0", "Accept": "*/*"}

DISK_CACHE = os.path.join(APP_DIR, ".cache.json")

# A requests.Session is not safe to share across threads — the connection pool
# deadlocks when the warm-up thread and a Flask request thread both use it.
# One session per thread, per host-profile, gives keep-alive without the hazard.
_local = threading.local()


def session(profile="browser"):
    attr = f"s_{profile}"
    s = getattr(_local, attr, None)
    if s is None:
        s = requests.Session()
        s.headers.clear()
        s.headers.update(UA_BROWSER if profile == "browser" else UA_PLAIN)
        s.mount("https://", requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=4, max_retries=1))
        setattr(_local, attr, s)
    return s


_cache = {}
_cache_lock = threading.Lock()


def _load_disk_cache():
    """Seed memory cache from disk so a restart is instant and survives outages."""
    try:
        with open(DISK_CACHE) as f:
            for k, (ts, val) in json.load(f).items():
                _cache[k] = (ts, val)
        print(f"[cache] restored {len(_cache)} entries from disk", flush=True)
    except Exception:
        pass


def _save_disk_cache():
    try:
        with _cache_lock:
            snap = {k: [ts, val] for k, (ts, val) in _cache.items()}
        tmp = DISK_CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, DISK_CACHE)
    except Exception:
        pass


# --------------------------------------------------------------------------
# fetch helpers
# --------------------------------------------------------------------------
def cached(key, fn, ttl=TTL_MARKET):
    """Return cached value, refresh if stale, fall back to stale data on error.

    Serving a slightly stale number beats serving a hole in the dashboard.
    """
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < ttl:
        return hit[1]
    try:
        val = fn()
        if val is not None:
            with _cache_lock:
                _cache[key] = (now, val)
            return val
    except Exception:
        pass
    return hit[1] if hit else None


def yahoo(symbol, rng="2y"):
    """Latest price, % change, and the historical high/low over `rng`."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = session().get(url, params={"range": rng, "interval": "1d"}, timeout=20)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    meta = res["meta"]

    closes, stamps = [], []
    try:
        quotes = res["indicators"]["quote"][0]["close"]
        for ts, c in zip(res.get("timestamp", []), quotes):
            if c is not None:
                closes.append(float(c))
                stamps.append(ts)
    except Exception:
        pass

    price = meta.get("regularMarketPrice")
    peak = max(closes) if closes else price
    trough = min(closes) if closes else price
    peak_date = None
    if closes:
        peak_date = datetime.fromtimestamp(
            stamps[closes.index(peak)], tz=timezone.utc).strftime("%d %b %Y")

    return {
        "price": price,
        "change_pct": meta.get("regularMarketChangePercent"),
        "peak": peak,
        "peak_date": peak_date,
        "trough": trough,
        "drawdown_pct": ((price / peak - 1) * 100) if (price and peak) else None,
        "series": closes[-180:],
        "asof": datetime.fromtimestamp(
            meta.get("regularMarketTime", time.time()), tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC"),
    }


def fred(series_id):
    """Latest non-empty observation of a FRED series, plus the full rows."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    r = session("plain").get(url, params={"id": series_id}, timeout=20)
    r.raise_for_status()
    rows = []
    for row in csv.reader(io.StringIO(r.text)):
        if len(row) == 2 and row[1] not in (".", "value", series_id) and row[0] != "DATE":
            try:
                rows.append((row[0], float(row[1])))
            except ValueError:
                continue
    if not rows:
        return None
    prev = rows[-2][1] if len(rows) > 1 else rows[-1][1]
    return {
        "value": rows[-1][1],
        "date": rows[-1][0],
        "prev": prev,
        "change": rows[-1][1] - prev,
        "rows": rows,
    }


def fred_yoy(series_id):
    """Year-over-year % change of a monthly FRED index series."""
    d = fred(series_id)
    if not d or len(d["rows"]) < 13:
        return None
    latest = d["rows"][-1]
    year_ago = d["rows"][-13]
    yoy = (latest[1] / year_ago[1] - 1) * 100
    prev_latest = d["rows"][-2]
    prev_year = d["rows"][-14]
    prev_yoy = (prev_latest[1] / prev_year[1] - 1) * 100
    return {"value": round(yoy, 2), "date": latest[0],
            "prev": round(prev_yoy, 2), "change": round(yoy - prev_yoy, 2)}


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# --------------------------------------------------------------------------
# scenario engine
# --------------------------------------------------------------------------
PRIORS = {"A": 0.35, "B": 0.30, "C": 0.25, "D": 0.10}

SCENARIOS = {
    "A": {"name": "Inflation wins",
          "blurb": "Oil stays bid, Fed hikes through Q1 and keeps going",
          "gold": "$3,000 – $3,400 through H1 2027", "colour": "#f85149"},
    "B": {"name": "The credit event",
          "blurb": "AI financing cracks, Fed forced to pivot",
          "gold": "$3,200 first, then $4,500 – $5,500", "colour": "#3fb950"},
    "C": {"name": "Muddle through",
          "blurb": "Two more hikes then a long pause",
          "gold": "$3,400 – $4,000, then $4,200 – $4,600 late 2027", "colour": "#58a6ff"},
    "D": {"name": "Fiscal accident",
          "blurb": "Debt ceiling or failed auction breaks the long end",
          "gold": "$5,000+ regardless of the Fed", "colour": "#f0b429"},
}


def evaluate_signals(ind):
    """Score each live indicator against its thresholds. Returns log-odds nudges."""
    sig = []

    def add(key, label, value, fmt, bands):
        """bands: list of (test, weights, verdict, tone)"""
        if value is None:
            sig.append({"key": key, "label": label, "value": "no data",
                        "verdict": "feed unavailable", "tone": "dead", "weights": {}})
            return
        for test, weights, verdict, tone in bands:
            if test(value):
                sig.append({"key": key, "label": label, "value": fmt(value),
                            "verdict": verdict, "tone": tone, "weights": weights})
                return

    brent = ind.get("brent", {}).get("price")
    add("brent", "Brent crude", brent, lambda v: f"${v:,.2f}", [
        (lambda v: v > 110, {"A": 1.3, "C": -0.9, "B": -0.3},
         "Above $110 — inflation engine firing hard", "bear"),
        (lambda v: v > 100, {"A": 0.6, "C": -0.4},
         "$100–110 — war premium rebuilding", "bear"),
        (lambda v: v >= 85, {},
         "$85–100 — neutral, premium deflating", "neutral"),
        (lambda v: True, {"C": 0.8, "A": -0.7, "B": 0.2},
         "Below $85 — surplus winning, disinflationary", "bull"),
    ])

    pce = ind.get("core_pce", {}).get("value")
    add("core_pce", "Core PCE y/y", pce, lambda v: f"{v:.2f}%", [
        (lambda v: v > 3.5, {"A": 1.4, "C": -1.0},
         "Above 3.5% — Warsh has no choice but to keep hiking", "bear"),
        (lambda v: v > 3.0, {"A": 0.5, "C": -0.2},
         "3.0–3.5% — sticky, hikes stay on the table", "bear"),
        (lambda v: v >= 2.8, {"C": 0.3},
         "2.8–3.0% — drifting the right way", "neutral"),
        (lambda v: True, {"C": 1.0, "A": -1.1, "B": 0.2},
         "Below 2.8% — the pause case is live", "bull"),
    ])

    dgs30 = ind.get("dgs30", {}).get("value")
    add("dgs30", "30-year Treasury", dgs30, lambda v: f"{v:.2f}%", [
        (lambda v: v > 5.75, {"D": 2.2, "C": -0.6},
         "Above 5.75% — term premium breaking, fiscal accident live", "bear"),
        (lambda v: v > 5.50, {"D": 0.9},
         "5.50–5.75% — long end under real stress", "bear"),
        (lambda v: v >= 5.15, {},
         "5.15–5.50% — elevated but contained", "neutral"),
        (lambda v: True, {"D": -0.7, "C": 0.3},
         "Below 5.15% — fiscal pressure easing", "bull"),
    ])

    real = ind.get("real10", {}).get("value")
    add("real10", "10y real yield", real, lambda v: f"{v:.2f}%", [
        (lambda v: v > 3.0, {"A": 1.0, "C": -0.5},
         "Above 3% — maximum opportunity cost on gold", "bear"),
        (lambda v: v >= 2.5, {"A": 0.3},
         "2.5–3.0% — still a real headwind", "neutral"),
        (lambda v: True, {"C": 0.7, "B": 0.4, "A": -0.8},
         "Below 2.5% — headwind fading", "bull"),
    ])

    dxy = ind.get("dxy", {}).get("price")
    add("dxy", "Dollar index", dxy, lambda v: f"{v:.2f}", [
        (lambda v: v > 105, {"A": 0.9, "C": -0.5},
         "Above 105 — dollar wrecking ball still swinging", "bear"),
        (lambda v: v >= 101, {"A": 0.3},
         "101–105 — strong, capping any gold rally", "neutral"),
        (lambda v: v >= 99, {"C": 0.4},
         "99–101 — rolling over, constructive", "bull"),
        (lambda v: True, {"C": 0.8, "B": 0.5, "A": -0.9},
         "Below 99 — dollar peak confirmed, gold bottom likely in", "bull"),
    ])

    ndx_dd = ind.get("ndx", {}).get("drawdown_pct")
    add("ndx", "Nasdaq 100 drawdown", ndx_dd, lambda v: f"{v:.1f}% from peak", [
        (lambda v: v < -30, {"B": 1.8, "C": -0.8},
         "Deeper than −30% — credit contagion odds rising fast", "bull"),
        (lambda v: v < -20, {"B": 0.8, "C": -0.3},
         "−20% to −30% — bear market, forced selling active", "neutral"),
        (lambda v: v < -12, {"B": 0.3},
         "−12% to −20% — correction, not yet systemic", "neutral"),
        (lambda v: True, {"B": -0.9, "C": 0.6},
         "Shallower than −12% — AI stress receding", "neutral"),
    ])

    orcl_dd = ind.get("orcl", {}).get("drawdown_pct")
    add("orcl", "Oracle drawdown (credit proxy)", orcl_dd, lambda v: f"{v:.1f}% from peak", [
        (lambda v: v < -55, {"B": 1.6},
         "Deeper than −55% — market pricing real default risk", "bull"),
        (lambda v: v < -40, {"B": 0.7},
         "−40% to −55% — credit stress elevated", "neutral"),
        (lambda v: True, {"B": -0.5},
         "Shallower than −40% — AI credit holding together", "neutral"),
    ])

    return sig


def run_engine(indicators, manual):
    """Combine priors with live signal nudges into posterior scenario weights."""
    signals = evaluate_signals(indicators)

    # manual credit inputs get their own signal row
    cds = manual.get("oracle_cds_bp", {}).get("value")
    if cds is not None:
        if cds > 400:
            w, verdict, tone = {"B": 2.0, "C": -0.7}, "Above 400bp — scenario B trigger hit", "bull"
        elif cds > 320:
            w, verdict, tone = {"B": 0.8}, "320–400bp — widening, watch closely", "neutral"
        else:
            w, verdict, tone = {"B": -0.4}, "Below 320bp — credit calm for now", "neutral"
        signals.append({"key": "oracle_cds", "label": "Oracle 5y CDS (manual)",
                        "value": f"{cds:.0f} bp", "verdict": verdict,
                        "tone": tone, "weights": w,
                        "stale_date": manual.get("oracle_cds_bp", {}).get("asof")})

    cb = manual.get("cb_buying_tonnes_q", {}).get("value")
    if cb is not None:
        if cb < 200:
            w, verdict, tone = {"B": -0.8, "C": -0.6, "A": 0.7}, "Below 200t — the floor is going", "bear"
        elif cb < 260:
            w, verdict, tone = {"A": 0.3}, "200–260t — official bid softening", "neutral"
        else:
            w, verdict, tone = {"C": 0.5, "B": 0.4, "A": -0.4}, "Above 260t — floor intact", "bull"
        signals.append({"key": "cb_buying", "label": "Central bank buying /qtr (manual)",
                        "value": f"{cb:.0f} t", "verdict": verdict,
                        "tone": tone, "weights": w,
                        "stale_date": manual.get("cb_buying_tonnes_q", {}).get("asof")})

    # accumulate in log space
    logodds = {k: math.log(v) for k, v in PRIORS.items()}
    for s in signals:
        for k, delta in s.get("weights", {}).items():
            logodds[k] = logodds.get(k, 0) + delta * 0.45  # damping factor

    mx = max(logodds.values())
    exps = {k: math.exp(v - mx) for k, v in logodds.items()}
    total = sum(exps.values())
    posterior = {k: round(v / total * 100, 1) for k, v in exps.items()}

    return posterior, signals


# --------------------------------------------------------------------------
# forecast tracking
# --------------------------------------------------------------------------
def track_forecast(gold_price, cfg):
    """Compare live gold against the locked forecast path, interpolated to today."""
    path = cfg["forecast"]["path"]
    today = datetime.now(timezone.utc).date()

    pts = [(datetime.strptime(p["date"], "%Y-%m-%d").date(), p["gold"]) for p in path]
    pts.sort()

    expected = None
    if today <= pts[0][0]:
        expected = pts[0][1]
    elif today >= pts[-1][0]:
        expected = pts[-1][1]
    else:
        for i in range(len(pts) - 1):
            d0, v0 = pts[i]
            d1, v1 = pts[i + 1]
            if d0 <= today <= d1:
                span = (d1 - d0).days or 1
                frac = (today - d0).days / span
                expected = v0 + (v1 - v0) * frac
                break

    err = None
    if gold_price and expected:
        err = (gold_price / expected - 1) * 100

    return {
        "expected_today": round(expected) if expected else None,
        "actual": round(gold_price, 2) if gold_price else None,
        "error_pct": round(err, 2) if err is not None else None,
        "path": path,
        "locked_on": cfg["forecast"]["locked_on"],
        "invalidation": cfg["forecast"]["invalidation"],
        "key_bet": cfg["forecast"]["key_bet"],
    }


def build_alerts(indicators, manual, gold_price, cfg):
    """Only fire when a live value actually crosses a threshold.

    Message text is built lazily so a missing feed never blows up formatting.
    """
    alerts = []
    t = cfg["thresholds"]

    def chk(value, passes, level, text):
        if value is None:
            return
        try:
            if passes(value):
                alerts.append({"level": level, "text": text(value)})
        except Exception:
            return

    b = indicators.get("brent", {}).get("price")
    chk(b, lambda v: v > t["brent_bear"], "bear",
        lambda v: f"Brent ${v:,.0f} is above the ${t['brent_bear']} bear trigger — scenario A strengthening")
    chk(b, lambda v: v < t["brent_bull"], "bull",
        lambda v: f"Brent ${v:,.0f} is below the ${t['brent_bull']} bull trigger — disinflation in train")

    p = indicators.get("core_pce", {}).get("value")
    chk(p, lambda v: v > t["core_pce_bear"], "bear",
        lambda v: f"Core PCE {v:.2f}% is above {t['core_pce_bear']}% — more hikes coming")
    chk(p, lambda v: v < t["core_pce_bull"], "bull",
        lambda v: f"Core PCE {v:.2f}% is below {t['core_pce_bull']}% — pause case live")

    y = indicators.get("dgs30", {}).get("value")
    chk(y, lambda v: v > t["dgs30_fiscal"], "bear",
        lambda v: f"30-year at {v:.2f}% breached {t['dgs30_fiscal']}% — fiscal accident risk live")

    r = indicators.get("real10", {}).get("value")
    chk(r, lambda v: v > t["real10_bear"], "bear",
        lambda v: f"10y real yield {v:.2f}% above {t['real10_bear']}% — maximum opportunity cost on gold")

    d = indicators.get("dxy", {}).get("price")
    chk(d, lambda v: v < t["dxy_bull"], "bull",
        lambda v: f"DXY {v:.1f} below {t['dxy_bull']} — dollar peak likely in, gold bottom near")

    n = indicators.get("ndx", {}).get("drawdown_pct")
    chk(n, lambda v: v < -t["ndx_dd_credit"], "bull",
        lambda v: f"Nasdaq 100 {v:.1f}% from peak — past the {t['ndx_dd_credit']}% credit-contagion line")

    c = manual.get("oracle_cds_bp", {}).get("value")
    chk(c, lambda v: v > t["oracle_cds_trigger"], "bull",
        lambda v: f"Oracle CDS {v:.0f}bp above {t['oracle_cds_trigger']}bp — scenario B trigger HIT")

    chk(gold_price, lambda v: v < t["gold_invalidation"], "critical",
        lambda v: f"Gold ${v:,.0f} is below ${t['gold_invalidation']} — THESIS INVALIDATED, stop buying")

    if not alerts:
        alerts.append({"level": "calm",
                       "text": "No thresholds breached. Nothing has changed that matters to the thesis."})
    return alerts


def plain_summary(indicators, posterior, forecast, cfg):
    """Turn the numbers into sentences a normal person can act on."""
    g = (indicators.get("gold") or {}).get("price")
    chg = (indicators.get("gold") or {}).get("change_pct")
    brent = (indicators.get("brent") or {}).get("price")
    pce = (indicators.get("core_pce") or {}).get("value")
    dxy = (indicators.get("dxy") or {}).get("price")
    orcl = (indicators.get("orcl") or {}).get("drawdown_pct")

    # headline: where gold is today
    if g is None:
        return {"headline": "Price feed is down.", "lines": [], "action": None, "mood": "neutral"}

    move = "barely moved"
    if chg is not None:
        if chg > 1.5:
            move = f"jumped {chg:.1f}% today"
        elif chg > 0.3:
            move = f"rose {chg:.1f}% today"
        elif chg < -1.5:
            move = f"dropped {abs(chg):.1f}% today"
        elif chg < -0.3:
            move = f"fell {abs(chg):.1f}% today"

    headline = f"Gold is ${g:,.0f} an ounce and {move}."

    lines = []

    # vs the plan
    exp = forecast.get("expected_today")
    err = forecast.get("error_pct")
    if exp and err is not None:
        if abs(err) < 2:
            lines.append({"icon": "=", "tone": "neutral",
                          "text": f"That is almost exactly where the plan expected it "
                                  f"(${exp:,.0f}). Nothing surprising yet."})
        elif err > 0:
            lines.append({"icon": "^", "tone": "bull",
                          "text": f"That is {err:.1f}% higher than the plan expected "
                                  f"(${exp:,.0f}). Gold is holding up better than forecast."})
        else:
            lines.append({"icon": "v", "tone": "bear",
                          "text": f"That is {abs(err):.1f}% below the plan (${exp:,.0f}). "
                                  f"Falling faster than forecast."})

    # the main force
    if pce is not None:
        if pce > 3.0:
            lines.append({"icon": "!", "tone": "bear",
                          "text": f"US inflation is still {pce:.1f}% — well above the 2% the Fed wants. "
                                  f"So the Fed keeps raising interest rates, and that is the single "
                                  f"biggest thing pushing gold down."})
        else:
            lines.append({"icon": "+", "tone": "bull",
                          "text": f"US inflation has cooled to {pce:.1f}%. The pressure on the Fed to "
                                  f"keep raising rates is easing — good for gold."})

    # oil
    if brent is not None:
        if brent > 100:
            lines.append({"icon": "!", "tone": "bear",
                          "text": f"Oil is ${brent:,.0f}. Expensive oil feeds inflation, which means "
                                  f"higher rates — bad for gold, even though there is a war on."})
        elif brent < 85:
            lines.append({"icon": "+", "tone": "bull",
                          "text": f"Oil has fallen to ${brent:,.0f}. Cheaper oil cools inflation, "
                                  f"which eventually lets the Fed stop raising rates. Good for gold."})
        else:
            lines.append({"icon": "=", "tone": "neutral",
                          "text": f"Oil is ${brent:,.0f} — middling. Not adding to inflation, "
                                  f"not helping either."})

    # dollar
    if dxy is not None and dxy > 101:
        lines.append({"icon": "!", "tone": "bear",
                      "text": f"The US dollar is strong ({dxy:.0f}). A strong dollar makes gold more "
                              f"expensive everywhere else, which caps any rally."})
    elif dxy is not None and dxy <= 100:
        lines.append({"icon": "+", "tone": "bull",
                      "text": f"The dollar has weakened to {dxy:.0f}. This is usually the signal that "
                              f"gold has bottomed."})

    # AI stress
    if orcl is not None and orcl < -50:
        lines.append({"icon": "!", "tone": "bull",
                      "text": f"AI-linked companies are in real trouble — Oracle is down {abs(orcl):.0f}% "
                              f"from its peak. If their debts start failing, the Fed would be forced to "
                              f"cut rates, which would send gold sharply higher. Watch this."})

    # which future is winning
    lead = max(posterior, key=posterior.get)
    lead_plain = {
        "A": ("Prices stay hot, rates keep climbing", "Gold grinds lower for months"),
        "B": ("The AI bubble bursts", "Gold dips hard first, then jumps a long way"),
        "C": ("Things slowly calm down", "Gold drifts back up through next year"),
        "D": ("A US government debt scare", "Gold spikes regardless of anything else"),
    }[lead]
    lines.append({"icon": ">", "tone": "neutral",
                  "text": f"Most likely path right now ({posterior[lead]:.0f}%): {lead_plain[0]}. "
                          f"If that happens — {lead_plain[1].lower()}."})

    # what to do
    inval = cfg["thresholds"]["gold_invalidation"]
    if g < inval:
        action = {"label": "STOP BUYING",
                  "text": f"Gold has fallen below ${inval:,}. That was the line where the whole plan "
                          f"was wrong. Do not buy more. Reassess from scratch.",
                  "tone": "critical"}
    elif g < 3400:
        action = {"label": "BUY YOUR SECOND THIRD",
                  "text": f"At ${g:,.0f} you are in the zone the plan was waiting for. "
                          f"This is a good level to put the next tranche in.",
                  "tone": "bull"}
    elif g <= 3750:
        action = {"label": "HOLD — NOTHING TO DO",
                  "text": f"${g:,.0f} is the normal range. If you have bought your first third, "
                          f"just wait. The next real decision point is the Fed meeting on 9 December.",
                  "tone": "neutral"}
    else:
        action = {"label": "DON'T CHASE IT",
                  "text": f"At ${g:,.0f} gold has run above the buying zone. Do not pile in here. "
                          f"Wait for it to come back — there is always another entry.",
                  "tone": "warn"}

    moods = [l["tone"] for l in lines]
    mood = "bear" if moods.count("bear") > moods.count("bull") else (
        "bull" if moods.count("bull") > moods.count("bear") else "neutral")

    return {"headline": headline, "lines": lines, "action": action, "mood": mood}


PLAIN_LABELS = {
    "gold":     ("Gold price", "What one ounce costs in US dollars."),
    "silver":   ("Silver price", "Moves like gold but twice as wild. An early warning sign."),
    "brent":    ("Oil price", "Expensive oil pushes up inflation, which means higher interest rates — bad for gold."),
    "core_pce": ("US inflation", "The exact number the Fed watches. It wants 2%. Until it gets there, rates keep rising."),
    "fedfunds": ("US interest rate", "What the Fed charges. Gold pays you nothing, so when this is high, gold looks worse."),
    "dgs30":    ("US 30-year borrowing cost", "What it costs the US government to borrow long term. Rising = lenders are worried about US debt."),
    "dgs10":    ("US 10-year borrowing cost", "The world's benchmark interest rate."),
    "dgs2":     ("US 2-year borrowing cost", "Tracks what traders think the Fed will do next."),
    "real10":   ("Safe return after inflation", "What you'd earn risk-free in US bonds after inflation. This is gold's direct competition."),
    "dxy":      ("US dollar strength", "A strong dollar makes gold pricier for everyone outside the US, which holds the price down."),
    "ndx":      ("US tech stocks", "When these crash, investors sell gold to cover their losses elsewhere."),
    "orcl":     ("Oracle — AI debt stress", "A stand-in for how shaky AI company finances are. Deep falls mean lenders are getting nervous."),
    "nvda":     ("Nvidia", "The company at the centre of the AI boom. A barometer for the whole trade."),
    "gld":      ("Gold ETF", "The biggest gold fund. Tracks whether Western investors are buying or selling."),
}

SCENARIOS_PLAIN = {
    "A": {"name": "Prices stay hot", "blurb": "Inflation won't come down, so the Fed keeps raising rates",
          "gold": "Gold grinds down to roughly $3,000–3,400 and stays there for months"},
    "B": {"name": "The AI bubble bursts", "blurb": "AI company debts start failing and drag the economy down",
          "gold": "Gold drops to about $3,200 first — then jumps to $4,500–5,500"},
    "C": {"name": "Things slowly calm down", "blurb": "A couple more rate rises, then the Fed stops",
          "gold": "Gold drifts $3,400–4,000, then recovers to $4,200–4,600 late next year"},
    "D": {"name": "US debt scare", "blurb": "Government borrowing costs spiral and spook the market",
          "gold": "Gold spikes above $5,000 no matter what the Fed does"},
}


def fetch_rss(url, limit=8):
    """Minimal RSS/Atom reader. Returns [{title, link, published}]."""
    import html as _html
    import re as _re

    r = session("plain").get(url, timeout=15)
    r.raise_for_status()
    text = r.text
    blocks = _re.findall(r"<item[\s>](.*?)</item>", text, _re.S) or \
             _re.findall(r"<entry[\s>](.*?)</entry>", text, _re.S)

    out = []
    for b in blocks[:limit]:
        def grab(tag):
            m = _re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", b, _re.S)
            if not m:
                return None
            v = _re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=_re.S)
            v = _re.sub(r"<[^>]+>", "", v)
            return _html.unescape(v).strip()

        link = grab("link")
        if not link:
            m = _re.search(r'<link[^>]*href="([^"]+)"', b)
            link = m.group(1) if m else None
        title = grab("title")
        if title:
            out.append({"title": title[:180], "link": link,
                        "published": grab("pubDate") or grab("updated") or ""})
    return out


def event_calendar(cfg):
    """Known catalysts with a live countdown."""
    from datetime import date as _date
    today = datetime.now(timezone.utc).date()
    rows = []
    for e in cfg.get("events", []):
        try:
            d = _date.fromisoformat(e["date"])
        except Exception:
            continue
        days = (d - today).days
        rows.append({**e, "days": days,
                     "status": "past" if days < 0 else ("today" if days == 0 else "upcoming")})
    rows.sort(key=lambda r: r["date"])
    return rows


def compute_deltas(indicators, posterior, baseline):
    """Change in every tracked value since the day the forecast was locked.

    Percentage-type readings (yields, inflation, drawdowns, scenario weights)
    are compared in absolute points; prices are compared both ways.
    """
    if not baseline:
        return {}

    base = baseline.get("values", {})
    field = {"gold": "price", "silver": "price", "brent": "price", "dxy": "price",
             "ndx": "drawdown_pct", "orcl": "drawdown_pct", "nvda": "drawdown_pct"}
    as_price = {"gold", "silver", "brent", "dxy"}

    out = {}
    for key, b in base.items():
        cur = (indicators.get(key) or {}).get(field.get(key, "value"))
        if cur is None or b is None:
            continue
        diff = cur - b
        out[key] = {
            "base": round(b, 4),
            "now": round(cur, 4),
            "diff": round(diff, 4),
            "pct": round((cur / b - 1) * 100, 2) if (key in as_price and b) else None,
        }

    out["posterior"] = {
        k: {"base": baseline.get("posterior", {}).get(k),
            "now": posterior.get(k),
            "diff": round(posterior.get(k, 0) - baseline.get("posterior", {}).get(k, 0), 1)}
        for k in ("A", "B", "C", "D")
    }
    out["_date"] = baseline.get("date")
    return out


# --------------------------------------------------------------------------
# history — the part that makes this a tracker rather than a gauge
# --------------------------------------------------------------------------
HISTORY_PATH = os.path.join(APP_DIR, "history.jsonl")
HISTORY_EVERY = 900  # 15 minutes


def log_history(payload):
    """Append one compact row per interval. Append-only, survives restarts."""
    i = payload["indicators"]

    def g(key, field="value"):
        v = i.get(key) or {}
        return v.get(field)

    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gold": g("gold", "price"),
        "brent": g("brent", "price"),
        "dxy": g("dxy", "price"),
        "silver": g("silver", "price"),
        "dgs30": g("dgs30"),
        "dgs10": g("dgs10"),
        "real10": g("real10"),
        "core_pce": g("core_pce"),
        "ndx_dd": g("ndx", "drawdown_pct"),
        "orcl_dd": g("orcl", "drawdown_pct"),
        "post": payload["posterior"],
        "err": payload["forecast"]["error_pct"],
    }
    if row["gold"] is None:
        return  # a failed fetch is not a data point
    try:
        with open(HISTORY_PATH, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def read_history(limit=600):
    rows = []
    try:
        with open(HISTORY_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
    except FileNotFoundError:
        return []
    return rows[-limit:]


def history_worker():
    """Record a snapshot on a fixed cadence, independent of anyone viewing."""
    time.sleep(20)
    while True:
        try:
            payload = build_snapshot()
            log_history(payload)
        except Exception as e:
            print(f"[history] skipped: {type(e).__name__}", flush=True)
        time.sleep(HISTORY_EVERY)


# --------------------------------------------------------------------------
# snapshot builder + routes
# --------------------------------------------------------------------------
TROY_OZ_G = 31.1034768


def local_prices(indicators, cfg):
    """Convert the world gold price into what it actually costs locally.

    The world quotes gold in US dollars per troy ounce. Nobody in Dammam or
    Delhi buys that way - Saudi dealers price per gram, Indian ones per ten
    grams, both by karat. This does the arithmetic so the page shows a number
    you could walk into a shop and compare against.
    """
    gold = (indicators.get("gold") or {}).get("price")
    if not gold:
        return {}

    fx = {
        "usdsar": (indicators.get("usdsar") or {}).get("price") or 3.75,
        "usdinr": (indicators.get("usdinr") or {}).get("price"),
    }
    conf = cfg.get("local") or {}
    out = {"fx": fx, "world_usd_oz": round(gold, 2), "markets": []}

    def rows(per_unit_usd, karats, premium_pct, rate, grams):
        res = []
        for k in karats:
            purity = k / 24.0
            base = gold / TROY_OZ_G * grams * purity * rate
            res.append({
                "karat": "%dk" % k,
                "purity_pct": round(purity * 100, 1),
                "raw": round(base, 2),
                "with_premium": round(base * (1 + premium_pct / 100.0), 2),
            })
        return res

    ksa = conf.get("ksa")
    if ksa and fx["usdsar"]:
        out["markets"].append({
            "key": "ksa",
            "label": ksa.get("label", "Saudi Arabia"),
            "currency": ksa.get("currency", "SAR"),
            "unit": ksa.get("unit", "per gram"),
            "fx_rate": round(fx["usdsar"], 4),
            "fx_label": "1 USD = %.4f SAR (pegged)" % fx["usdsar"],
            "premium_pct": ksa.get("dealer_premium_pct", 0),
            "premium_label": "typical dealer premium",
            "rows": rows(None, ksa.get("karats", [24]), ksa.get("dealer_premium_pct", 0),
                         fx["usdsar"], 1),
            "note": ksa.get("note", ""),
        })

    ind = conf.get("india")
    if ind and fx["usdinr"]:
        out["markets"].append({
            "key": "india",
            "label": ind.get("label", "India"),
            "currency": ind.get("currency", "INR"),
            "unit": ind.get("unit", "per 10 grams"),
            "fx_rate": round(fx["usdinr"], 3),
            "fx_label": "1 USD = %.2f INR" % fx["usdinr"],
            "premium_pct": ind.get("import_duty_gst_pct", 0),
            "premium_label": "import duty + GST",
            "rows": rows(None, ind.get("karats", [24]), ind.get("import_duty_gst_pct", 0),
                         fx["usdinr"], 10),
            "note": ind.get("note", ""),
        })

    # What the next planned buy level looks like in local money.
    plan = cfg.get("plan") or {}
    nxt = next((t for t in plan.get("tranches", [])
                if t.get("status") != "done" and t.get("trigger_high")), None)
    if nxt:
        trig = float(nxt["trigger_high"])
        out["next_buy"] = {
            "usd_oz": trig,
            "ksa_gram_24k": round(trig / TROY_OZ_G * (fx["usdsar"] or 3.75), 2),
            "india_10g_24k": round(trig / TROY_OZ_G * 10 * fx["usdinr"], 2) if fx["usdinr"] else None,
        }
    return out


def build_snapshot():
    cfg = load_config()

    indicators = {}

    # Yahoo tolerates parallel requests fine.
    market_jobs = {
        "gold":   lambda: cached("gold",   lambda: yahoo("GC=F")),
        "silver": lambda: cached("silver", lambda: yahoo("SI=F", "1y")),
        "brent":  lambda: cached("brent",  lambda: yahoo("BZ=F", "1y")),
        "dxy":    lambda: cached("dxy",    lambda: yahoo("DX-Y.NYB", "1y")),
        "ndx":    lambda: cached("ndx",    lambda: yahoo("%5ENDX", "1y")),
        "gld":    lambda: cached("gld",    lambda: yahoo("GLD", "1y")),
        "nvda":   lambda: cached("nvda",   lambda: yahoo("NVDA", "1y")),
        "orcl":   lambda: cached("orcl",   lambda: yahoo("ORCL", "1y")),
        "usdsar": lambda: cached("usdsar", lambda: yahoo("SAR=X", "1mo")),
        "usdinr": lambda: cached("usdinr", lambda: yahoo("INR=X", "1mo")),
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {k: pool.submit(v) for k, v in market_jobs.items()}
        for k, fut in futures.items():
            try:
                indicators[k] = fut.result(timeout=30) or {}
            except Exception:
                indicators[k] = {}

    # FRED throttles bursts — run these one at a time, cached for an hour.
    macro_jobs = [
        ("dgs30",    "dgs30",  lambda: fred("DGS30")),
        ("dgs10",    "dgs10",  lambda: fred("DGS10")),
        ("dgs2",     "dgs2",   lambda: fred("DGS2")),
        ("real10",   "real10", lambda: fred("DFII10")),
        ("fedfunds", "ff",     lambda: fred("DFF")),
        ("core_pce", "pce",    lambda: fred_yoy("PCEPILFE")),
    ]
    for name, key, fn in macro_jobs:
        indicators[name] = cached(key, fn, ttl=TTL_MACRO) or {}

    # trim heavy series out of everything but gold
    for k, v in indicators.items():
        if isinstance(v, dict):
            v.pop("rows", None)
            if k != "gold":
                v.pop("series", None)

    manual = cfg["manual"]
    gold_price = indicators.get("gold", {}).get("price")
    posterior, signals = run_engine(indicators, manual)
    _save_disk_cache()

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "indicators": indicators,
        "manual": manual,
        "scenarios": SCENARIOS,
        "priors": {k: round(v * 100, 1) for k, v in PRIORS.items()},
        "posterior": posterior,
        "signals": signals,
        "forecast": track_forecast(gold_price, cfg),
        "alerts": build_alerts(indicators, manual, gold_price, cfg),
        "thresholds": cfg["thresholds"],
        "baseline": cfg.get("baseline", {}),
        "deltas": compute_deltas(indicators, posterior, cfg.get("baseline", {})),
        "local": local_prices(indicators, cfg),
        "plain": plain_summary(indicators, posterior,
                               track_forecast(gold_price, cfg), cfg),
        "plain_labels": PLAIN_LABELS,
        "scenarios_plain": SCENARIOS_PLAIN,
    }


@app.route("/api/snapshot")
def snapshot():
    return jsonify(build_snapshot())


@app.route("/api/news")
def news():
    cfg = load_config()
    fed = cached("rss_fed",
                 lambda: fetch_rss("https://www.federalreserve.gov/feeds/press_monetary.xml", 6),
                 ttl=600) or []
    wires = cached("rss_news",
                   lambda: fetch_rss("https://news.google.com/rss/search?"
                                     "q=gold+price+OR+%22federal+reserve%22+OR+brent+crude"
                                     "&hl=en-US&gl=US&ceid=US:en", 10),
                   ttl=600) or []
    return jsonify({"fed": fed, "wires": wires, "events": event_calendar(cfg)})


@app.route("/api/history")
def history():
    rows = read_history()
    return jsonify({
        "rows": rows,
        "count": len(rows),
        "since": rows[0]["ts"] if rows else None,
        "interval_s": HISTORY_EVERY,
    })


@app.route("/api/manual", methods=["POST"])
def update_manual():
    """Patch a hand-maintained value (CDS spread, tonnage, etc)."""
    body = request.get_json(force=True)
    key, val = body.get("key"), body.get("value")
    cfg = load_config()
    if key not in cfg["manual"]:
        return jsonify({"ok": False, "error": "unknown key"}), 400
    cfg["manual"][key]["value"] = float(val)
    cfg["manual"][key]["asof"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    return jsonify({"ok": True, "manual": cfg["manual"]})


@app.route("/")
def index():
    """Serve the page with a proof-of-origin stamp.

    The workspace file viewer can also render static/index.html, but it does so
    in a network-less sandbox where fetch() always fails. Sniffing location /
    origin is unreliable across browsers, so instead the live server injects a
    marker. No marker == the page was opened as a file, and it says so plainly
    instead of claiming the server is down.
    """
    path = os.path.join(app.static_folder, "index.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    stamp = (
        "<script>window.__SERVED_BY_APP__=true;"
        f"window.__SERVED_AT__={json.dumps(datetime.now(timezone.utc).strftime('%H:%M:%S UTC'))};"
        "</script>"
    )
    html = html.replace("<script>", stamp + "<script>", 1)
    return Response(html, mimetype="text/html",
                    headers={"Cache-Control": "no-store"})


def warm_cache():
    """Pre-fetch the slow FRED series in the background.

    Runs off-thread so the server binds its port immediately — FRED can take
    tens of seconds when it is throttling, and a blocked bind kills the preview.
    """
    time.sleep(1)
    for sid, key in [("DGS30", "dgs30"), ("DGS10", "dgs10"), ("DGS2", "dgs2"),
                     ("DFII10", "real10"), ("DFF", "ff")]:
        cached(key, lambda s=sid: fred(s), ttl=TTL_MACRO)
        time.sleep(0.6)  # be polite, avoid the throttle
    cached("pce", lambda: fred_yoy("PCEPILFE"), ttl=TTL_MACRO)
    ok = sum(1 for k in ("dgs30", "dgs10", "dgs2", "real10", "ff", "pce") if _cache.get(k))
    _save_disk_cache()
    print(f"[warm] {ok}/6 macro series cached", flush=True)


if __name__ == "__main__":
    _load_disk_cache()
    threading.Thread(target=warm_cache, daemon=True).start()
    threading.Thread(target=history_worker, daemon=True).start()
    port = int(os.environ.get("PORT", "3000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
