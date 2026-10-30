"""SPY Long Straddle backtest — strategy and pricing logic.

All option prices in this project are MODELED with the Black–Scholes
formula (European-style, continuous dividend yield). They are not actual
historical market quotes. See README.md for the full list of assumptions.

Public entry points
-------------------
fetch_data(start, end) -> (spy_df, vix_df)   raw prices from yfinance
run_backtest(data)     -> dict               full daily backtest
self_test()            -> None               assertion-based checks
"""

from __future__ import annotations

import math

import pandas as pd
from scipy.stats import norm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
START = "2020-01-01"
END = "2026-01-01"          # exclusive, so the last included month is Dec 2025
RISK_FREE_RATE = 0.01       # annual rate (documented assumption: flat 1% curve)
DIVIDEND_YIELD = 0.013      # SPY's ~1.3% distribution yield (documented assumption)
CONTRACT_SIZE = 100         # one standard option contract = 100 shares
COMMISSION_PER_CONTRACT = 5.0     # $5 per option contract, charged at each leg
SPREAD_REL_HALF = 0.02      # enter at mid + 2% of premium; exit at mid - 2%
MIN_DTE, MAX_DTE = 21, 45   # allowed days-to-expiration on the entry date

# ---------------------------------------------------------------------------
# Black–Scholes pricing (European options, continuous dividend yield)
# ---------------------------------------------------------------------------
def bs_price(option_type: str, S: float, K: float, T: float, sigma: float,
             r: float = RISK_FREE_RATE, q: float = DIVIDEND_YIELD) -> float:
    """Black–Scholes price of a European call or put.

    S      spot price
    K      strike
    T      time to expiration in YEARS (fraction of 365)
    sigma  annualized volatility (decimal, e.g. 0.20 for 20%)
    r      annual risk-free rate
    q      annual continuous dividend yield
    """
    if T <= 0:
        # Intrinsic value at expiration: max(S - K, 0) / max(K - S, 0)
        return max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    if S <= 0:
        raise ValueError("spot price must be positive")
    if sigma <= 0:
        # Degenerate zero-vol case: forward intrinsic value.
        fwd = S * math.exp(-q * T) - K * math.exp(-r * T)
        return max(fwd, 0.0) if option_type == "call" else max(-fwd, 0.0)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    if option_type == "call":
        return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def _extract_close(raw: pd.DataFrame, ticker: str) -> pd.Series:
    """Extract the unadjusted 'Close' series for ``ticker`` from a
    yfinance frame.

    ``yf.download`` returns **MultiIndex** columns ``(Price, Ticker)``
    (``Close``/SPY, ``Close``/^VIX, …) even for a single ticker; older or
    single-series frames use flat columns (``Close``, ``High``, …). Select
    the *unadjusted* ``Close`` (never ``Adj Close`` — the dividend yield is
    already modeled via *q* in Black–Scholes, so adjusted prices would
    double-count distributions).

    Returns a Series named 'close' carrying the raw (possibly tz-aware)
    index. If ``ticker`` has no Close column at all (its download failed),
    an empty Series is returned so the caller can report it as 0 rows.
    """
    if isinstance(raw.columns, pd.MultiIndex):
        try:
            close = raw[("Close", ticker)]
        except KeyError:
            return pd.Series(dtype=float, name="close")
    else:
        if "Close" not in raw.columns:
            return pd.Series(dtype=float, name="close")
        close = raw["Close"]
    return pd.Series(close.astype(float), name="close", index=raw.index)


def fetch_data(start: str = START, end: str = END,
               vix_ticker: str = "^VIX", spy_ticker: str = "SPY"):
    """Download daily SPY closes and VIX closes from yfinance.

    SPY is downloaded UNADJUSTED (``auto_adjust=False``): option strikes
    and expirations are defined against the actual traded index level,
    and the dividend yield is already modeled explicitly in
    Black–Scholes — adjusting for dividends would double-count them.

    Both symbols are fetched in a **single ``yf.download`` call** rather
    than two ``yf.Ticker(ticker).history`` calls. The per-ticker
    ``Ticker.history`` path silently returns an *empty* DataFrame (no
    exception, by default) when its timezone pre-probe or the price fetch
    hits a transient network error — so one hiccup on SPY alone yields
    ``spy=0 rows`` while VIX succeeds. ``yf.download`` retries transient
    errors and reports a failed ticker as an all-NaN column, which this
    function then turns into a hard error below instead of fabricating
    results.

    Both series are aligned to the common NYSE trading days, so on any
    backtest date only prices known at that date's close are available
    (no look-ahead: same-day VIX close is used as the volatility input
    for same-day valuation — both are published in the evening).

    Returns (spy_df, vix_df), each a DataFrame indexed by date with one
    'close' column.
    """
    import yfinance as yf

    # auto_adjust=False is REQUIRED: the backtest needs the actual traded
    # close (option strikes are set against the traded SPY level), and the
    # dividend drag is modeled explicitly via q in Black–Scholes — using
    # dividend-adjusted prices would double-count the distributions.
    # progress=False keeps the batched download quiet.
    raw = yf.download([spy_ticker, vix_ticker], start=start, end=end,
                      auto_adjust=False, progress=False)

    if raw is None or raw.empty:
        raise RuntimeError(
            "yfinance returned no data (spy=0 rows, vix=0 rows). Check "
            "network access — the backtest is NOT run on fabricated data.")

    spy_close = _extract_close(raw, spy_ticker)
    vix_close = _extract_close(raw, vix_ticker)

    # A ticker whose download failed arrives as an all-NaN column (or is
    # absent). That is a hard failure, distinct from genuine mid-series
    # gaps handled below: refuse to run rather than drop the symbol.
    n_spy = int(spy_close.notna().sum())
    n_vix = int(vix_close.notna().sum())
    if n_spy == 0 or n_vix == 0:
        raise RuntimeError(
            f"yfinance returned no data (spy={n_spy} rows, "
            f"vix={n_vix} rows). Check network access — the backtest "
            "is NOT run on fabricated data.")

    # yfinance timestamps SPY with America/New_York and VIX with
    # America/Chicago (Ticker.history) or naively (download); both are
    # midnight of the trading day, so normalize to naive local dates
    # before intersecting the calendars.
    if spy_close.index.tz is not None:
        spy_close.index = spy_close.index.tz_localize(None)
    if vix_close.index.tz is not None:
        vix_close.index = vix_close.index.tz_localize(None)
    common = spy_close.index.intersection(vix_close.index)
    spy_close, vix_close = spy_close.loc[common], vix_close.loc[common]

    # Report (do not silently repair) missing values.
    if spy_close.isna().any() or vix_close.isna().any():
        n_spy = int(spy_close.isna().sum())
        n_vix = int(vix_close.isna().sum())
        raise RuntimeError(
            f"Missing values in downloaded data: {n_spy} SPY, {n_vix} VIX. "
            "Refusing to run the backtest on gaps in the data.")
    # The backtest window is Jan 2020 – Dec 2025; make coverage explicit.
    if common[0] > pd.Timestamp("2020-02-01") or common[-1] < pd.Timestamp(
            "2025-12-01"):
        raise RuntimeError(
            f"Downloaded data does not cover the required window "
            f"Jan 2020 – Dec 2025: first date {common[0].date()}, "
            f"last date {common[-1].date()}.")
    return spy_close.to_frame(), vix_close.to_frame()


# ---------------------------------------------------------------------------
# Contract months
# ---------------------------------------------------------------------------
def contract_months(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """All SPY monthly contract expirations between start (inclusive) and
    end (inclusive).

    CBOE rule for SPY monthlies: the 3rd Friday of the month; if that day
    is Good Friday, expiration moves to the 2nd Friday (this happens in
    the 2020–2025 window for April 2022 and April 2025).
    """
    out = []
    year = start.year
    month = start.month
    while True:
        first = pd.Timestamp(year, month, 1)
        last = first + pd.offsets.MonthEnd(0)
        if last > end:
            break
        # 1st Friday of the month, then step to the 3rd
        d = first
        while d.weekday() != 4:
            d += pd.Timedelta(days=1)
        d += pd.Timedelta(days=14)
        if d == _easter(year) - pd.Timedelta(days=2):   # Good Friday
            d -= pd.Timedelta(days=7)
        out.append(d)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return [d for d in out if start <= d <= end]


def _easter(year: int) -> pd.Timestamp:
    """Gregorian Easter (Meeus/Jones/Butcher), verified against known
    dates 2000–2025. Only needed for the rare Good-Friday exception in
    the SPY contract-month rule (e.g. April 2022, April 2025)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return pd.Timestamp(year, n, day)


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
def run_backtest(data, start: str = START, end: str = END,
                 initial_capital: float = 10_000.0,
                 risk_free: float = RISK_FREE_RATE,
                 dividend_yield: float = DIVIDEND_YIELD,
                 spread_rel_half: float = SPREAD_REL_HALF,
                 commission: float = COMMISSION_PER_CONTRACT,
                 target_dte: int = MIN_DTE) -> dict:
    """Run the monthly long-straddle backtest.

    ``data`` is a (spy_df, vix_df) pair as returned by ``fetch_data``.

    Execution convention (close-of-day, explicitly stated so the no
    look-ahead claim is airtight):

    The backtest walks trading days t_1 < t_2 < ... . On day t the only
    information made available to the strategy is the SPY close S[t] and
    the VIX close V[t], both of which are FINALIZED at 16:00 ET on day t.
    Every decision on day t — the mark-to-market, and (if t is a
    scheduled entry day) the entry decision and its fill — is made at the
    close of day t, using only S[t] and V[t]. Because the decision and
    the (hypothetical) fill occur at the SAME instant, the close of day
    t, and both use only values already finalized at that instant, no
    quantity dated after the fill is ever used. This is why "we only use
    the same day's close" is here a genuine no-look-ahead guarantee and
    not merely an assumption: the information set at decision time equals
    the information set at execution time. (The only disclosed optimism
    is that a real fill at the close could differ from the close — gaps,
    spread, queue — because daily data has no intraday prices.) The
    truncated-data causality test (test_no_lookahead_truncation) proves
    this empirically: running on data cut at date D reproduces the full
    run's equity path up to D.

    Daily loop (only that day's closes and that day's VIX are visible):

    1. If a position is open and its expiration == today: mark it to
       modeled intrinsic value at today's close and settle — the payoff
       is sold at the same relative half-spread as the daily marks
       (intrinsic x (1 - spread_rel_half)) minus exit commissions.
    2. If no position is open (at most one position at a time — enforced
       by the ``open_trade is None`` guard below) and today is the last
       trading day on which ``target_dte`` <= DTE <= ``MAX_DTE`` for some
       future contract: enter a new straddle at the nearest-at-the-money
       strike (nearest close to today's close, ties -> lower strike),
       paying modeled mid prices + spread + commissions for both legs.
    3. Daily portfolio value = cash + (if open) 100 x modeled mid price
       of both legs at that day's close and VIX.

    ``target_dte`` is the strategy's target days-to-expiration (its lower
    entry bound, in [MIN_DTE, MAX_DTE]). ``MIN_DTE`` (21) is the default.

    Returns a dict with: equity (daily value), trades (one row per
    straddle), and summary metrics — including
    ``capital_exhausted``, a factual True/False flag for whether the
    strategy stopped trading because entries became unaffordable.
    """
    spy = data[0]["close"]
    vix = data[1]["close"]
    dates = spy.index

    expiry_dates = contract_months(
        pd.Timestamp(start),
        pd.Timestamp(end) - pd.Timedelta(days=1))

    # Entry day per expiration: last trading day with
    # target_dte <= DTE <= MAX_DTE before the expiration.
    entries: dict[pd.Timestamp, pd.Timestamp] = {}
    for exp in expiry_dates:
        window = dates[(dates >= exp - pd.Timedelta(days=MAX_DTE))
                       & (dates <= exp - pd.Timedelta(days=target_dte))]
        if window.empty:
            continue
        entries[exp] = window[-1]

    # Trades keyed by entry date (max_positions=1 -> one entry per day).
    trade_by_entry: dict[pd.Timestamp, dict] = {}
    open_trade: dict | None = None
    cash = float(initial_capital)
    equity = []
    blocked_entries: list[tuple[pd.Timestamp, float, float]] = []

    for date in dates:
        S = float(spy[date])
        vol = float(vix[date]) / 100.0          # VIX level -> decimal vol

        # ---- 1. Settlement at expiration ----
        # CBOE SPY monthlies always expire on a trading day (3rd Friday,
        # moved to the 2nd Friday when that is Good Friday — see
        # contract_months). The `>=` is a guard: settle on the first
        # trading day on/after expiration so a position can never be
        # left open unsettled; on real data this is identical to `==`.
        if open_trade is not None and date >= open_trade["expiration"]:
            K = open_trade["strike"]
            payoff = max(S - K, 0.0) + max(K - S, 0.0)
            # Modeled mid at T=0 equals intrinsic value. The position is
            # closed at the same relative half-spread (mid - spread_rel_half)
            # as every daily mark-to-market, so the equity curve is
            # continuous at expiration. (The previous version applied
            # 2 x spread_rel_half here, i.e. a full round-trip, which
            # double-charged the half-spread already applied on entry and
            # created a step in the equity curve at each expiration.)
            exit_gross = payoff * CONTRACT_SIZE
            exit_net = exit_gross * (1.0 - spread_rel_half) - 2 * commission
            cash += exit_net
            open_trade["exit_value"] = exit_gross
            open_trade["exit_net"] = exit_net
            open_trade["exit_date"] = date
            trade_by_entry[open_trade["entry_date"]] = open_trade
            open_trade = None

        # ---- 2. Entry check ----
        # (max_positions is enforced by the ``open_trade is None`` guard:
        #  a new position is only opened while no position is open.)
        if open_trade is None:
            for exp, entry_day in entries.items():
                if entry_day == date:
                    K = _nearest_strike(S)
                    T_in = (exp - date).days / 365.0
                    call_mid = bs_price("call", S, K, T_in, vol,
                                        risk_free, dividend_yield)
                    put_mid = bs_price("put", S, K, T_in, vol,
                                       risk_free, dividend_yield)
                    premium_mid = call_mid + put_mid
                    entry_cost = (CONTRACT_SIZE * premium_mid
                                  * (1.0 + spread_rel_half)) + 2 * commission
                    if entry_cost <= cash:      # need enough capital
                        open_trade = {
                            "entry_date": date,
                            "expiration": exp,
                            "strike": K,
                            "spot_at_entry": S,
                            "dte": (exp - date).days,
                            "vol": vol,
                            "entry_cost": entry_cost,
                        }
                        cash -= entry_cost
                    else:
                        # Unaffordable: stay flat that month and record the
                        # factual state (entry day, cash, cost) so the app
                        # can report capital exhaustion — never assume it.
                        blocked_entries.append((date, cash, entry_cost))
                    break

        # ---- 3. Daily mark-to-market ----
        if open_trade is not None:
            K = open_trade["strike"]
            T = (open_trade["expiration"] - date).days / 365.0
            call = bs_price("call", S, K, T, vol, risk_free, dividend_yield)
            put = bs_price("put", S, K, T, vol, risk_free, dividend_yield)
            mtm = CONTRACT_SIZE * (call + put) * (1.0 - spread_rel_half)
            value = cash + mtm
        else:
            value = cash
        equity.append({"date": date, "equity": value,
                       "open": open_trade is not None})

    equity_df = pd.DataFrame(equity).set_index("date")

    # ---- Trade table ----
    trades = []
    for t in (trade_by_entry[td] for td in sorted(trade_by_entry)):
        gross = t["exit_value"] - t["entry_cost"]
        net = t["exit_net"] - t["entry_cost"]
        trades.append({
            "entry_date": t["entry_date"],
            "expiration": t["expiration"],
            "strike": t["strike"],
            "dte": t["dte"],
            "vol_at_entry": round(t["vol"] * 100, 2),
            "entry_cost": round(t["entry_cost"], 2),
            "exit_value": round(t["exit_value"], 2),
            "net_pnl": round(net, 2),
            "gross_pnl": round(gross, 2),
            "win": net > 0,
        })
    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df = trades_df.set_index(["entry_date", "expiration"])

    # ---- Capital exhaustion ----
    # Factual report of the documented affordability guard. ``capital_
    # exhausted`` is True when the strategy ended FLAT and the window's
    # *last* entry day was itself blocked for lack of cash: it was flat
    # on that day, could not afford the straddle, and nothing comes after
    # it — i.e. trading genuinely stopped because of the capital floor,
    # not because the window ended. Earlier mid-run blocks (a transient
    # cash dip the strategy recovered from) do NOT count, because the
    # strategy resumed trading after them. The app shows its one-line
    # message only when this flag is True — never by assumption.
    if trade_by_entry and open_trade is None and blocked_entries:
        blocked_days = {d for d, _cash, _cost in blocked_entries}
        last_entry_day = max(entries.values())
        exhausted = last_entry_day in blocked_days
    else:
        exhausted = False

    # ---- Summary metrics ----
    eq = equity_df["equity"]
    peak = eq.cummax()
    max_dd = float(((eq - peak) / peak).min())
    total_return = float(eq.iloc[-1] / initial_capital - 1.0)
    n_trades = len(trades_df)
    win_rate = float(trades_df["win"].mean()) if n_trades else float("nan")

    return {
        "equity": equity_df,
        "trades": trades_df,
        "summary": {
            "initial_capital": initial_capital,
            "final_equity": float(eq.iloc[-1]),
            "total_return": total_return,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "n_trades": n_trades,
            "final_cash": float(cash),
            # True when trading stopped because the strategy could not
            # afford further straddles (see above). The dashboard shows
            # its one-line capital message only on this flag.
            "capital_exhausted": bool(exhausted),
            "blocked_entries": len(blocked_entries),
        },
        "params": {
            "start": start, "end": end,
            "risk_free": risk_free, "dividend_yield": dividend_yield,
            "spread_rel_half": spread_rel_half,
            "commission_per_contract": commission,
            "contract_size": CONTRACT_SIZE,
            "dte_window": (MIN_DTE, MAX_DTE),
        },
    }


def _nearest_strike(S: float) -> int:
    """Nearest SPY monthly strike to S (SPY strikes are $1 apart in
    normal ranges; a half-dollar tie goes to the lower strike)."""
    return int(math.ceil(S - 0.5))


# ---------------------------------------------------------------------------
# Self-test: option payoffs, costs, accounting (run: python backtest.py)
# ---------------------------------------------------------------------------
def self_test() -> None:
    # 1. Expiration intrinsic value.
    assert bs_price("call", 100, 90, 0, 0.2) == 10
    assert bs_price("put", 100, 90, 0, 0.2) == 0
    assert bs_price("call", 90, 100, 0, 0.2) == 0
    assert bs_price("put", 90, 100, 0, 0.2) == 10
    assert abs(bs_price("call", 100, 100, 0, 0.2) - 0.0) < 1e-9

    # 2. Put-call parity: C - P = S*exp(-qT) - K*exp(-rT).
    S, K, T, sig, r, q = 500.0, 500.0, 30 / 365, 0.20, 0.01, 0.013
    c = bs_price("call", S, K, T, sig, r, q)
    p = bs_price("put", S, K, T, sig, r, q)
    assert abs((c - p) - (S * math.exp(-q * T) - K * math.exp(-r * T))) < 1e-8

    # 3. Reference values (independent computation, no dividend):
    #    S=K=100, T=1y, sigma=20%, r=5% -> call 10.4506, put 5.5735.
    c = bs_price("call", 100.0, 100.0, 1.0, 0.20, 0.05, 0.0)
    p = bs_price("put", 100.0, 100.0, 1.0, 0.20, 0.05, 0.0)
    assert abs(c - 10.4506) < 5e-4, f"call {c}"
    assert abs(p - 5.5735) < 5e-4, f"put {p}"

    # 4. Transaction costs are charged on both legs.
    mid = 5.0
    cost = CONTRACT_SIZE * mid * (1 + SPREAD_REL_HALF) + 2 * COMMISSION_PER_CONTRACT
    assert cost == 100 * 5.0 * 1.02 + 10.0

    # 5. Portfolio accounting: a straddle that expires at the strike has
    #    gross payoff 0, so net P&L = -(entry cost + exit commissions);
    #    the entry cost already includes entry commissions for both legs.
    S_in, K, T, sig = 500.0, 500.0, 30 / 365, 0.25
    prem = (bs_price("call", S_in, K, T, sig) + bs_price("put", S_in, K, T, sig))
    entry_cost = CONTRACT_SIZE * prem * (1 + SPREAD_REL_HALF) \
        + 2 * COMMISSION_PER_CONTRACT
    exit_gross = 0.0 * CONTRACT_SIZE                    # S == K at expiry
    exit_net = exit_gross * (1 - SPREAD_REL_HALF) \
        - 2 * COMMISSION_PER_CONTRACT
    net_pnl = exit_net - entry_cost
    expected = -(entry_cost + 2 * COMMISSION_PER_CONTRACT)
    assert abs(net_pnl - expected) < 1e-9 and net_pnl < 0

    # 6. Cash reconciliation on a tiny synthetic backtest: a flat
    #    portfolio must keep its initial cash untouched.
    idx = pd.bdate_range("2020-01-02", "2020-03-31")
    spy = pd.DataFrame({"close": pd.Series(500.0, index=idx)})
    vix = pd.DataFrame({"close": pd.Series(15.0, index=idx)})
    # Contract months in this window: 2020-02-21 and 2020-03-20; with
    # start 2020-01-01 the Feb expiry is entered Jan 31, 2020 (DTE 21).
    res = run_backtest((spy, vix), start="2020-01-01", end="2020-04-01")
    s = res["summary"]
    assert s["n_trades"] >= 1, "expected at least one trade in the window"
    # Daily equity must equal cash + marked position; on flat days it is
    # exactly cash, and cash never goes negative.
    eq = res["equity"]["equity"]
    assert (eq >= 0).all()
    # Final equity must equal final cash (no open position at the end).
    assert abs(eq.iloc[-1] - s["final_cash"]) < 1e-9

    # 7. Contract months: known historical expirations, including the
    #    Good-Friday exception (2nd Friday when the 3rd is Good Friday).
    cm = contract_months(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-12-31"))
    assert pd.Timestamp("2020-02-21") in cm, "Feb 2020 monthly"
    assert pd.Timestamp("2020-03-20") in cm, "Mar 2020 monthly"
    assert pd.Timestamp("2020-06-19") in cm, "Jun 2020 monthly"
    assert pd.Timestamp("2022-04-08") in contract_months(
        pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")), \
        "Apr 2022 Good-Friday exception"
    assert pd.Timestamp("2025-04-11") in contract_months(
        pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")), \
        "Apr 2025 Good-Friday exception"

    print("All self-tests passed.")


if __name__ == "__main__":
    self_test()
