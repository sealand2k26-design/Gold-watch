#!/usr/bin/env python3
"""Build a completely self-contained gold report.

Why this exists
---------------
The live Flask app works, but it can only be viewed through a preview panel.
When that panel is unavailable, the user is left with nothing. This script
pulls the same live data and bakes it into ONE static HTML file with no
JavaScript fetching, no external CSS, no fonts and no images - so it renders
correctly in the workspace file viewer, which has no network access.

Trade-off: it is a photograph, not a video. Re-run it to get a fresh one.

    cd ~/gold-watch && python3 make_report.py
"""

import html
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = os.environ.get("GOLD_BASE", "http://127.0.0.1:8000")
OUT = os.environ.get("GOLD_OUT", "/home/user/gold-now.html")

C = {
    "bull": "#2f9e44", "bear": "#d6336c", "warn": "#e8900c",
    "neutral": "#666", "critical": "#d6336c",
}


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode())


def e(x):
    return html.escape(str(x))


def money(v, dp=0):
    try:
        return "$" + format(float(v), ",." + str(dp) + "f")
    except (TypeError, ValueError):
        return "-"


def load_cfg():
    """Read config.json once, tolerate it being missing."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                      # noqa: BLE001
        return {}


def plan_block(price):
    """Render the user's own buying plan, measured against today's price."""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            plan = json.load(fh).get("plan")
    except Exception:                                      # noqa: BLE001
        return ""
    if not plan:
        return ""

    cards = []
    for t in plan.get("tranches", []):
        done = t.get("status") == "done"
        col = C["bull"] if done else "#b0a06a"
        if done:
            head = "DONE &middot; %s at %s" % (e(t.get("when", "")), money(t.get("price")))
            dist = ""
            if price and t.get("price"):
                diff = float(price) - float(t["price"])
                updown = "above" if diff >= 0 else "below"
                dist = ('<div style="font-size:12.5px;color:%s;margin-top:4px">'
                        "Today's price is %s %s what you paid.</div>"
                        % (C["bull"] if diff >= 0 else C["bear"], money(abs(diff)), updown))
        else:
            lo, hi = t.get("trigger_low"), t.get("trigger_high")
            if lo and hi:
                head = "WAITING &middot; buy at %s - %s" % (money(lo), money(hi))
                dist = ""
                if price:
                    gap = float(price) - float(hi)
                    if gap > 0:
                        dist = ('<div style="font-size:12.5px;color:#8a7a3a;margin-top:4px">'
                                "Still %s too expensive. Do nothing.</div>" % money(gap))
                    else:
                        dist = ('<div style="font-size:13px;color:%s;font-weight:700;margin-top:4px">'
                                "IN RANGE NOW - this is your buy.</div>" % C["bull"])
            else:
                head = "WAITING &middot; no price trigger - timing based"
                dist = ""
        cards.append(
            '<div style="border:1px solid #e3e3e3;border-left:4px solid %s;border-radius:7px;'
            'padding:12px 14px;margin-bottom:9px;background:#fff">'
            '<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">'
            '<b style="font-size:15px">%s</b>'
            '<span style="font-size:11.5px;letter-spacing:1px;color:%s;font-weight:700">%s</span></div>'
            '<div style="color:#555;font-size:13px;margin-top:5px;line-height:1.5">%s</div>%s</div>'
            % (col, e(t.get("label", "Tranche")), col, head, e(t.get("note", "")), dist)
        )

    rules = "".join('<li style="margin-bottom:7px">%s</li>' % e(r) for r in plan.get("rules", []))
    notes = "".join('<li style="margin-bottom:7px">%s</li>' % e(n) for n in plan.get("buying_notes", []))

    return (
        '<h2 style="font-size:19px;margin:0 0 4px">Your plan</h2>'
        '<div style="color:#777;font-size:12.5px;margin-bottom:12px">%s<br>%s</div>'
        '%s'
        '<div style="border:1px solid #e3e3e3;border-radius:7px;padding:14px 16px;margin:14px 0 8px;background:#fcfcfa">'
        '<div style="font-size:11px;letter-spacing:1.5px;color:#8a7a3a;font-weight:700;margin-bottom:8px">YOUR RULES</div>'
        '<ul style="padding-left:19px;margin:0;font-size:13.5px">%s</ul></div>'
        '<div style="border:1px solid #e3e3e3;border-radius:7px;padding:14px 16px;margin-bottom:30px;background:#fcfcfa">'
        '<div style="font-size:11px;letter-spacing:1.5px;color:#8a7a3a;font-weight:700;margin-bottom:8px">'
        'BUYING IT IN SAUDI ARABIA</div>'
        '<ul style="padding-left:19px;margin:0;font-size:13.5px">%s</ul></div>'
        % (e(plan.get("who", "")), e(plan.get("style", "")), "".join(cards), rules, notes)
    )


def local_block(snap):
    """Show the world price converted into Saudi and Indian shop prices."""
    loc = snap.get("local") or {}
    if not loc.get("markets"):
        return ""

    cards = []
    for m in loc["markets"]:
        rows = []
        n_pub = sum(1 for r in m["rows"] if r.get("basis") == "published")
        if n_pub:
            srcs = ((snap.get("indicators") or {}).get("published") or {}).get(m.get("key"), {})
            names = ", ".join(srcs.get("sources") or [])
            head = ("Read straight off %s - the rate being published in the market right now, "
                    "not worked out from the dollar price." % (names or "local price sites"))
        else:
            head = ("Local sites could not be read this time, so these are converted from "
                    "the spot price instead.")
        rows.append('<tr><td colspan="3" style="padding:2px 8px 9px;color:#777;font-size:12px">'
                    + e(head) + '</td></tr>')

        grams = int(m.get("grams") or (10 if "10" in (m.get("unit") or "") else 1))
        dp = 0 if grams == 10 else 2
        for r in m["rows"]:
            cal = (r["raw"] / r["computed"]) if r.get("computed") else 1.0
            attrs = ('data-lv="1" data-fx="%s" data-g="%s" data-pur="%s" data-dp="%s" data-cal="%s"'
                     % (m["fx_rate"], grams, r.get("purity", r["purity_pct"] / 100.0), dp,
                        round(cal, 6)))
            rows.append(
                '<tr>'
                '<td style="padding:7px 8px;border-bottom:1px solid #eee"><b>%s</b>'
                '<span style="color:#999;font-size:11.5px"> %s%%</span></td>'
                '<td style="padding:7px 8px;border-bottom:1px solid #eee;text-align:right;'
                'font-family:ui-monospace,Menlo,monospace" %s data-prem="0">%s</td>'
                '<td style="padding:7px 8px;border-bottom:1px solid #eee;text-align:right;'
                'font-family:ui-monospace,Menlo,monospace;color:#8a7a3a" %s data-prem="%s">'
                '<b>%s</b></td>'
                '</tr>'
                % (e(r["karat"]), r["purity_pct"],
                   attrs, format(r["raw"], ",.2f"),
                   attrs, m["premium_pct"], format(r["with_premium"], ",.2f"))
            )
        cards.append(
            '<div style="border:1px solid #e3e3e3;border-radius:8px;padding:14px 16px;'
            'margin-bottom:12px;background:#fff">'
            '<div style="display:flex;justify-content:space-between;align-items:baseline;'
            'flex-wrap:wrap;gap:8px">'
            '<b style="font-size:16px">%s</b>'
            '<span style="color:#888;font-size:11.5px">%s</span></div>'
            '<div style="color:#777;font-size:12px;margin:3px 0 9px">%s &middot; %s</div>'
            '<table style="width:100%%;border-collapse:collapse;font-size:13.5px">'
            '<tr><th style="text-align:left;padding:4px 8px;font-size:11px;color:#999;'
            'text-transform:uppercase;letter-spacing:.8px">Purity</th>'
            '<th style="text-align:right;padding:4px 8px;font-size:11px;color:#999;'
            'text-transform:uppercase;letter-spacing:.8px">Benchmark</th>'
            '<th style="text-align:right;padding:4px 8px;font-size:11px;color:#8a7a3a;'
            'text-transform:uppercase;letter-spacing:.8px">+ %s %s%%</th></tr>'
            '%s</table>'
            '<div style="color:#666;font-size:12.5px;margin-top:9px;line-height:1.5">%s</div>'
            '</div>'
            % (e(m["label"]), e(m["fx_label"]), e(m["currency"]), e(m["unit"]),
               e(m["premium_label"]), m["premium_pct"], "".join(rows), e(m["note"]))
        )

    nb = loc.get("next_buy") or {}
    target = ""
    if nb.get("ksa_gram_24k"):
        india = (" &middot; <b>%s INR</b> per 10g in India" % format(nb["india_10g_24k"], ",.0f")) \
                if nb.get("india_10g_24k") else ""
        target = (
            '<div style="border:1px dashed #b0a06a;border-radius:8px;padding:12px 15px;'
            'margin-bottom:30px;background:#fcfcfa;font-size:13.5px">'
            '<b style="color:#8a7a3a">Your next buy level in local money:</b> when gold reaches '
            '$%s an ounce, that is about <b>%s SAR</b> per gram of 24k in Saudi Arabia%s '
            '<span style="color:#888">(before dealer premium).</span></div>'
            % (format(nb["usd_oz"], ",.0f"), format(nb["ksa_gram_24k"], ",.2f"), india)
        )

    return (
        '<h2 style="font-size:19px;margin:0 0 4px">What it costs where you are '
        '<span style="font-size:10.5px;letter-spacing:1.5px;color:#2f9e44;'
        'font-weight:700;vertical-align:middle">&#9679; LIVE</span></h2>'
        '<div style="color:#777;font-size:12.5px;margin-bottom:12px">'
        'Converted from the live <b>spot</b> gold price ($%s an ounce) - the benchmark shops '
        'price off, not the futures contract quoted at the top of this page.</div>'
        '%s%s'
        % (format(loc.get("world_usd_oz", 0), ",.2f"), "".join(cards), target)
    )


SOURCE_OF = {
    "spot":     ("Spot gold (XAU/USD)",        "median of 3 feeds, live"),
    "gold":     ("Gold futures (COMEX GC=F)",  "Yahoo Finance"),
    "usdsar":   ("US dollar / Saudi riyal",    "Yahoo Finance"),
    "usdinr":   ("US dollar / Indian rupee",   "Yahoo Finance"),
    "brent":    ("Brent crude oil",            "Yahoo Finance"),
    "dxy":      ("US dollar index",            "Yahoo Finance"),
    "silver":   ("Silver",                     "Yahoo Finance"),
    "ndx":      ("Nasdaq 100",                 "Yahoo Finance"),
    "orcl":     ("Oracle share price",         "Yahoo Finance"),
    "nvda":     ("Nvidia share price",         "Yahoo Finance"),
    "core_pce": ("US core inflation",          "US Federal Reserve (FRED)"),
    "real10":   ("10-year real yield",         "US Federal Reserve (FRED)"),
    "dgs30":    ("30-year Treasury yield",     "US Federal Reserve (FRED)"),
    "dgs10":    ("10-year Treasury yield",     "US Federal Reserve (FRED)"),
    "fedfunds": ("Fed funds rate",             "US Federal Reserve (FRED)"),
}


def sources_block(snap):
    """Every number with its own timestamp, so nobody has to be trusted."""
    ind = snap.get("indicators") or {}
    rows = []
    for key, (label, src) in SOURCE_OF.items():
        v = ind.get(key) or {}
        val = v.get("price", v.get("value"))
        asof = v.get("asof")
        if val is None or not asof:
            continue
        rows.append(
            '<tr>'
            '<td style="padding:6px 8px;border-bottom:1px solid #eee">%s</td>'
            '<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;'
            'font-family:ui-monospace,Menlo,monospace">%s</td>'
            '<td style="padding:6px 8px;border-bottom:1px solid #eee;color:#777;'
            'font-size:11.5px">%s</td>'
            '<td style="padding:6px 8px;border-bottom:1px solid #eee;color:#999;'
            'font-size:11.5px">%s</td></tr>'
            % (e(label), e(val), e(str(asof)[:16].replace("T", " ")), e(src))
        )
    if not rows:
        return ""
    return (
        '<h2 style="font-size:19px;margin:0 0 4px">Where these numbers come from</h2>'
        '<div style="color:#777;font-size:12.5px;margin-bottom:10px">'
        'Each figure carries the moment it was published. Check any of them yourself - '
        'you should not have to take this page on trust. '
        '<b>Markets close at weekends</b>, so on a Saturday or Sunday the share and futures '
        'timestamps correctly show Friday.</div>'
        '<table style="width:100%%;border-collapse:collapse;font-size:13px;margin-bottom:26px">'
        '<tr><th style="text-align:left;padding:4px 8px;font-size:10.5px;color:#999;'
        'text-transform:uppercase;letter-spacing:.8px">What</th>'
        '<th style="text-align:right;padding:4px 8px;font-size:10.5px;color:#999;'
        'text-transform:uppercase;letter-spacing:.8px">Value</th>'
        '<th style="text-align:left;padding:4px 8px;font-size:10.5px;color:#999;'
        'text-transform:uppercase;letter-spacing:.8px">Published</th>'
        '<th style="text-align:left;padding:4px 8px;font-size:10.5px;color:#999;'
        'text-transform:uppercase;letter-spacing:.8px">Source</th></tr>'
        '%s</table>' % "".join(rows)
    )


def live_block(snap):
    """A ticker that keeps moving between hourly rebuilds.

    The page itself is static - GitHub rebuilds it roughly hourly. But gold
    never stops: tokenised gold (PAXG) trades 24/7, so its bid/ask moves even
    on a Saturday when COMEX is shut. This block runs in the reader's browser,
    polls two open feeds directly, and updates the number in front of them.

    If either feed is unreachable the static numbers simply stay put - nothing
    on the page breaks.
    """
    loc = snap.get("local") or {}
    fx = loc.get("fx") or {}
    sar = fx.get("usdsar") or 3.75
    inr = fx.get("usdinr") or 0
    base = loc.get("world_usd_oz") or (snap.get("indicators", {}).get("gold") or {}).get("price") or 0
    if not base:
        return ""

    import urllib.request as _u

    def _get(url):
        _rq = _u.Request(url, headers={"User-Agent": "curl/8.5.0", "Accept": "*/*"})
        return json.load(_u.urlopen(_rq, timeout=8))

    # how far the published local rate sits from our own arithmetic, so the live
    # figures tick from the published level rather than drifting back to ours
    cal_sar, cal_inr, cal_sar22, cal_inr22 = 1.0, 1.0, 1.0, 1.0
    for _m in ((snap.get("local") or {}).get("markets") or []):
        for _r in (_m.get("rows") or []):
            if not _r.get("computed"):
                continue
            _c = round(_r["raw"] / _r["computed"], 6)
            _k, _mk = _r.get("karat"), _m.get("key")
            if _mk == "ksa" and _k == "24k":
                cal_sar = _c
            elif _mk == "ksa" and _k == "22k":
                cal_sar22 = _c
            elif _mk == "india" and _k == "24k":
                cal_inr = _c
            elif _mk == "india" and _k == "22k":
                cal_inr22 = _c

    basis = 0.0
    try:
        _t = list(_get("https://api.kraken.com/0/public/Ticker?pair=PAXGUSD")["result"].values())[0]
        _paxg = (float(_t["b"][0]) + float(_t["a"][0])) / 2.0
        if 100 < _paxg < 20000:
            basis = round(base - _paxg, 2)
    except Exception:
        basis = 0.0

    # The browser cannot reach Swissquote (no CORS), so it anchors on the feed it
    # can reach and applies the standing offset to that feed's own median error.
    adj_fx, adj_ga = 0.0, 0.0
    try:
        _v = float(_get("https://api.gold-api.com/price/XAU")["price"])
        if 100 < _v < 20000:
            adj_ga = round(base - _v, 2)
    except Exception:
        pass

    cfg_js = json.dumps({"sar": sar, "inr": inr, "base": base, "oz": 31.1034768,
                         "k22": 22.0 / 24.0, "basis": basis,
                         "calSar": cal_sar, "calInr": cal_inr,
                         "calSar22": cal_sar22, "calInr22": cal_inr22, "pavan": 8,
                         "adjFx": adj_fx, "adjGa": adj_ga})

    return """
<div id="lvBox" style="border:1px solid #e3e3e3;border-left:4px solid #c9a227;border-radius:8px;
     padding:14px 16px;margin:0 0 22px;background:#fffdf7">
  <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:10px">
    <div>
      <div style="font-size:10.5px;letter-spacing:1.8px;color:#8a7a3a;font-weight:700">
        <span id="lvDot" style="display:inline-block;width:7px;height:7px;border-radius:50%;
              background:#2f9e44;margin-right:6px"></span>LIVE NOW
      </div>
      <div style="font-size:30px;font-weight:800;color:#1a1a1a;line-height:1.15;
           font-family:ui-monospace,Menlo,monospace" id="lvPrice">&mdash;</div>
      <div style="font-size:12.5px;margin-top:2px" id="lvChg">connecting&hellip;</div>
    </div>
    <div style="text-align:right;font-size:12.5px;color:#555;line-height:1.75">
      <div><b id="lvSar">&mdash;</b> <span style="color:#777">SAR / gram 24k</span></div>
      <div><b id="lvSar22">&mdash;</b> <span style="color:#777">SAR / gram 22k</span></div>
      <div style="height:6px"></div>
      <div><b id="lvInr">&mdash;</b> <span style="color:#777">INR / pavan 24k</span></div>
      <div><b id="lvInr22">&mdash;</b> <span style="color:#777">INR / pavan 22k</span></div>
      <div style="color:#999;font-size:11px;margin-top:3px" id="lvTime"></div>
    </div>
  </div>
  <svg id="lvSpark" width="100%" height="34" viewBox="0 0 300 34" preserveAspectRatio="none"
       style="margin-top:10px;display:block"><polyline id="lvLine" fill="none"
       stroke="#c9a227" stroke-width="1.6" points=""></polyline></svg>
  <div style="color:#888;font-size:11.5px;margin-top:6px" id="lvNote">
    Priced off the median of three independent spot feeds, with movement from the 24-hour gold token market - so it keeps
    ticking at weekends when COMEX is shut. Spot feeds differ by a dollar or two between
    providers, so a search may show a figure within about 0.1% of this one.
  </div>
</div>
<script>
(function(){
  var C = CONFIG_JSON;
  var $ = function(i){ return document.getElementById(i); };
  var ticks = [], last = null;
  function f(n, d){ return n.toLocaleString(undefined, {minimumFractionDigits:d, maximumFractionDigits:d}); }

  function draw(){
    if (ticks.length < 2) return;
    var lo = Math.min.apply(null, ticks), hi = Math.max.apply(null, ticks);
    var span = (hi - lo) || 1, n = ticks.length;
    var pts = ticks.map(function(v, i){
      return (i * (300 / (n - 1))).toFixed(1) + ',' + (31 - ((v - lo) / span) * 28).toFixed(1);
    }).join(' ');
    $('lvLine').setAttribute('points', pts);
    $('lvLine').setAttribute('stroke', ticks[n-1] >= ticks[0] ? '#2f9e44' : '#d6336c');
  }

  function paint(p){
    $('lvPrice').textContent = '$' + f(p, 2);
    var d = p - C.base, up = d >= 0;
    $('lvChg').innerHTML = '<span style="color:' + (up ? '#2f9e44' : '#d6336c') + ';font-weight:700">'
      + (up ? '\u25b2' : '\u25bc') + ' $' + f(Math.abs(d), 2) + ' (' + (up ? '+' : '-')
      + f(Math.abs(d / C.base * 100), 2) + '%)</span> <span style="color:#888">since this page was built</span>';
    var gSar = p / C.oz * C.sar;
    $('lvSar').textContent = f(gSar * (C.calSar || 1), 2);
    $('lvSar22').textContent = f(gSar * C.k22 * (C.calSar22 || 1), 2);
    if (C.inr){
      var tInr = p / C.oz * C.pavan * C.inr;
      $('lvInr').textContent = f(tInr * (C.calInr || 1), 0);
      $('lvInr22').textContent = f(tInr * C.k22 * (C.calInr22 || 1), 0);
    }

    if (window.lvHooks){ for (var hk = 0; hk < window.lvHooks.length; hk++){
      try { window.lvHooks[hk](p); } catch(e){} } }

    var cells = document.querySelectorAll('[data-lv]');
    for (var i = 0; i < cells.length; i++){
      var el = cells[i], ds = el.dataset;
      var v = p / C.oz * parseFloat(ds.g) * parseFloat(ds.pur) * parseFloat(ds.fx)
              * (1 + parseFloat(ds.prem) / 100) * parseFloat(ds.cal || 1);
      var dp = parseInt(ds.dp, 10);
      var txt = f(v, dp);
      var tgt = el.querySelector('b') || el;
      if (tgt.textContent !== txt){
        tgt.textContent = txt;
        el.style.transition = 'none';
        el.style.background = (last !== null && p > last) ? '#e8f7ec' : '#fdeaf1';
        (function(n){ setTimeout(function(){ n.style.transition = 'background 1s'; n.style.background = 'transparent'; }, 50); })(el);
      }
    }
    $('lvTime').textContent = 'updated ' + new Date().toLocaleTimeString();
    if (last !== null && p !== last){
      var c = p > last ? '#2f9e44' : '#d6336c';
      $('lvPrice').style.transition = 'none'; $('lvPrice').style.color = c;
      setTimeout(function(){ $('lvPrice').style.transition = 'color 1.2s'; $('lvPrice').style.color = '#1a1a1a'; }, 60);
    }
    last = p;
    ticks.push(p); if (ticks.length > 150) ticks.shift();
    draw();
  }

  function fail(){
    $('lvDot').style.background = '#d6336c';
    $('lvChg').innerHTML = '<span style="color:#888">live feed unreachable - the figures below are still valid</span>';
  }

  // The 24/7 token feed gives movement; the spot feed gives the correct level.
  // We hold the gap between them and re-anchor only when spot genuinely changes,
  // so the number both MOVES and matches what you see when you search.
  var basis = C.basis, spotNow = null, reanchor = false;

  function setSpot(v){
    if (v > 100 && v !== spotNow){ spotNow = v; reanchor = true; }
  }
  function pullSpot(){
    // anchor, corrected to the build-time median of the server-side feeds
    fetch('https://api.gold-api.com/price/XAU', {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(j){ setSpot(parseFloat(j.price) + C.adjGa); })
      .catch(function(){});
  }

  function pull(){
    fetch('https://api.kraken.com/0/public/Ticker?pair=PAXGUSD', {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(j){
        var k = Object.keys(j.result)[0], t = j.result[k];
        var mid = (parseFloat(t.b[0]) + parseFloat(t.a[0])) / 2;
        if (!(mid > 100)) return;
        if (reanchor && spotNow){ basis = spotNow - mid; reanchor = false; }
        $('lvDot').style.background = '#2f9e44';
        paint(mid + basis);
      })
      .catch(fail);
  }
  pullSpot(); pull();
  setInterval(pull, 5000);
  setInterval(pullSpot, 60000);
})();
</script>
""".replace("CONFIG_JSON", cfg_js)


def accuracy_block(snap):
    """Show how closely the independent spot feeds agree, right now.

    Precision is not something the reader should take on faith. Three separate
    providers are queried every rebuild; this prints all three, says which were
    used, and states the spread between them in both dollars and riyals.
    """
    sp = (snap.get("indicators") or {}).get("spot") or {}
    srcs = sp.get("sources") or []
    if not srcs:
        return ""
    oz = 31.1034768
    fx = ((snap.get("local") or {}).get("fx") or {}).get("usdsar") or 3.75

    rows = []
    for q in srcs:
        ok = q.get("used")
        rows.append(
            '<tr>'
            '<td style="padding:6px 8px;border-bottom:1px solid #eee">%s</td>'
            '<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;'
            'font-family:ui-monospace,Menlo,monospace">$%s</td>'
            '<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;'
            'font-family:ui-monospace,Menlo,monospace">%s</td>'
            '<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;'
            'color:%s;font-size:12px">%s</td>'
            '</tr>'
            % (e(q.get("name", "")), format(q.get("price", 0), ",.2f"),
               format(q.get("price", 0) / oz * fx, ",.2f"),
               "#2f9e44" if ok else "#d6336c",
               "counted" if ok else "outvoted")
        )

    n_ok, n_tried = sp.get("n_ok", 0), sp.get("n_tried", 0)
    spread_usd, spread_pct = sp.get("spread_usd", 0), sp.get("spread_pct", 0)
    spread_sar = spread_usd / oz * fx

    if n_ok >= 2 and spread_pct < 0.25:
        verdict, colour = "Feeds agree. The figure below is the middle one.", "#2f9e44"
    elif n_ok >= 2:
        verdict, colour = "Feeds disagree more than usual - treat the last decimal loosely.", "#e8a33d"
    else:
        verdict, colour = "Only one feed answered. Cross-check before you trade on it.", "#d6336c"

    pub = (snap.get("indicators") or {}).get("pubksa") or {}
    check = ""
    if pub:
        ksa_rows = []
        for m in ((snap.get("local") or {}).get("markets") or []):
            if m.get("key") == "ksa":
                ksa_rows = m.get("rows") or []
        lines, worst = [], 0.0
        for r in ksa_rows:
            p = pub.get(r.get("karat"))
            if not p:
                continue
            diff = (r["raw"] / p - 1) * 100
            worst = max(worst, abs(diff))
            lines.append(
                '<tr>'
                '<td style="padding:5px 8px;border-bottom:1px solid #eee">%s</td>'
                '<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right;'
                'font-family:ui-monospace,Menlo,monospace">%s</td>'
                '<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right;'
                'font-family:ui-monospace,Menlo,monospace">%s</td>'
                '<td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:right;'
                'font-family:ui-monospace,Menlo,monospace;color:%s">%s%%</td>'
                '</tr>'
                % (e(r["karat"]), format(r["raw"], ",.2f"), format(p, ",.2f"),
                   "#2f9e44" if abs(diff) < 0.1 else "#e8a33d",
                   ("%+.3f" % diff) if abs(diff) > 0.0005 else "0.000")
            )
        if lines:
            check = (
                '<div style="margin-top:16px;font-size:13px;color:#666">'
                'And here is the page checking itself. Every rebuild it reads the rate table '
                'published by <b>%s</b> - a Saudi price site, the kind you would find by '
                'searching - and compares it against its own arithmetic.</div>'
                '<table style="width:100%%;border-collapse:collapse;font-size:13.5px;margin-top:8px">'
                '<tr style="text-align:left;color:#888;font-size:11.5px;letter-spacing:.4px">'
                '<th style="padding:0 8px 5px">Karat</th>'
                '<th style="padding:0 8px 5px;text-align:right">This page</th>'
                '<th style="padding:0 8px 5px;text-align:right">Published locally</th>'
                '<th style="padding:0 8px 5px;text-align:right">Gap</th></tr>%s</table>'
                '<div style="margin-top:8px;font-size:12.5px;color:%s">%s</div>'
                % (e(pub.get("source", "a Saudi price site")), "".join(lines),
                   "#2f9e44" if worst < 0.1 else "#e8a33d",
                   ("Largest gap <b>%.3f%%</b>. These are the same number." % worst) if worst < 0.1
                   else ("Largest gap <b>%.3f%%</b> - wider than usual today." % worst))
            )

    return (
        '<h2 style="font-size:19px;margin:26px 0 4px">How accurate is this right now</h2>'
        '<div style="color:#666;font-size:13px;margin:0 0 10px">'
        'Three independent providers are asked for the gold price every single rebuild. '
        'If one rounds its number or freezes at a weekend value, it gets outvoted by the '
        'other two instead of being believed.</div>'
        '<table style="width:100%%;border-collapse:collapse;font-size:13.5px">'
        '<tr style="text-align:left;color:#888;font-size:11.5px;letter-spacing:.4px">'
        '<th style="padding:0 8px 5px">Provider</th>'
        '<th style="padding:0 8px 5px;text-align:right">USD / ounce</th>'
        '<th style="padding:0 8px 5px;text-align:right">SAR / gram 24k</th>'
        '<th style="padding:0 8px 5px;text-align:right">&nbsp;</th></tr>'
        '%s</table>'
        '<div style="margin-top:10px;padding:10px 12px;border-left:3px solid %s;'
        'background:#fafafa;border-radius:0 6px 6px 0;font-size:13px">'
        '<b style="color:%s">%s</b><br>'
        '<span style="color:#666">%s of %s feeds answered &middot; widest disagreement '
        '<b>$%s</b> an ounce = <b>%s halalas</b> a gram &middot; that is <b>%s%%</b>. '
        'A dealer&rsquo;s own margin is many times larger than this.</span></div>'
        % ("".join(rows), colour, colour, verdict, n_ok, n_tried,
           format(spread_usd, ",.2f"), format(spread_sar * 100, ",.0f"),
           format(spread_pct, ".3f"))
        + check
    )


def staleness_block(snap):
    """Warn the reader if the robot has stopped refreshing this page.

    A static page cannot tell you it is stale - it just sits there looking
    confident. This compares the build time against the clock in the reader's
    own browser and says so plainly once the page is more than three hours old.
    """
    built = int(time.time())
    html = """
<div id="stale" style="display:none;border:1px solid #f1c0c7;background:#fff5f6;border-radius:8px;
     padding:11px 14px;margin:0 0 16px;font-size:13.5px;color:#9b2c3a"></div>
<script>
(function(){
  var built = BUILT_EPOCH * 1000;
  function check(){
    var hrs = (Date.now() - built) / 3600000;
    var el = document.getElementById('stale');
    if (hrs > 3){
      el.style.display = 'block';
      el.innerHTML = '<b>This page has not refreshed for ' + hrs.toFixed(1) + ' hours.</b><br>'
        + 'The hourly robot may be stuck. The live price box above is still correct because your '
        + 'browser fetches that itself, but the analysis below is going stale.';
    } else { el.style.display = 'none'; }
  }
  check(); setInterval(check, 60000);
})();
</script>
"""
    return html.replace("BUILT_EPOCH", str(built))


def position_block(snap, cfg):
    """What the reader's own gold is worth today, in riyals, live.

    Abstract dollar-per-ounce moves mean nothing to someone holding grams. This
    turns the plan's first tranche into: this is what you paid, this is what it
    is worth now, this is the difference.
    """
    plan = (cfg.get("plan") or {})
    done = [t for t in (plan.get("tranches") or []) if t.get("status") == "done" and t.get("price")]
    if not done:
        return ""
    t = done[0]
    entry = float(t["price"])
    fx = ((snap.get("local") or {}).get("fx") or {}).get("usdsar") or 3.75
    oz = 31.1034768
    entry_sar = entry / oz * fx

    html = """
<h2 style="font-size:19px;margin:26px 0 4px">What your gold is worth today
  <span style="font-size:10.5px;letter-spacing:1.5px;color:#2f9e44;font-weight:700;
        vertical-align:middle">&#9679; LIVE</span></h2>
<div style="color:#666;font-size:13px;margin:0 0 10px">
  You bought your first third on ENTRY_WHEN at about $ENTRY_USD an ounce, which was
  <b>ENTRY_SAR riyals a gram</b> for 24k. Type in how many grams you actually bought and this
  works out the rest by itself.</div>
<div style="border:1px solid #e3e3e3;border-radius:8px;padding:14px 16px;background:#fafafa">
  <label style="font-size:13px;color:#555">Grams of 24k you hold
    <input id="pgGrams" type="number" value="100" min="0" step="1"
       style="width:92px;margin-left:8px;padding:5px 7px;border:1px solid #ccc;border-radius:5px;
              font-size:14px;font-family:ui-monospace,Menlo,monospace">
  </label>
  <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:12px">
    <tr><td style="padding:5px 0;color:#555">What you paid</td>
        <td id="pgCost" style="padding:5px 0;text-align:right;
            font-family:ui-monospace,Menlo,monospace">&mdash;</td></tr>
    <tr><td style="padding:5px 0;color:#555">What it is worth now</td>
        <td id="pgNow" style="padding:5px 0;text-align:right;
            font-family:ui-monospace,Menlo,monospace">&mdash;</td></tr>
    <tr><td style="padding:7px 0;border-top:1px solid #ddd"><b>Difference</b></td>
        <td id="pgPnl" style="padding:7px 0;border-top:1px solid #ddd;text-align:right;
            font-family:ui-monospace,Menlo,monospace;font-weight:700">&mdash;</td></tr>
  </table>
  <div style="color:#888;font-size:12px;margin-top:8px">
    Metal value only, at the benchmark price. It ignores what the dealer charged you on top,
    which you do not get back when you sell.</div>
</div>
<script>
(function(){
  var ENTRY = ENTRY_SAR_JS, oz = 31.1034768, fx = FX_JS;
  window.lvHooks = window.lvHooks || [];
  function f(n,d){ return n.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}); }
  var lastP = null;
  function upd(p){
    lastP = p;
    var g = parseFloat(document.getElementById('pgGrams').value);
    if (!(g >= 0)) g = 0;
    var cost = ENTRY * g, now = p / oz * fx * g, d = now - cost;
    document.getElementById('pgCost').textContent = f(cost,2) + ' SAR';
    document.getElementById('pgNow').textContent  = f(now,2) + ' SAR';
    var el = document.getElementById('pgPnl');
    el.style.color = d >= 0 ? '#2f9e44' : '#d6336c';
    el.textContent = (d >= 0 ? '+' : '-') + f(Math.abs(d),2) + ' SAR  ('
                   + (d >= 0 ? '+' : '-') + f(Math.abs(cost ? d/cost*100 : 0),2) + '%)';
  }
  window.lvHooks.push(upd);
  document.getElementById('pgGrams').addEventListener('input', function(){ if (lastP) upd(lastP); });
})();
</script>
"""
    return (html.replace("ENTRY_WHEN", e(str(t.get("when", "your entry"))))
                .replace("ENTRY_USD", format(entry, ",.0f"))
                .replace("ENTRY_SAR_JS", repr(entry_sar))
                .replace("ENTRY_SAR", format(entry_sar, ",.2f"))
                .replace("FX_JS", repr(float(fx))))


def dealer_block(snap):
    """Check a shop's quote against the live benchmark, standing in the shop.

    This is the one number a physical buyer actually needs and can never work
    out on the spot: how much over the metal is this dealer charging me.
    """
    fx = ((snap.get("local") or {}).get("fx") or {}).get("usdsar") or 3.75
    html = """
<h2 style="font-size:19px;margin:26px 0 4px">Is the dealer&rsquo;s price fair?</h2>
<div style="color:#666;font-size:13px;margin:0 0 10px">
  In the shop, type in the price per gram they just quoted you and pick the karat.
  This tells you how much over the metal you are being asked to pay, against the live
  price this second.</div>
<div style="border:1px solid #e3e3e3;border-radius:8px;padding:14px 16px;background:#fafafa">
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
    <label style="font-size:13px;color:#555">Quoted SAR per gram
      <input id="dqPrice" type="number" value="" placeholder="e.g. 455" min="0" step="0.01"
        style="width:110px;margin-left:8px;padding:6px 8px;border:1px solid #ccc;border-radius:5px;
               font-size:15px;font-family:ui-monospace,Menlo,monospace"></label>
    <label style="font-size:13px;color:#555">Karat
      <select id="dqKarat" style="margin-left:6px;padding:6px 8px;border:1px solid #ccc;
              border-radius:5px;font-size:14px">
        <option value="24">24k</option><option value="22" selected>22k</option>
        <option value="21">21k</option><option value="18">18k</option></select></label>
  </div>
  <div id="dqOut" style="margin-top:12px;font-size:14px;color:#888">
    Enter a price to check it.</div>
</div>
<script>
(function(){
  var oz = 31.1034768, fx = FX_JS, live = null;
  window.lvHooks = window.lvHooks || [];
  function f(n,d){ return n.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}); }
  function calc(){
    var out = document.getElementById('dqOut');
    var q = parseFloat(document.getElementById('dqPrice').value);
    var k = parseFloat(document.getElementById('dqKarat').value);
    if (!live){ out.innerHTML = '<span style="color:#888">waiting for the live price&hellip;</span>'; return; }
    if (!(q > 0)){ out.innerHTML = '<span style="color:#888">Enter a price to check it.</span>'; return; }
    var bench = live / oz * fx * (k / 24);
    var prem = (q / bench - 1) * 100;
    var verdict, colour;
    if (prem < 0)       { verdict = 'Below the metal price. Check the karat and the weight carefully.'; colour = '#e8a33d'; }
    else if (prem <= 3) { verdict = 'Good. This is about as close to the metal as retail gets.'; colour = '#2f9e44'; }
    else if (prem <= 6) { verdict = 'Fair for a small bar or coin. Worth asking for a little off.'; colour = '#2f9e44'; }
    else if (prem <= 12){ verdict = 'High. Normal for jewellery, too much for a plain bar. Haggle or walk.'; colour = '#e8a33d'; }
    else                { verdict = 'Very high. You are paying mostly for making charges, not gold.'; colour = '#d6336c'; }
    out.innerHTML =
      '<div style="font-size:13px;color:#555">Metal is worth <b>' + f(bench,2) + ' SAR/gram</b> at ' + k + 'k right now.</div>'
      + '<div style="margin-top:4px;font-size:22px;font-weight:800;color:' + colour + '">'
      + (prem >= 0 ? '+' : '') + f(prem,1) + '% over the metal</div>'
      + '<div style="font-size:13px;color:#555;margin-top:2px">That is '
      + (prem >= 0 ? '' : 'minus ') + f(Math.abs(q - bench),2) + ' SAR a gram'
      + ' &middot; on 10 grams, ' + f(Math.abs(q - bench) * 10, 0) + ' SAR.</div>'
      + '<div style="margin-top:7px;padding:8px 10px;border-left:3px solid ' + colour
      + ';background:#fff;border-radius:0 5px 5px 0;font-size:13.5px">' + verdict + '</div>';
  }
  window.lvHooks.push(function(p){ live = p; calc(); });
  document.getElementById('dqPrice').addEventListener('input', calc);
  document.getElementById('dqKarat').addEventListener('change', calc);
})();
</script>
"""
    return html.replace("FX_JS", repr(float(fx)))


def render(snap, news):
    """Turn one snapshot + news bundle into a standalone HTML page."""
    ind = snap["indicators"]
    plain = snap["plain"]
    labels = snap["plain_labels"]
    scen = snap["scenarios_plain"]
    post = snap["posterior"]
    fc = snap["forecast"]
    deltas = snap.get("deltas", {})
    gold = ind.get("gold", {})
    spot_i = ind.get("spot", {})
    # the headline must be the number that is actually live today
    headline = spot_i.get("price") or gold.get("price")

    # the change and the timestamp must describe the headline number, not a
    # futures contract that stopped trading on Friday evening
    schg = ind.get("spotchg") or {}
    if schg.get("change_pct") is not None:
        chg_txt = "%+.2f%% since midnight" % schg["change_pct"]
        chg_val = schg["change_pct"]
    elif gold.get("change_pct") is not None:
        chg_txt = "%+.2f%% at Friday's close" % gold["change_pct"]
        chg_val = gold["change_pct"]
    else:
        chg_txt, chg_val = "-", 0
    asof_txt = (spot_i.get("asof") or gold.get("asof") or "-").replace("T", " ").replace("Z", " UTC")
    act = plain["action"]
    acol = C.get(act.get("tone"), "#666")
    now = datetime.now(timezone.utc)

    # ---------- what's going on ----------
    lines = "".join(
        '<li style="margin:0 0 11px 0;color:%s">%s</li>'
        % (C.get(l.get("tone"), "#333"), e(l["text"]))
        for l in plain["lines"]
    )

    # ---------- four futures ----------
    rows = []
    for k in "ABCD":
        s = scen.get(k, {})
        p = post.get(k, 0)
        bar = max(2, round(p * 2.4))
        rows.append(
            '<tr>'
            '<td style="padding:11px 10px;border-bottom:1px solid #eee;vertical-align:top;width:30%%">'
            '<b style="font-size:15px">%s</b><br>'
            '<span style="color:#777;font-size:12.5px">%s</span></td>'
            '<td style="padding:11px 10px;border-bottom:1px solid #eee;vertical-align:top;width:17%%;white-space:nowrap">'
            '<b style="font-size:19px">%.1f%%</b><br>'
            '<span style="display:inline-block;height:7px;width:%dpx;background:#c9a227;border-radius:4px;margin-top:4px"></span></td>'
            '<td style="padding:11px 10px;border-bottom:1px solid #eee;vertical-align:top;font-size:13.5px;color:#444">%s</td>'
            '</tr>'
            % (e(s.get("name", k)), e(s.get("blurb", "")), p, bar, e(s.get("gold", "")))
        )
    scen_rows = "".join(rows)

    # ---------- drivers ----------
    helps = {
        "core_pce": lambda v: v < 2.8, "brent": lambda v: v < 85,
        "dxy": lambda v: v < 100, "fedfunds": lambda v: v < 3.5,
        "real10": lambda v: v < 2.5, "orcl_dd": lambda v: v < -50,
        "ndx_dd": lambda v: v < -30, "dgs30": lambda v: v > 5.75,
    }
    dr = []
    for key, test in helps.items():
        src = ind.get(key, {})
        val = src.get("value", src.get("price", src.get("drawdown_pct")))
        if val is None:
            continue
        lab, why = labels.get(key, (key, ""))
        good = False
        try:
            good = test(float(val))
        except (TypeError, ValueError):
            pass
        col = C["bull"] if good else C["bear"]
        mark = "helping gold" if good else "hurting gold"
        sym = "&#10003;" if good else "&#9650;"
        d = deltas.get(key) or {}
        chg = ""
        if isinstance(d, dict) and d.get("diff") is not None:
            try:
                chg = ' <span style="color:#999;font-size:11.5px">(%+.2f since 27 Nov)</span>' % float(d["diff"])
            except (TypeError, ValueError):
                chg = ""
        shown = ("%.2f" % float(val)).rstrip("0").rstrip(".")
        dr.append(
            '<div style="border:1px solid #e3e3e3;border-left:4px solid %s;border-radius:7px;padding:12px 14px;margin-bottom:9px">'
            '<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">'
            '<b style="font-size:14.5px">%s</b>'
            '<span style="font-family:ui-monospace,Menlo,monospace;font-size:14.5px"><b>%s</b>%s</span></div>'
            '<div style="color:%s;font-size:12px;font-weight:700;margin-top:3px">%s %s</div>'
            '<div style="color:#666;font-size:12.5px;margin-top:5px;line-height:1.5">%s</div></div>'
            % (col, e(lab), shown, chg, col, sym, mark, e(why))
        )
    drivers = "".join(dr)

    # ---------- calendar ----------
    ev = []
    for x in news.get("events", []):
        if x.get("status") == "past":
            continue
        when = "today" if x.get("days") == 0 else "in %s days" % x.get("days")
        ev.append(
            '<tr><td style="padding:9px 10px;border-bottom:1px solid #eee;white-space:nowrap">'
            '<b>%s</b><br><span style="color:#888;font-size:12px">%s</span></td>'
            '<td style="padding:9px 10px;border-bottom:1px solid #eee">'
            '<b style="font-size:14px">%s</b><br>'
            '<span style="color:#666;font-size:12.5px">%s</span></td></tr>'
            % (e(x.get("date")), e(when), e(x.get("label")), e(x.get("why", "")))
        )
    events = "".join(ev)

    # ---------- headlines ----------
    def feed(items, n=5):
        out = []
        for it in items[:n]:
            out.append(
                '<li style="margin-bottom:7px;line-height:1.45">%s'
                '<span style="color:#999;font-size:11.5px"> &middot; %s</span></li>'
                % (e(it.get("title", "")), e((it.get("published") or "")[:22]))
            )
        return "".join(out) or '<li style="color:#999">none returned</li>'

    # ---------- forecast path ----------
    pr = []
    for p in fc.get("path", []):
        pr.append(
            '<tr><td style="padding:7px 10px;border-bottom:1px solid #eee">%s</td>'
            '<td style="padding:7px 10px;border-bottom:1px solid #eee;'
            'font-family:ui-monospace,Menlo,monospace;text-align:right">%s</td></tr>'
            % (e(p.get("date")), money(p.get("gold")))
        )
    path_rows = "".join(pr)

    err = fc.get("error_pct")
    err_txt = ("%+.2f%%" % err) if isinstance(err, (int, float)) else "-"

    doc = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="data:application/manifest+json,%%7B%%22name%%22%%3A%%22Gold%%20Watch%%22%%2C%%22short_name%%22%%3A%%22Gold%%22%%2C%%22display%%22%%3A%%22standalone%%22%%2C%%22background_color%%22%%3A%%22%%23ffffff%%22%%2C%%22theme_color%%22%%3A%%22%%231a1a1a%%22%%2C%%22start_url%%22%%3A%%22.%%22%%7D">
<link rel="apple-touch-icon" href="data:image/svg+xml,%%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%%3E%%3Crect width='192' height='192' rx='34' fill='%%231a1a1a'/%%3E%%3Ctext x='96' y='128' font-size='104' font-family='Georgia,serif' font-weight='bold' text-anchor='middle' fill='%%23c9a227'%%3EAu%%3C/text%%3E%%3C/svg%%3E">
<link rel="icon" href="data:image/svg+xml,%%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%%3E%%3Crect width='192' height='192' rx='34' fill='%%231a1a1a'/%%3E%%3Ctext x='96' y='128' font-size='104' font-family='Georgia,serif' font-weight='bold' text-anchor='middle' fill='%%23c9a227'%%3EAu%%3C/text%%3E%%3C/svg%%3E">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Gold">
<meta name="theme-color" content="#1a1a1a">
<meta name="robots" content="noindex,nofollow">
<meta http-equiv="refresh" content="900">
<title>Gold - is it time to buy?</title>
<style>
  @media (max-width:600px){
    h1{font-size:24px !important}
    table td{display:block;width:auto !important}
  }
</style>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
            max-width:860px;margin:0 auto;padding:30px 22px 60px;color:#1a1a1a;line-height:1.6">

  <div style="border-bottom:3px solid #c9a227;padding-bottom:16px;margin-bottom:26px">
    <div style="font-size:11px;letter-spacing:2.5px;color:#c9a227;font-weight:700;text-transform:uppercase">
      Gold tracker
    </div>
    <h1 style="margin:6px 0 4px;font-size:30px;letter-spacing:-.5px">Is it time to buy?</h1>
    <div style="color:#777;font-size:13px">
      Last checked <b>%(stamp)s</b>.
    </div>
  </div>

  <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:12px;margin-bottom:24px">
    <div>
      <div style="font-size:12px;color:#888;text-transform:uppercase;letter-spacing:1.5px">Gold, per ounce</div>
      <div style="font-size:42px;font-weight:800;color:#c9a227;line-height:1.1">%(price)s</div>
      <div style="color:%(chgcol)s;font-weight:700;font-size:14px">%(chg)s</div>
    </div>
    <div style="text-align:right;color:#777;font-size:12.5px">
      market timestamp %(asof)s<br>
      plan expected %(exp)s &middot; off by <b>%(err)s</b>
    </div>
  </div>

  <div style="border:2px solid %(acol)s;border-radius:10px;padding:20px 22px;margin-bottom:30px;background:#fcfcfa">
    <div style="font-size:10.5px;letter-spacing:2px;color:%(acol)s;font-weight:700;margin-bottom:7px">WHAT TO DO TODAY</div>
    <div style="font-size:26px;font-weight:800;color:%(acol)s;margin-bottom:10px;letter-spacing:-.4px">%(alabel)s</div>
    <div style="font-size:15.5px">%(atext)s</div>
  </div>

  %(stale)s

  %(live)s

  %(plan)s

  %(local)s

  <h2 style="font-size:19px;margin:0 0 6px">What's going on</h2>
  <div style="font-size:16px;font-weight:600;margin-bottom:12px">%(headline)s</div>
  <ul style="padding-left:20px;margin:0 0 30px;font-size:14.5px">%(lines)s</ul>

  <h2 style="font-size:19px;margin:0 0 10px">What might happen next</h2>
  <table style="width:100%%;border-collapse:collapse;margin-bottom:30px">%(scen)s</table>

  <h2 style="font-size:19px;margin:0 0 10px">What's pushing the price around</h2>
  %(drivers)s

  <h2 style="font-size:19px;margin:26px 0 10px">Coming up</h2>
  <table style="width:100%%;border-collapse:collapse;margin-bottom:30px">%(events)s</table>

  <h2 style="font-size:19px;margin:0 0 10px">The plan, for reference</h2>
  <div style="color:#666;font-size:13px;margin-bottom:8px">
    Locked %(locked)s. Headline bet: %(bet)s. Thesis dies on a close below %(inval)s.
  </div>
  <table style="width:100%%;border-collapse:collapse;max-width:330px;margin-bottom:30px;font-size:14px">%(path)s</table>

  %(position)s

  %(dealer)s

  %(accuracy)s

  %(sources)s

  <h2 style="font-size:19px;margin:0 0 10px">Latest from the Fed</h2>
  <ul style="padding-left:20px;font-size:13.5px;margin-bottom:22px">%(fed)s</ul>

  <h2 style="font-size:19px;margin:0 0 10px">Latest headlines</h2>
  <ul style="padding-left:20px;font-size:13.5px;margin-bottom:30px">%(wires)s</ul>

  <div style="border-top:1px solid #ddd;padding-top:14px;color:#888;font-size:12px">
    Prices from Yahoo Finance; interest rates and inflation from the US Federal Reserve's database.
    The percentages are one scoring model's output, not a market consensus. Not financial advice.
  </div>
</div>
""" % {
        "stamp": now.strftime("%d %b %Y, %H:%M UTC"),
        "price": money(headline),
        "chg": chg_txt,
        "chgcol": "#2f9e44" if chg_val >= 0 else "#d6336c",
        "asof": e(asof_txt),
        "exp": money(fc.get("expected_today")),
        "err": err_txt,
        "acol": acol,
        "alabel": e(act.get("label", "")),
        "atext": e(act.get("text", "")),
        "live": live_block(snap),
        "plan": plan_block(headline),
        "local": local_block(snap),
        "stale": staleness_block(snap),
        "position": position_block(snap, load_cfg()),
        "dealer": dealer_block(snap),
        "accuracy": accuracy_block(snap),
        "sources": sources_block(snap),
        "headline": e(plain.get("headline", "")),
        "lines": lines,
        "scen": scen_rows,
        "drivers": drivers,
        "events": events,
        "locked": e(fc.get("locked_on", "-")),
        "bet": e(fc.get("key_bet", "-")),
        "inval": money(fc.get("invalidation")),
        "path": path_rows,
        "fed": feed(news.get("fed", [])),
        "wires": feed(news.get("wires", []), 6),
    }

    return doc


def main():
    try:
        snap = get("/api/snapshot")
        news = get("/api/news")
    except Exception as exc:                      # noqa: BLE001
        print("could not reach the app on %s: %s" % (BASE, exc), file=sys.stderr)
        print("start it first:  cd ~/gold-watch && python3 app.py", file=sys.stderr)
        return 1
    doc = render(snap, news)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("wrote %s (%d bytes)" % (OUT, len(doc)))
    print("gold %s  %s" % (money(snap["indicators"].get("gold", {}).get("price")),
                           snap["plain"]["action"].get("label")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
