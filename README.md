# Gold Macro Watch

Live tracker for the gold thesis locked on 2026-11-27. Pulls real market data,
re-scores the four scenarios on every refresh, and tracks the forecast against reality.

## Run it

```bash
cd ~/gold-watch
python3 app.py          # binds 0.0.0.0:8000
```

Open port 8000. The page refreshes itself every 60 seconds.

## What updates by itself

| Indicator | Source | Refresh |
|---|---|---|
| Gold, silver, Brent, DXY | Yahoo Finance | 5 min |
| Nasdaq 100, Nvidia, Oracle, GLD | Yahoo Finance | 5 min |
| 30y / 10y / 2y Treasury, 10y real yield | FRED | 1 hour |
| Fed funds effective | FRED | 1 hour |
| Core PCE y/y (computed from index) | FRED | 1 hour |

Drawdowns are computed against the actual trailing peak, so "% from peak"
stays correct as new highs or lows print — no hardcoded reference levels.

## What you maintain by hand

No free feed exists for these. Edit them in the UI (bottom table) or in
`config.json`; the engine re-scores immediately and shows the "as of" date so
you can see when a number has gone stale.

- Oracle 5y CDS — the scenario B trigger (400bp)
- CoreWeave 2031 bond yield — weakest link in AI financing
- Central bank buying per quarter — the floor under the price (WGC, quarterly)
- GLD tonnage
- December FOMC hike odds

## The scenario engine

Starts from the priors set on 27 Nov (A 35 / B 30 / C 25 / D 10), then each live
indicator applies a log-odds nudge based on which band it falls in. Results are
softmaxed back to probabilities. Every contribution is shown in the
"How each signal is voting" table — nothing is hidden.

Tune the bands in `evaluate_signals()` and the trigger levels in
`config.json → thresholds`.

## Forecast tracking

`config.json → forecast.path` holds the locked waypoints. The app interpolates
to today, compares against live spot, and reports the error. Change the path
only if you intend to admit the original call was wrong — that's the point of
locking it.

## The 27 Nov baseline

`config.json → baseline` holds a frozen snapshot of every tracked value on the
day the forecast was made. The dashboard compares live data against it in three
places: the strip at the top, a "vs 27 Nov" line on every indicator tile, and
the drift shown on each scenario card.

All deltas read zero on day one — that is correct, not a bug. They accumulate.

To re-baseline (only if you deliberately want a new reference point):

```bash
python3 -c "
import sys,json; sys.path.insert(0,'.')
import app as A
s=A.build_snapshot(); i=s['indicators']
f={'gold':'price','silver':'price','brent':'price','dxy':'price',
   'ndx':'drawdown_pct','orcl':'drawdown_pct','nvda':'drawdown_pct'}
c=json.load(open('config.json'))
c['baseline']={'date':__import__('datetime').date.today().isoformat(),
  'note':'re-baselined',
  'values':{k:(i.get(k) or {}).get(f.get(k,'value')) for k in c['baseline']['values']},
  'posterior':s['posterior']}
json.dump(c,open('config.json','w'),indent=2); print('re-baselined')
"
```

## Does it keep updating on its own?

**While the process is running — yes.**

| Layer | Cadence |
|---|---|
| Browser page refresh | 60 s |
| Market prices (Yahoo) | 5 min |
| Rates & inflation (FRED) | 1 hour |
| History snapshot written to disk | 15 min |

The history worker runs on its own thread, so rows keep accumulating whether
or not anyone has the page open.

**When the process stops — no.** This is the honest limit. A background process
does not survive the sandbox being recycled. Your *files* persist
(`history.jsonl`, `config.json`, `.cache.json`), the *process* does not.

To pick it back up:

```bash
cd ~/gold-watch && python3 app.py
```

History resumes appending to the same file, so the record survives the gap —
you just lose the snapshots during the downtime.

**For genuine 24/7**, deploy it off the sandbox. It's a stock Flask app with two
dependencies (`flask`, `requests`), so a free tier on Render, Railway or Fly
works with no code changes. Set the port from `$PORT` if the host requires it.

**What never updates itself:** the five hand-maintained inputs. No free feed
exists for CDS spreads or central-bank tonnage. They display their "as of" date
precisely so a stale number can't quietly masquerade as a live one.

## Gotchas worth knowing

- **FRED blocks browser User-Agents.** It serves `curl/8.5.0` instantly but drops
  `Mozilla/...` from a non-browser TLS client. Yahoo needs the opposite. Hence
  the two session profiles in `session()`.
- **Never share a `requests.Session` across threads** — the connection pool
  deadlocks. One session per thread, per profile.
- Responses are cached to `.cache.json`, so a restart is instant and the
  dashboard survives a provider outage by serving the last good value.
