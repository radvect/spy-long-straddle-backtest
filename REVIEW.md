# Review & Audit Notes

Independent review of the project before presentation: what was checked,
what was found, what was fixed, what remains. Dates: audit performed
2026-01; a second audit round was performed 2026-10-29 (see §7). All
statements below were actually executed, not assumed.

## 1. Question under investigation: why does the equity curve go flat?

The dashboard's equity curve becomes a flat line after mid-2022 (the
previously-observed level was ~$729; after the fix in §2 below the flat
level is ~$146). Investigation:

1. Replayed the engine's entry-day affordability check day by day on the
   real data for the no-trade gap (cash after the last settlement, each
   candidate expiry's entry cost).
2. Found: after the final settlement, cash = $1,511 (pre-fix $729). Every
   subsequent candidate straddle costs ~$1,200–$2,700; the documented
   guard `entry_cost <= cash` therefore blocks all entries. The one
   affordable candidate in the gap (2023-02-17 expiry, cost $1,475.69,
   cash $1,511.21) was in fact taken and is the last trade. After that
   settlement cash = $146.47 — below every future entry cost forever.

**Conclusion: the flat line is the documented insufficient-capital
behavior, not an implementation error.** The strategy, under its
documented assumptions, is a net loser that exhausts $10k in ~2–3 years
and then sits flat. Results were **not** altered to make it look
profitable; the flat segment is displayed and explained as-is.

## 2. Bugs found and fixed

| # | Bug | Fix | Verified |
|---|-----|-----|----------|
| 1 | **Dividend double-count (data)** — `fetch_data` used yfinance's default `auto_adjust` (effectively `True` → dividend-adjusted Close), contradicting the module docstring and double-counting dividends (already modeled via *q* in Black–Scholes). Empirically confirmed (e.g. 2025-12-09: adjusted 675.75 vs unadjusted 683.04). | Explicit `auto_adjust=False` for both SPY and VIX in `backtest.py`. | `python backtest.py` self-tests pass; full test suite passes; the strategy's results shift (25→23 trades, −92.7%→−98.5%) as the correct consequence of the documented intent. |
| 2 | **Settlement only on exact date match** — `date == expiration` would leave a position unsettled (and the final equity ≠ cash invariant broken) if an expiration ever fell on a non-trading day. Latent, never triggered on real data (CBOE monthlies always expire on trading days; the Good-Friday exception is already handled). | Guard changed to `date >= open_trade["expiration"]` with a comment explaining equivalence on real data. | No behavior change on real data (identical trade list); covered by the "final equity = final cash" and "no open position at end" checks. |

Not a bug, but worth knowing: the trade table stores 2-decimal-rounded
`entry_cost` / `exit_value` / `net_pnl`. Recomputing net P&L from the
*rounded* columns deviates by up to 1 cent per trade — the engine's
full-precision accounting identity is exact (verified to <1e-9).

## 3. Tests performed and results

Command: `python -m pytest test_backtest.py test_app.py -v`
(Python 3.12.10, pytest 9.1.1, pandas 3.1.0, numpy 2.5.3, scipy 1.18.1,
plotly 7.1.0, Streamlit 1.66.1, yfinance 1.7.0.)

### `test_backtest.py` — 18 tests, all PASSED

| Area | Test | Result |
|------|------|--------|
| BS vs known references | S=K=100, T=1y, σ=20%, r=5%, q=0 → call 10.4506 / put 5.5735 | pass |
| Expiration payoffs | OTM/ITM/ATM intrinsic values, near-expiry continuity | pass |
| Put–call parity | 3 parameter sets, tolerance 1e-8 | pass |
| Limits | zero-vol → forward intrinsic; deep-ITM call → forward spread | pass |
| Contract months | known 2020 dates; Good-Friday exception (Apr 2022, Apr 2025); inclusive bounds | pass |
| Strike selection | nearest-$1 rule, half-dollar tie → lower strike | pass |
| Entry cost | 100 × premium × 1.02 + $10, recomputed from the trade's own DTE | pass |
| Insufficient capital | cost−0.01 → 0 trades, equity flat; cost+0.01 → 1 trade; $100 → flat | pass |
| DTE boundaries | every entry is the last trading day in the 21–45 window; DTE ∈ {21,22,23} on a holiday-free calendar | pass |
| No overlap | each expiration ≤ next entry date | pass |
| Metric consistency | total return / max DD / win rate / n_trades recomputed from equity & trades | pass |
| Accounting | flat-day equity = cash; final equity = final cash (<1e-9); equity ≥ 0 | pass |
| Per-trade P&L identity | net = exit_gross − 2·0.02·exit_gross − $10 − entry_cost, within 2-dp storage rounding; win flag consistent | pass |
| No look-ahead | truncated-data equivalence: run on data cut at 2020-09-30 reproduces the full run's equity up to that date and the same settled trades | pass |
| Real data (network) | ≥1400 aligned rows 2020-01→2025-12, no duplicates/NaNs; DTE window respected; no overlaps; last-day-in-window entries; final equity = final cash; per-trade identity | pass |

### `test_app.py` — 4 tests, all PASSED (Streamlit `AppTest`, in-process)

- Default render: 4 metric cards (labels, tones — return colored by
  sign), 3 traces in fixed order, $10,000 normalization of every series,
  chart heights 400/220, unified hover, horizontal legend.
- Series selection: SPY-only, 2-series combo, and empty selection
  (annotation shown, no crash).
- Trade table: exact 9 columns, hidden index, row count = Number of
  Trades card.
- DTE section: fixed 21–45 window stated, "last trading day" rule,
  honest "Optimal DTE: TBD — no optimization" placeholder.

### Other verifications actually performed

- `python backtest.py` (built-in self-tests): **passed**.
- Real-data audit: 1,508 business days 2020-01-02 → 2025-12-31; SPY/VIX
  indices identical; 0 duplicates, 0 NaNs; 57 missing business days =
  market holidays (expected). Includes the March 2020 spike (VIX peak
  close 82.69 on 2020-03-16; the 2020-02-28 entry into the Mar 20 expiry
  won +$4,114.90).
- All four dashboard summary metrics independently recomputed from the
  engine's equity/trades output: **exact match**.

**Honesty note on visual verification:** browser automation is not
available in this environment, so **no visual/browser inspection of the
rendered page was performed**. The in-process `AppTest` runs above render
the real `app.py` and assert on its actual elements, which is the closest
substitute; layout/pixel-level correctness is not verified.

## 4. Remaining limitations (unchanged from README, plus)

- VIX as IV proxy; modeled (BS) option prices, not market quotes;
  European model for American options; flat 2% relative spread and $5
  commission; single contract, no sizing; results depend on the
  documented flat 1% rate and 1.3% dividend yield.
- The strategy itself is not profitable under these assumptions — this
  is a property of the strategy + assumptions, and it is the *correct*
  output of the code. Presenting it as a finding, not a defect to hide,
  is the whole point of the audit.

## 5. Likely quant-interviewer questions

1. **Why does the curve go flat at the end?** Capital exhaustion via the
   affordability guard; show the replay (last settlement cash < every
   future entry cost). Not a bug — and don't "fix" it by ignoring the
   guard.
2. **Why unadjusted SPY prices?** Strikes are set against the traded
   level; dividends are already in *q*; adjusted closes would
   double-count. (This was a real bug before the fix — knowing the
   `auto_adjust` default in yfinance is the tell.)
3. **How do you price the options?** European BS with continuous dividend
   yield; why not historical quotes (not available in bulk from
   yfinance; modeling assumption stated up front).
4. **What's wrong with using VIX as IV?** Index 30-day vol, not option
   IV; misses smile/skew, event premium, term structure; near-ATM
   straddles are the least-bad choice.
5. **How is there no look-ahead?** Same-day closes only; both SPY and VIX
   close are published by the evening; entry decision uses only
   information up to that close. Test: truncated-data equivalence.
6. **Why 21–45 DTE?** √time premium decay, capital lock-up vs. final-week
   gamma/spread problems; the 45 upper bound is a safety margin because
   entry is the *last* day in the window (actual entry 21–23 DTE).
7. **What is the win rate and why is it low?** 25% (3/12) at the
   dashboard's 30-DTE default; 32% (8/25) at the engine's 21-DTE default
   (post-settlement-fix; pre-fix it was 26% (6/23) at DTE 21). Buying
   volatility monthly with costs is a negative-carry bet in a trending
   market; one 2020-spike win (+$4.1k) doesn't offset the bleed.
8. **How would you make this a real strategy study?** Market quotes for
   straddles, realistic IV surface, sizing/money management, out-of-sample
   discipline, and an honest statement that the current result is a
   baseline, not a system.
9. **How do you verify the accounting?** Per-trade P&L identity, flat-day
   equity = cash, final equity = final cash, independent recomputation of
   all summary metrics (all in the test suite).
10. **What would change if we used real quotes?** Costs and prices both
    differ; the *code* is the same — only `bs_price` calls are replaced.

## 6. Presentation notes (15-minute talk)

- Open with the honest result (≈ −96% at the dashboard's 30-DTE default;
  ≈ −92.2% at the engine's 21-DTE default — post-settlement-fix) and the
  flat-line question — the flatline investigation *is* the story: capital
  guard working as designed.
- Walk: rules → one trade by hand (entry day, strike, BS premium, cost,
  settlement) → invariants the tests prove → the two real bugs found
  (`auto_adjust` double-count; the settlement double-spread, §7) →
  limitations → what a real study would need.
- Tests all pass; `python -m pytest test_backtest.py test_app.py -v`
  runs in under a minute (plus the one network fetch).

## 7. Second audit round (2026-10-29)

A full re-audit (look-ahead, Black–Scholes, strategy rules, accounting,
outputs) plus a UI redesign of the Methodology section. Executed, not
assumed.

### Bugs found and fixed

| # | Bug | Fix | Verified |
|---|-----|-----|----------|
| 3 | **Double-charged exit spread at settlement.** Entry and every daily mark-to-market applied a single 2% relative half-spread (buy `mid×1.02`, mark `mid×0.98`), but the settlement step applied **two** (`intrinsic×(1−0.04)`). This contradicted the documented "2% half-spread" convention and created a downward step in the equity curve at every expiration (a discontinuity equal to 2% of the gross payoff). | Settlement now sells at the same single half-spread as the marks: `intrinsic×100×(1−0.02) − 2×commission` (`backtest.py` settlement block + `self_test`). The two test sites that mirrored the old 4% were corrected. | New regression test `test_settlement_uses_single_half_spread` (asserts net matches the single-spread convention and is >$0.50 away from the old double-spread value); `test_per_trade_pnl_identity` and the real-data identity now use `exit×(1−STR)−2COM`. Exact cash-path replay in `audit_check.py` matches the engine to the cent. |

No other defects were found. Specifically re-verified:

- **Black–Scholes**: call/put formulas, continuous dividend yield,
  maturity in years, vol scaling all correct. Put–call parity holds to
  1.1e-13 and the four standard no-arbitrage bounds (call/put lower &
  upper) have zero violation across 1,944 real (S,K,T) points. VIX is
  divided by 100 before use (percent → decimal). VIX remains a *proxy*
  for IV, not the option's actual IV (documented limitation, unchanged).
- **Look-ahead**: none. The engine uses only same-day SPY close and
  same-day VIX close (both published by that evening). The execution
  convention is now stated explicitly as **close-of-day**: entries,
  marks, and settlements execute at the close of the day on which they
  occur. Daily data has no intraday prices, so close-of-day is a
  disclosed approximation, not intraday execution realism. Confirmed by
  the existing truncated-data causality test (running on data cut at a
  date reproduces the full run up to that date).
- **Strategy rules**: ATM call+put at the nearest $1 strike (tie →
  lower), same monthly expiration (3rd Friday; 2nd Friday on Good
  Friday — Apr 2022 & Apr 2025), entry on the last trading day inside
  the DTE window, one position at a time, no entry unless the full cost
  fits in cash. All confirmed on real data.
- **Accounting**: 100-share multiplier, $5 per contract per leg per
  side, single 2% half-spread, no negative cash (min replayed cash
  $81.76 at DTE 30), final equity = final cash, no open position at the
  end. Cash conservation `final = initial + Σ net P&L` holds to <5¢.

### Why only ~10–12 trades in 2020–2025 (not 60+)

**A valid capital-constraint consequence, not an implementation
defect.** There are 71 candidate entry months in the window, but the
strategy is a net loser under the stated assumptions; the documented
affordability guard (`entry_cost <= cash`) blocks an entry whenever the
remaining cash cannot cover the full straddle cost (~$1,200–2,700).
After the capital runs low, entries are skipped and the strategy sits
flat. At DTE 21: 25 trades, 46 blocked entries. At DTE 30 (dashboard
default): 12 trades, 51 blocked entries. The `capital_exhausted` flag
is True in both cases (the strategy ended flat and its final entry day
was blocked for lack of cash). This is the guard working as designed;
it is not "fixed" by ignoring the guard.

### Corrected results (after the settlement fix)

The fix removes a cost that was double-charged, so the strategy loses
less per expiration and affords a few more trades. Honest, un-tuned:

| DTE | trades | total return | max DD | win rate | final equity |
|-----|--------|--------------|--------|----------|--------------|
| 21 (engine default) | 25 | −92.2% | −95.2% | 32% | $784 |
| 30 (dashboard default) | 12 | −96.0% | −96.1% | 25% | $402 |

(Pre-fix: 23/10 trades, −98.5%/−87.7%, $146/$1,233.)

### Tests

`python -m pytest test_backtest.py test_app.py -v` (Python 3.12.10,
pandas 3.1.0, numpy 2.5.3, scipy 1.18.1, plotly 7.1.0, Streamlit 1.66.1,
yfinance 1.7.0): **37 passed, 0 failed** (22.9s, including the live
yfinance fetch). New tests added: no-arbitrage bounds, straddle
expiration payoff identity (100×|S_T−K|), single-half-spread settlement
regression, cash conservation + nonnegative cash, win-rate/max-DD
recompute, reproducibility on fixed synthetic data, and no-negative-
cash / no-leverage replay. The network tests ran (online) and are not
conflated with local-only failures.

### Methodology UI redesign

The former single dense-paragraph `st.expander` was replaced with a
structured "Methodology & Assumptions" section: (A) a five-tile strategy
overview bound to the live `target_dte`; (B) a two-column mechanics grid
(entry/strike, expiration/settlement, sizing, cash constraints); (C) a
pricing & execution assumptions table whose r/q/commission/spread/
multiplier/DTE values are read from `backtest` constants and the live
slider (not hardcoded); (D) five compact, subtly-emphasized model
limitation rows; (E) a collapsible "Data provenance & disclosure"
footer. Values track configuration (e.g. Target DTE 30 ↔ window 30–45)
and are asserted against the engine constants in `test_app.py`.

### Browser verification (round 2 — performed)

Unlike round 1, this round used **real browser automation** (Playwright +
system Chrome, `methodology_check.py`) against the live server. Verified:
the section renders all five subsections; the overview tiles and DTE-window
row track the live slider (30 → 35 live-updates, stale 30 does not leak);
all assumption-table values match the `backtest` constants; five limitation
rows and the provenance footer are present; no horizontal overflow at a
390px viewport and the tile/mechanics grids collapse to one column. This
covers layout and responsiveness that in-process `AppTest` cannot.

**Pre-existing (not introduced by this change), flagged honestly:** no
element in the app carries a `data-theme` attribute, so the
`[data-theme="dark"]` CSS token block — present before this redesign — is
dormant. All surfaces (metric cards, trade table, and the new section)
render the same light "paper" style in both light and dark schemes. The
new section's surfaces and value-ink were asserted **identical** to the
existing metric-card surfaces in both schemes, so it is consistent with
the rest of the dashboard. Activating true dark mode would require wiring
`data-theme` onto the app root and is a dashboard-wide change, deliberately
out of scope for this task.

## 8. Third audit round (2026-10-29): SPY-only + compact Methodology

Executed, not assumed. Financial results are unchanged (same engine,
same data, same assumptions); only the UI and its documentation changed.

### Changes made

- **SPY is the only underlying.** The S&P 500 (SPX) benchmark series was
  removed: `_fetch_spx()`/`spx_close`, the SPX entries in `norm`,
  `SERIES_ORDER`, and `SERIES_STYLES`, and the SPX multiselect option.
  The equity chart now compares Strategy vs. SPY only; no SPX label
  remains anywhere on the page (asserted in `methodology_check.py` and
  `test_app.py`). No calculation logic was touched.
- **Compact Methodology section.** The A-E structured section was
  replaced with: (1) a one-line summary "SPY Long Straddle | Modeled"
  plus "Entry: Monthly | Target DTE: 30 days | Exit: Expiration"
  (Target DTE live-bound to the slider); (2) an always-visible
  disclosure "Option prices are modeled, not historical market
  quotes."; (3) one expandable "Pricing, execution & limitations" group
  containing three accordions (Entry & exit rules / Pricing & costs /
  Limitations & data), **all collapsed by default**. No nested cards,
  no warning borders, no icons; values read from `backtest` constants
  and the live slider (1.0% flat, 1.3% continuous, $5 per contract per
  leg, 2% relative half-spread, 100-share multiplier, live
  `target_dte`-45 window). No em or en dashes in any visible UI text
  (caption "2020-2025", chart title "Equity, ...", and all new section
  text use standard hyphens; verified across `document.body.innerText`
  in the browser check).
- **Tests updated, all executed:** `py -3.12 -m pytest test_backtest.py
  test_app.py -v` — **37 passed, 0 failed** (28.4s, including the live
  yfinance fetch). `methodology_check.py` was rewritten for the new
  design and passed fully against the live server (collapsed-by-default,
  click-to-expand at both disclosure levels, live 30 → 35 tracking with
  no stale-30 leak, compactness, 390px no-overflow in both collapsed
  and expanded states, no SPX, no dashes).

### Substantive modeling inconsistency found (reported, not masked)

The round-3 brief listed the entry rule as "**First trading day of the
month**". The implementation does **not** do that, and the UI now
states the actual rule rather than the brief's phrasing. `backtest.py`
selects, per expiration, the **last** trading day whose DTE is still
inside the `target_dte`-to-45 window (`entries[exp] = window[-1]`,
backtest.py:284-292) — i.e. entry at ~`target_dte` days before the
3rd-Friday monthly expiration. The Methodology accordion states exactly
this ("Last trading day whose DTE is still inside the 30-45-day
window", with the 30 live-bound), and separately notes that the slider
range is 21-45 while the live window is `target_dte`-45 (default 30-45).
No code was changed to "fix" the discrepancy: the behavior is the
documented engine behavior, has full test coverage, and matching the
brief's wording in the UI would have been the mask the brief forbids.

### Verification summary

- 37/37 automated tests pass (engine + UI, real-data invariants
  included).
- Browser check: 39/39 assertions pass, screenshots inspected
  (collapsed, expanded, 390px narrow) - layout balanced, readable,
  no overflow, no broken expansion.
- No SPX-specific functionality, labels, or selectors remain; the page
  is consistently SPY.
- No em/en dashes in visible UI text; no bright warning borders, no
  decorative icons; fixed 1200px container preserved; Optimal DTE mode
  still hides all results; DTE slider and all controls preserved.

## 9. Fourth audit round (2026-10-29): adjustable Initial Capital

Executed, not assumed. The engine already threaded `initial_capital`
through every calculation (backtest.py `run_backtest(...,
initial_capital=10_000.0, ...)`; cash initialized from it, every
settlement against it, `total_return` relative to it). No engine change
was needed: this round wires the value from a new UI control through to
that parameter, so a changed capital re-runs the full portfolio
accounting.

### Why a capital change is a re-run, not a rescale

The affordability guard (`if entry_cost <= cash`, backtest.py:345) is
capital-dependent: it decides **which** trades are taken. The position
size is fixed (one call + one put, no leverage, no fractional
contracts, no automatic scaling), so the same premium that is
unaffordable at $10,000 is affordable at $100,000. Real-data probe at
30-DTE (measured, not modeled): $5,000 -> 5 trades / -90.6%, $10,000 ->
12 trades / -96.0%, $25,000 -> 37 trades / -91.8%, $50,000 -> 44 trades
/ -53.6%, $100,000 -> 44 trades / -26.8%. The $10,000 trade set is a
strict subset of the $100,000 set. Total return is NOT proportional to
capital (that would be the rescale the brief forbids); only the
per-trade P&L in dollars is invariant, which is the correct
consequence of fixed one-contract sizing.

### Changes made

- **`app.py`**: new "Initial Capital" `st.slider` ($5,000-$100,000,
  step $5,000, default $10,000, `format="$%,d"` so the value displays
  as `$10,000`), in the same 3-column compact row as the mode selector
  and the Target DTE slider (both sliders keep the 400px CSS cap).
  `key` + `persist_state="session"` keep it stable across the Optimal
  DTE round trip, like the DTE slider. The engine call now passes
  `initial_capital=initial_capital`. The equity chart base became
  `BASE = float(initial_capital)` with the title and y-axis label
  bound to it (the strategy series is then its own dollars; SPY is
  indexed to the same base). The Methodology facts line gained
  "Capital: $10,000" (live-bound) and the Position accordion item now
  states the fixed-sizing rule plus the skip-and-record behavior; the
  accordion also lists the slider range and live value.
- **`test_backtest.py`** (4 new, all synthetic fixtures):
  `test_insufficient_capital_records_blocked_entry` (skip is recorded
  in `blocked_entries`; with zero taken trades the exhaustion flag
  stays False, per the engine's documented semantics),
  `test_cash_conservation_across_capital_levels` (identity at $8k,
  $9k, $12k, $100k), `test_trade_count_changes_with_capital`
  (16 trades at $8k with a recorded blocked entry and exhausted=True;
  the same 17 trades at $9k/$12k/$100k, same P&L sum), and
  `test_reproducible_across_capital_values` (byte-identical repeated
  runs at each of three capital levels).
- **`test_app.py`**: default-render assertions for the capital slider
  (label, bounds, step, default, USD format string), the chart title
  bound to the live capital, the facts line, and the fixed-sizing
  disclosure; new `test_capital_slider_reruns_accounting` moves the
  slider $10,000 -> $100,000 and asserts every card matches an
  independent engine re-run on the same real data, the trade-table row
  count follows the new trade set (12 -> 44), the return is not a
  proportional rescale, the chart title and methodology fact track the
  new value, and the factual capital-exhaustion note appears only when
  the engine says so (False at $100k, True back at $10k).
- **`README.md`**: strategy-rules capital bullet, the results note
  (30-DTE, $100k -> 44 trades / ~-26.8% vs 12 at $10k), and the test
  descriptions.

### Verification summary

- 42/42 automated tests pass (38 engine + 4 UI, real-data invariants
  included): `py -3.12 -m pytest test_backtest.py test_app.py -v`
  (36.6s).
- Browser check `methodology_check.py`: 42/42 assertions pass,
  including the new live 10,000 -> 25,000 capital tracking with no
  stale-value leak, the fixed-sizing accordion text, no em/en dashes,
  no SPX, and 390px no-overflow in both states.
- Screenshots inspected (`run_top.png`, `run_fullpage.png`): the three
  controls share one compact row inside the fixed 1200px container;
  the capital value renders as `$10,000`; all prior sections intact.
- No strategy, period, pricing, or sizing change; one contract per leg
  at every capital level; page dimensions and styling preserved.
