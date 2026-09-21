# Market-data reliability

The site retains successful market data in `.cache/market.sqlite` and successful
published JSON in `.cache/site-data`. GitHub Actions restores/saves `.cache`
between runs. The cache is excluded from the Pages artifact; only explicit site
files, assets, and public JSON are staged in `_site`.

## Refresh policy

| Data | Refresh policy |
| --- | --- |
| Daily OHLCV | Once per completed NYSE session, after a 30-minute settlement buffer |
| Price history | Ten-bar overlap; rebuild for changed corporate actions and every 30 days |
| Company profile | Stable per-symbol interval of 1–28 days |
| Fundamental metrics | Stable per-symbol interval of 1–7 days |
| Analyst data | Stable per-symbol interval of 1–3 days |
| Quarterly statements | Stable per-symbol interval of 1–7 days; daily near the reported earnings date |
| Option expiry discovery | Daily |
| Option quotes | Six hours; only for the screened shortlist |

Yahoo bundles profiles, metrics and analyst data in `.info`. Independently
scheduled groups share a one-day `.info` response cache. Fetching three groups
never requires three `.info` requests on the same day. Rotating intervals reduce
bursts, but do not guarantee a specific reduction because groups share that
endpoint. Scanner enrichment reuses these caches and does not request statements.

Yahoo bars remain per-symbol requests. Shorter incremental windows reduce bytes
and processing, not necessarily request count. One worker serializes high-level
yfinance calls; yfinance can issue multiple HTTP requests inside a call. The
operation budget is therefore **not** a claim about Yahoo's actual rate limit.

## Controls

- `MARKET_CACHE`: SQLite path (default `.cache/market.sqlite`).
- `YAHOO_MIN_INTERVAL`: seconds between operations (default 1).
- `YAHOO_DAILY_OPERATIONS`: shared UTC-day operation budget (default 5,000).
- `SCAN_MAX_SYMBOLS`: symbols attempted per scanner run (default 1,500).

Prices for the main universe run first, then its fundamentals, then options, then
the broader scanner. The scanner rotates a persistent cursor to prevent the
same end of the universe being skipped every day. Its `coverage` reports the
universe, attempted/current symbols and whether results are partial. This
conservative Yahoo mode is **not a complete daily market-wide scan** when the
universe exceeds the limit. Increasing the limit can increase throttling; use a
true bulk provider for complete daily coverage (see below).

Transient failures retry at most three times with exponential backoff and jitter.
An exposed `Retry-After` is honored; longer waits defer work. A returned bar behind the expected session is retained with a 15-minute retry
delay instead of repeatedly downloaded. Three consecutive
rate limits open a 15-minute provider circuit. Failures are also negatively cached
for 15 minutes (24 hours for explicit not-found responses). Budgets, cooldowns and
cached successes survive process restarts. Run collectors sequentially; the
SQLite gateway is not intended to coordinate concurrent collection processes.

## Failure behavior

- A failed refresh does not erase successful cached data or its observation date.
- Null fields in a partial fundamentals response retain prior values, with
  `retained_fields` and per-field `field_updated_at` metadata.
- Failed price updates preserve the last available signal. Age does not erase ratings in the browser.
- Failed option refreshes expose `historical_contracts` separately from current
  contracts. Their prices, Greeks and underlying price belong to that snapshot.
- A successfully scanned universe with zero matches publishes an empty result.
  A wholly failed scan preserves the preceding dated result.
- Only explicit `--sample` creates synthetic data. Live options refuse sample
  inputs. The production publisher refuses to deploy without a real stock dataset
  and replaces optional sample fixtures with an explicit unavailable state.
- `data/health-{stocks,options,scanner}.json` and the `data-health` Actions artifact
  report cache results and error categories. Missing fields are classified as
  unavailable after a successful response, or carry the acquisition failure.
  Empty responses do not prove that an instrument is unsupported.

A green deployment is not a guarantee of complete data. Inspect health reports,
coverage, observation dates and failed-refresh banners. No live provider can
supply a P/E for every company or LEAP expiries for every instrument.

## Deployment and recovery

Scheduled runs and manual runs with **refresh** enabled collect data. Ordinary
pushes restore and deploy the saved snapshot without calling Yahoo. On initial
installation, run **Update data & deploy to Pages** manually with refresh enabled
to establish a real snapshot. A push before that will fail safely instead of
publishing committed sample fixtures.

Actions caches are an acceleration/persistence mechanism with eviction, not a
permanent database. If evicted, bootstrap again with a manual refresh. For a
long-lived production service, move the SQLite file/snapshots to durable object
storage or a persistent worker volume. Preserve the gateway's sequential access
or replace it with transactional distributed coordination when scaling workers.

## Bulk-provider evaluation (2026-09-10)

1. **Massive full-market snapshots / stock flat files:** a full-market snapshot
   includes daily bars for over 10,000 tickers in one response. Daily stock flat
   files offer an alternative for scheduled acquisition. These suit maintaining
   a rolling volume history across the market without per-ticker Yahoo calls.
   A snapshot alone does not supply the prior 50 sessions: bootstrap historical
   data first and confirm session/adjustment semantics before computing RVOL.
   Sources: [Full market snapshot](https://www.massive.com/docs/rest/stocks/snapshots/full-market-snapshot),
   [Stock flat files](https://massive.com/docs/flat-files/stocks/overview).
2. **Alpaca multi-symbol bars:** one request accepts multiple symbols, with
   pagination. Follow `next_page_token` until exhausted; a page can contain only
   the first symbol's bars. Use a consistent feed throughout the volume baseline:
   IEX-only and consolidated exchange volume are not interchangeable. Verify
   historical-feed entitlement, delay and public-display rights for the chosen
   plan. Sources: [Historical bars](https://docs.alpaca.markets/us/reference/stockbars),
   [Market Data API](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api).

Recommendation: choose a bulk end-of-day feed for the scanner after measuring
coverage with the new reports. Keep provider-specific bar caches separate and
validate ticker mappings, split adjustments, volume and session timestamps before
migration. No paid provider is enabled by this change; credentials, feed choice
and display entitlement are needed first. Prices and contractual limits were not
assumed or hard-coded.

## Validation

```sh
python -m unittest discover -s tests -v
node tests/test_data_quality.cjs
```

Tests mock Yahoo calls: failures, partial responses, shared quotas, cooldowns,
cache restart, incremental history, corporate actions, scanner zero-results,
options history, sample rejection and snapshot restoration. They do not measure
live Yahoo availability or promise a particular coverage percentage.


## Refresh schedule

Price snapshots are scheduled weekdays at **10:30AM and 1:00PM Eastern**.
The workflow uses `America/New_York`, keeping these times through daylight saving
changes. These are collection start times; publishing follows collection and
GitHub schedules can start late.

Other weekday UTC schedules: 11:17 daily-price recovery,
21:47 completed-session stocks/options/scanner, and 23:17 recovery. During daylight
saving these are 7:17AM, 5:47PM, and 7:17PM Eastern; winter is one hour
earlier. Recovery reuses company information and skips earnings discovery and the
broad scanner to prioritize daily prices and options.
Manual runs select full, recovery, signals, or quotes. Pushes deploy cached data.

Quote jobs use 5-minute regular-session bars, at most 800 symbols per run ordered
by oldest quote, and reserve 2,000 of the 5,000 daily high-level operations for
other work. These are delayed snapshots, not real-time quotes. Retries also consume
budget. Completed-session signal values are never recalculated from intraday quotes.
The broad scanner rotates 750 symbols on the full evening run only.

The screener contains the Robinhood and S&P 500 core lists. Market-wide earnings
discovery and earnings-only membership are disabled. Cached-only deployments also
remove old earnings-only stocks and their detail/LEAP entries before publishing.

## Afternoon signals and display

At 3:45PM Eastern each weekday, the signals job updates daily price history,
technicals and ratings together, using the current session’s available daily bar.
It reuses company information and publishes without waiting for options or the
broad scanner. This is a pre-close calculation, not an official closing signal.
An evening refresh can replace it with the completed session. Partial-session
history is explicitly re-fetched by the completed-session job. GitHub can delay starts.

The UI preserves saved ratings and option information regardless of age and omits
freshness badges, snapshot notices and expiry-based overrides. Acquisition metadata
remains in the JSON for diagnosis. Missing data remains missing.
