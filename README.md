# SPY Long Straddle Backtest

A small, self-contained research project: a Streamlit dashboard that
backtests a monthly **long straddle** (one at-the-money call and one
at-the-money put, same strike, same monthly expiration) on **SPY** from
**January 2020 to December 2025**. Every option premium is **modeled**
with the Black–Scholes formula — these are not historical market quotes —
and the project is built to be read, reproduced, and defended line by
line. The goal is a defensible research artifact, not a production
trading system.

The strategy is a net loser under its stated assumptions. That is an
honest, un-tuned output of the code and is shown as-is (see
[Results](#results-jan-2020--dec-2025)). Nothing has been adjusted to make
it look profitable.

## 1. What the project does

For each SPY monthly contract month, the engine buys a long ATM straddle
on a pre-computed entry day, marks it to modeled value every day, and
settles it at the contract's expiration, tracking cash, equity, drawdown,
and per-trade P&L. A single-page Streamlit dashboard (`app.py`) wraps the
engine: every control is a real engine input, so moving one re-runs the
full pricing and accounting path live.

### Dashboard features

Verified against `app.py`; every item below is present and wired:

- **Controls (all real engine inputs):**
  - *Expiration* mode toggle — `Fixed DTE` (active) / `Optimal DTE (TBD)`
    (displays "not implemented yet" and runs no backtest).
  - *Target DTE* slider, 21–45 days (default 30).
  - *Initial Capital* slider, $5,000–$100,000 in $5,000 steps (default $10,000).
  - *Risk-Free Rate (%)* number input, −10 to 20 (default 1.0).
  - *Dividend Yield (%)* number input, 0 to 10 (default 1.3).
  - Slider and pricing-input selections persist in the browser session
    across a mode switch.
- **Performance summary:** four metric cards — Total Return, Max
  Drawdown, Win Rate, and number of Trades — computed from the live run.
- **Portfolio Equity chart:** the backtested strategy curve alongside a
  buy-and-hold SPY benchmark; both read in real dollars and both start at
  the chosen initial capital. A multiselect toggles which series are shown.
- **Drawdown chart:** the strategy's running drawdown below the equity
  curve.
- **Trade History table:** one row per settled straddle — entry date,
  expiration, strike, DTE, entry cost, exit value, and net P&L (color-coded
  by sign).
- **Methodology & Assumptions** section at the bottom of the page: a
  short summary bound to the live configuration, an always-visible
  "modeled prices" disclosure, and three expandable accordions (entry &
  exit rules, pricing model, execution convention / no look-ahead).
- **Capital-exhaustion note:** shown only when the engine's factual
  `capital_exhausted` flag is set, explaining that the affordability
  guard blocked all further entries.

On first load the page downloads ~6 years of daily SPY and VIX closes from
Yahoo Finance via `yfinance` and caches them for one hour. If the download
fails, the page shows the error and stops — it never invents results.

## 2. Install and run

### Prerequisites

- **Python 3.12** — the version the project was developed and tested
  against (see the exact package versions in `requirements.txt`).
- **Internet access on first load** — the dashboard downloads ~6 years of
  daily SPY and VIX closes from Yahoo Finance via `yfinance`.

### Steps

Commands below use PowerShell (Windows); the same commands work on
macOS/Linux with the noted substitution.

```powershell
# 1. From the project folder, create and activate a virtual environment
#    (optional but recommended)
python -m venv .venv
.\.venv\Scripts\Activate.psl
#    macOS/Linux instead of the two lines above:
#      python3 -m venv .venv && source .venv/bin/activate

# 2. Install the dependencies
pip install -r requirements.txt

# 3. (Optional, before first launch) run the built-in self-tests:
#    option payoffs, put-call parity, literature reference values,
#    accounting invariants. Expect "All self-tests passed."
python backtest.py

# 4. Launch the dashboard
streamlit run app.py
```

Then open **http://localhost:8501** in a browser. Streamlit prints the
local URL when the server starts and stays running until you stop it.

### What happens on first load

The page downloads the SPY and VIX series (cached for one hour by
`st.cache_data`, so page reloads within the hour do not re-download) and
runs the backtest. If the download fails, the page shows the error and
stops — it never invents results.

### Using the dashboard

- Every control (Target DTE, Initial Capital, Risk-Free Rate, Dividend
  Yield) is a real engine input: changing one re-runs the full pricing
  and accounting path live. There is no "Run" button.
- The *Expiration* toggle switches between `Fixed DTE` (the full
  backtest, shown above) and `Optimal DTE (TBD)` (not implemented; it
  shows a notice and runs no backtest).
- Stop the server with **Ctrl+C** in the terminal that runs Streamlit.

### If port 8501 is already in use

```powershell
streamlit run app.py --server.port 8502
```

…then open http://localhost:8502 instead.

## 3. Data sources

| Input | Source | How it is used |
|---|---|---|
| SPY close | yfinance `SPY`, **unadjusted** (`auto_adjust=False`) | the underlying price S |
| VIX close | yfinance `^VIX` | the volatility input sigma = VIX / 100 |
| Call/Put prices | **Black–Scholes, computed** | not historical market quotes |

Why **unadjusted** SPY: option strikes are set against the actual traded
SPY level, and the dividend drag is already modeled explicitly through the
continuous dividend yield `q` in Black–Scholes. Using dividend-adjusted
closes would count the dividends twice. This is a real, documented choice,
not a default.

Both series are aligned to the common NYSE trading days. If the download
contains missing values, `fetch_data` raises an error instead of silently
filling gaps or fabricating data.

## 4. Strategy entry and exit rules

- **Cadence:** one candidate per contract month.
- **Expiration:** the standard SPY monthly — the 3rd Friday of the month,
  moved to the 2nd Friday when the 3rd is Good Friday (the case for April
  2022 and April 2025). This is computed from the calendar, not hardcoded.
- **Entry day:** the **last** trading day on which the contract's DTE is
  still inside the `target_dte`-to-45 window. On a normal calendar that
  lands 21–23 days before expiration.
- **Strike:** the nearest `$1` strike to the entry-day SPY close; a
  half-dollar tie goes to the **lower** strike.
- **Position:** exactly one call and one put, one standard contract each
  (100-share multiplier), fixed for every capital level. No leverage, no
  fractional contracts, no position scaling.
- **Overlap:** at most one position open at a time; a new straddle opens
  only after the previous one has expired.
- **Affordability guard:** a straddle is opened only if its full entry
  cost fits in available cash; otherwise the entry is skipped and the skip
  is recorded. Because this guard is capital-dependent, changing the
  initial capital changes **which** trades are taken (a re-run), not a
  rescale of a single run.
- **Exit:** held to expiration and settled at intrinsic value.

### Why 21–45 days to expiration?

The window is a fixed input, not a tuned parameter. Entering earlier
(DTE > 45) locks up more capital in a contract whose premium decays
roughly with √time. Entering later (DTE < 21) buys a contract dominated
by final-week gamma and vega with thin quotes, leaving little time for the
move being paid for. The upper bound of 45 is a safety margin only: because
entry is the *last* day inside the window, the actual entry is at 21–23 DTE
even on thin calendars. **No optimization has been performed** — sweeping
DTE against modeled prices would fit the model to itself.

## 5. Pricing model

European Black–Scholes with a continuous dividend yield:

```
C = S·e^{-qT}·N(d1) − K·e^{-rT}·N(d2)
P = K·e^{-rT}·N(−d2) − S·e^{-qT}·N(−d1)
d1 = [ ln(S/K) + (r − q + σ²/2)·T ] / (σ·√T)
d2 = d1 − σ·√T
```

- `T` is time to expiration in **years** (calendar days / 365).
- `sigma` is the annualized volatility, the VIX close divided by 100.
- `r` (risk-free rate) and `q` (dividend yield) are adjustable inputs.
- At `T = 0` the price is exactly the intrinsic value `max(S−K,0)` /
  `max(K−S,0)`; at zero volatility it returns the forward intrinsic value.
- **Put–call parity** `C − P = S·e^{-qT} − K·e^{-rT}` holds to ~1e-13 and
  is checked in the test suite, as are the standard no-arbitrage bounds.

Known model approximation: SPY options are American; the European
Black–Scholes value is a close but not exact substitute.

## 6. Portfolio accounting

The engine maintains a single cash account plus at most one open straddle:

- **Entry:** `cash −= 100 × (call_mid + put_mid) × (1 + 0.02) + 2 × $5`
  (the 100-share multiplier, a 2% relative half-spread paid to buy at mid +
  2%, and $5 commission per contract per leg, i.e. $10 for both legs).
- **Daily mark-to-market:** portfolio value = `cash + 100 × (call_mid +
  put_mid) × (1 − 0.02)` while a straddle is open, else just `cash`. The
  same single 2% half-spread used on entry is the haircut on every daily
  mark, so the equity curve is continuous.
- **Settlement at expiration:** the gross payoff is
  `100 × (max(S−K,0) + max(K−S,0)) = 100 × |S−K|`. It is sold at the **same
  single** 2% half-spread as the daily marks and exit commissions are
  charged once: `cash += gross × (1 − 0.02) − 2 × $5`. The half-spread is
  applied once per side, not doubled at settlement (a previous version
  double-charged it; a regression test pins this).
- **Invariants** (all asserted in the tests): cash never goes negative,
  final equity = final cash (no position open at the end), final equity =
  initial capital + Σ per-trade net P&L, and there is no leverage.

## 7. Execution convention and no look-ahead

This is stated explicitly because "we only use the same day's close" alone
is not enough proof when execution also occurs at that close.

The backtest walks trading days in order. On day `t` the only information
made available to the strategy is the SPY close `S[t]` and the VIX close
`V[t]`, both of which are **finalized at 16:00 ET on day t**. Every decision
on day `t` — the mark-to-market, and (if `t` is a scheduled entry day) the
entry decision and its fill — is made **at the close of day t**, using only
`S[t]` and `V[t]`. Because the decision and the fill occur at the *same*
instant, and both use only values already finalized at that instant, no
quantity dated after the fill is ever used. The information set at
decision time equals the information set at execution time — that is the
genuine no-look-ahead guarantee.

The single disclosed optimism is that a real close fill could differ from
the close (gaps, spread, queue), because daily data has no intraday prices.
`test_no_lookahead_truncation` proves the property empirically: running the
engine on data truncated at date D reproduces the full run's equity path
and settled trades up to D exactly.

## 8. Adjustable parameters

| Control | Type | Range | Default | What it re-runs |
|---|---|---|---|---|
| Target DTE | slider | 21–45 | 30 | entry day and modeled premium for every trade |
| Initial Capital | slider | $5,000–$100,000 (step $5,000) | $10,000 | the affordability guard, so **which** trades are taken |
| Risk-Free Rate | number input (%) | −10 to 20 | 1.0 | every Black–Scholes call (entry, marks, settlement) |
| Dividend Yield | number input (%) | 0 to 10 | 1.3 | every Black–Scholes call |

Every control is a real engine input: changing one re-runs the full
pricing and accounting path. The tests assert that a moved control changes
the results in a way that is *not* a rescale of a fixed run.

## 9. Results (Jan 2020 – Dec 2025)

With the dashboard defaults ($10,000 capital, 30-DTE target) the strategy
ends at roughly **$402** (≈ −96.0% total return, max drawdown ≈ −96.1%,
12 trades, 25% win rate). At the engine's 21-DTE default it ends at about
**$784** (≈ −92.2%, 25 trades, 32% win rate). At $100,000 capital and
30-DTE it takes 44 trades and ends ≈ −26.8%.

The equity curve goes permanently flat after the last trade because the
affordability guard blocks every further entry: straddle premiums run
~$1,200–2,700 while the remaining cash is below that. This is a capital
constraint working as designed, not a bug. The engine reports it factually
via a `capital_exhausted` flag (true only when the strategy ended flat and
its last entry day in the window was itself blocked for lack of cash); the
dashboard shows its "insufficient capital" note only when that flag is true.

Buying ATM straddles monthly with costs is a negative-carry bet in a
trending market: one large spike (March 2020, VIX to 82) does not offset
the steady premium bleed.

## 10. Tests

```powershell
python -m pytest test_backtest.py test_app.py -v
```

- **`test_backtest.py`** — engine tests on **synthetic fixtures**
  (deterministic price series; never market results): Black–Scholes against
  literature reference values, put–call parity, expiration payoffs,
  zero-vol and deep-ITM limits, the four no-arbitrage bounds, contract
  months (including the Good-Friday exception), nearest-strike ties, the
  entry-cost formula, the affordability boundary, the `target_dte`
  parameter, the factual `capital_exhausted` flag (all three states), no
  overlapping positions, summary-metric recomputation, the per-trade P&L
  identity, cash conservation across several capital levels, no-negative-
  cash / no-leverage replay, reproducibility on fixed synthetic data, and
  the **no-look-ahead causality** test. A final test re-validates all
  invariants on the real download and is **skipped automatically when
  offline**.
- **`test_app.py`** — dashboard tests via Streamlit's in-process `AppTest`
  (renders the real `app.py`; **not** a browser/pixel check): the metric
  cards, both charts and their traces, all series-selection combinations,
  the trade table, and that each control (DTE, capital, risk-free rate,
  dividend yield) triggers a real engine re-run matching an independent
  recompute.

`python backtest.py` runs the quick built-in self-tests.

## 11. Modeling assumptions and known limitations

- **Modeled, not historical, option prices.** Every premium is a
  Black–Scholes computation, not a traded quote. Results are therefore
  sensitive to the r, q, and volatility inputs.
- **VIX as a volatility proxy.** VIX is the index's expected 30-day
  volatility, not the implied volatility of the straddles themselves; it
  misses smile/skew, event premium, and the term structure.
- **European model for American options.** A close, not exact,
  approximation.
- **Simplified execution costs.** One fixed $5 commission and one flat 2%
  relative half-spread per side; no market impact, queue, or overnight-gap
  risk.
- **Strike selection.** "Nearest $1 strike to the close" approximates
  at-the-money; real ATM choice can depend on the option chain.
- **Single contract.** One contract per leg regardless of portfolio size —
  a capital constraint, not a sizing strategy.
- **Backtest window ends December 2025** and includes the 2020 spike by
  construction.

**A profitable result is not required or claimed.** The point is a
reproducible, readable implementation of stated rules.

## Files

- `app.py` — Streamlit dashboard (single page).
- `backtest.py` — data download, Black–Scholes pricing, contract-month
  generation, the daily backtest loop, and the built-in self-tests.
- `test_backtest.py` — engine test suite (synthetic fixtures + network
  tests on real data, skipped offline).
- `test_app.py` — dashboard tests (Streamlit `AppTest`).
- `requirements.txt` — pinned dependency list.
- `README.md` — this file.
- `REVIEW.md` — the audit trail: four review rounds, the bugs found and
  fixed (dividend double-count, settlement double-spread), and a list of
  likely interview questions. Kept as the record of the decisions.
- `presentation/` — reserved for a LaTeX presentation
  (`slides.tex`, `figures/`, `slides.pdf`), to be added in a later commit.
  Currently contains only a `.gitkeep` placeholder; no presentation
  files exist yet.
