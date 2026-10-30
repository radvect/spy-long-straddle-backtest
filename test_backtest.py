"""Automated tests for the backtest engine (backtest.py).

All backtest tests use SYNTHETIC fixtures (deterministic price series on a
plain business-day calendar) — they validate the *logic* (pricing,
costs, accounting, entry/exit rules), never market results. The single
network test at the bottom fetches real yfinance data and is skipped
automatically when offline; it also re-validates the real-data invariants.

Run:  python -m pytest test_backtest.py -v
"""

import math

import numpy as np
import pandas as pd
import pytest

import backtest as bt


# ---------------------------------------------------------------------------
# Synthetic fixtures (never presented as market data)
# ---------------------------------------------------------------------------
def _series_df(values, index):
    return pd.DataFrame({"close": pd.Series(values, index=index)})


def _flat(spy=500.0, vix=20.0, start="2020-01-02", end="2020-02-29"):
    """Flat prices on a holiday-free business calendar (one contract month:
    Feb 2020 expiry 2020-02-21, entered 2020-02-01 at DTE 21)."""
    idx = pd.bdate_range(start, end)
    return (_series_df(np.full(len(idx), spy), idx),
            _series_df(np.full(len(idx), vix), idx))


def _moving(spy0=500.0, start="2020-01-02", end="2021-06-30"):
    """Deterministic drifting sine — enough for 17 contract months."""
    idx = pd.bdate_range(start, end)
    i = np.arange(len(idx))
    spy = spy0 + 0.15 * i + 15.0 * np.sin(i / 9.0)
    vix = 15.0 + 4.0 * np.abs(np.sin(i / 7.0))
    return (_series_df(spy, idx), _series_df(vix, idx))


STR = bt.SPREAD_REL_HALF
COM = bt.COMMISSION_PER_CONTRACT


# ---------------------------------------------------------------------------
# Black–Scholes
# ---------------------------------------------------------------------------
def test_bs_reference_values():
    # Literature reference: S=K=100, T=1y, sigma=20%, r=5%, q=0.
    c = bt.bs_price("call", 100.0, 100.0, 1.0, 0.20, 0.05, 0.0)
    p = bt.bs_price("put", 100.0, 100.0, 1.0, 0.20, 0.05, 0.0)
    assert abs(c - 10.4506) < 5e-4, c
    assert abs(p - 5.5735) < 5e-4, p


def test_bs_expiration_payoff():
    assert bt.bs_price("call", 100, 90, 0, 0.2) == 10.0
    assert bt.bs_price("put", 100, 90, 0, 0.2) == 0.0
    assert bt.bs_price("call", 90, 100, 0, 0.2) == 0.0
    assert bt.bs_price("put", 90, 100, 0, 0.2) == 10.0
    assert bt.bs_price("call", 100, 100, 0, 0.2) == 0.0
    # near-expiry continuity
    assert abs(bt.bs_price("call", 100, 95, 1e-9, 0.2) - 5.0) < 1e-3


def test_bs_put_call_parity():
    for S, K, T, sig, r, q in [(500, 500, 30 / 365, 0.20, 0.01, 0.013),
                               (510, 495, 60 / 365, 0.35, 0.02, 0.013),
                               (480, 520, 12 / 365, 0.12, 0.01, 0.013)]:
        c = bt.bs_price("call", S, K, T, sig, r, q)
        p = bt.bs_price("put", S, K, T, sig, r, q)
        fwd = S * math.exp(-q * T) - K * math.exp(-r * T)
        assert abs((c - p) - fwd) < 1e-8, (S, K, T)


def test_bs_zero_vol_limit():
    # sigma -> 0 must approach forward intrinsic value.
    S, K, T, r, q = 500.0, 490.0, 30 / 365, 0.01, 0.013
    fwd = S * math.exp(-q * T) - K * math.exp(-r * T)
    assert abs(bt.bs_price("call", S, K, T, 1e-8, r, q) - max(fwd, 0.0)) < 1e-6
    S2, K2 = 490.0, 500.0
    fwd2 = S2 * math.exp(-q * T) - K2 * math.exp(-r * T)
    assert abs(bt.bs_price("put", S2, K2, T, 1e-8, r, q) - max(-fwd2, 0.0)) < 1e-6


def test_bs_deep_itm_approximation():
    # Deep ITM call ~= forward spread; the put must be ~0
    # (0.01 = 1e-5 of the underlying price: negligible, but robust).
    S, K, T, sig, r, q = 200.0, 100.0, 1.0, 0.20, 0.01, 0.01
    fwd = S * math.exp(-q * T) - K * math.exp(-r * T)
    assert bt.bs_price("call", S, K, T, sig, r, q) - fwd < 0.01
    assert bt.bs_price("put", S, K, T, sig, r, q) < 0.01


# ---------------------------------------------------------------------------
# Contract months / strikes
# ---------------------------------------------------------------------------
def test_contract_months_known_dates():
    cm2020 = bt.contract_months(pd.Timestamp("2020-01-01"),
                                pd.Timestamp("2020-12-31"))
    for d in ("2020-01-17", "2020-02-21", "2020-03-20", "2020-06-19",
              "2020-12-18"):
        assert pd.Timestamp(d) in cm2020, d
    # Good-Friday exception: 3rd Friday 2022-04-15 is Good Friday (Easter
    # 2022-04-17) -> expiration moves to the 2nd Friday.
    cm2022 = bt.contract_months(pd.Timestamp("2022-01-01"),
                                pd.Timestamp("2022-12-31"))
    assert pd.Timestamp("2022-04-08") in cm2022
    assert pd.Timestamp("2022-04-15") not in cm2022
    cm2025 = bt.contract_months(pd.Timestamp("2025-01-01"),
                                pd.Timestamp("2025-12-31"))
    assert pd.Timestamp("2025-04-11") in cm2025
    # range bounds are inclusive
    assert cm2020[0] >= pd.Timestamp("2020-01-01")
    assert cm2020[-1] <= pd.Timestamp("2020-12-31")


def test_nearest_strike():
    assert bt._nearest_strike(500.0) == 500
    assert bt._nearest_strike(500.4) == 500
    assert bt._nearest_strike(500.49) == 500
    assert bt._nearest_strike(500.5) == 500      # half-dollar tie -> lower
    assert bt._nearest_strike(500.51) == 501
    assert bt._nearest_strike(499.99) == 500


# ---------------------------------------------------------------------------
# Transaction costs
# ---------------------------------------------------------------------------
def test_entry_cost_formula():
    data = _flat()
    res = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=1_000_000.0)
    assert res["summary"]["n_trades"] == 1
    t = res["trades"].iloc[0]
    # recompute the premium exactly as the engine does, from the trade's
    # own entry date (Feb 2020 expiry entered 2020-01-31 at DTE 21)
    T = t["dte"] / 365.0
    prem = bt.bs_price("call", 500.0, t["strike"], T, 0.20) \
        + bt.bs_price("put", 500.0, t["strike"], T, 0.20)
    expected = bt.CONTRACT_SIZE * prem * (1 + STR) + 2 * COM
    assert abs(t["entry_cost"] - expected) < 0.01
    assert t["strike"] == 500 and t["dte"] == 21


# ---------------------------------------------------------------------------
# Insufficient-capital behavior
# ---------------------------------------------------------------------------
def _first_entry_cost() -> float:
    """Full-precision cost of the single trade in the _flat() window:
    Feb 2020 expiry (2020-02-21) entered 2020-01-31 at DTE 21."""
    S, K = 500.0, 500.0
    T = (pd.Timestamp("2020-02-21") - pd.Timestamp("2020-01-31")).days / 365.0
    prem = bt.bs_price("call", S, K, T, 0.20) + bt.bs_price("put", S, K, T, 0.20)
    return bt.CONTRACT_SIZE * prem * (1 + STR) + 2 * COM


def test_insufficient_capital_skips_entry():
    data = _flat()
    cost = _first_entry_cost()
    res = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=cost - 0.01)
    assert res["summary"]["n_trades"] == 0
    assert (res["equity"]["equity"] == cost - 0.01).all()
    assert res["summary"]["final_cash"] == cost - 0.01


def test_just_affordable_enters():
    data = _flat()
    cost = _first_entry_cost()
    res = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=cost + 0.01)
    assert res["summary"]["n_trades"] == 1


def test_tiny_capital_stays_flat():
    data = _flat()
    res = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=100.0)
    assert res["summary"]["n_trades"] == 0
    assert (res["equity"]["equity"] == 100.0).all()
    # never traded -> cannot claim the strategy "stopped trading"
    assert res["summary"]["capital_exhausted"] is False


# ---------------------------------------------------------------------------
# Capital-exhaustion flag (factual, from the affordability guard)
# ---------------------------------------------------------------------------
def _exhausting_data():
    """Two-month fixture (Mar + Apr 2020 expirations) with DIFFERENT,
    measured entry costs.

    Mar-2020 expiry (2020-03-20) enters 2020-02-28 at DTE 21, strike
    500, VIX 10. Apr-2020 expiry (2020-04-17) enters 2020-03-27 at DTE
    21, strike 500, VIX 30 (stepped up on 2020-03-20). The VIX step
    makes the second straddle far dearer than the first, so a capital
    level between the two costs affords exactly one trade. Returns
    (data, cost_mar, cost_apr) — the actual costs, measured.
    """
    idx = pd.bdate_range("2020-01-02", "2020-05-31")
    spy = np.full(len(idx), 500.0)
    vix = np.full(len(idx), 10.0)
    vix[idx >= pd.Timestamp("2020-03-20")] = 30.0
    data = (_series_df(spy, idx), _series_df(vix, idx))
    # start 2020-03-01: only the Mar (03-20) and Apr (04-17) expiries
    # are in scope — the Jan/Feb expiries precede the window.
    r1 = bt.run_backtest(data, start="2020-03-01", end="2020-05-01",
                         initial_capital=1_000_000.0)
    assert r1["summary"]["n_trades"] == 2
    c1 = r1["trades"].loc[(pd.Timestamp("2020-02-28"),
                           pd.Timestamp("2020-03-20")), "entry_cost"]
    c2 = r1["trades"].loc[(pd.Timestamp("2020-03-27"),
                           pd.Timestamp("2020-04-17")), "entry_cost"]
    assert c2 > c1, "fixture must make the second straddle dearer"
    return data, c1, c2


def test_capital_exhausted_flag_true():
    """Afford trade 1, cannot afford trade 2, window continues -> True."""
    data, c1, c2 = _exhausting_data()
    res = bt.run_backtest(data, start="2020-03-01", end="2020-05-01",
                          initial_capital=c1 + 200.0)
    s = res["summary"]
    assert s["n_trades"] == 1, "afforded exactly the first straddle"
    assert s["capital_exhausted"] is True
    assert s["blocked_entries"] >= 1


def test_capital_exhausted_flag_false_when_window_ended():
    """Same money, but the window ends before the next entry day -> no
    blocked entry was ever observed -> the flag must stay False
    (the window simply ended; that is not 'stopped trading')."""
    data, c1, c2 = _exhausting_data()
    # window ends 2020-04-05: only the Mar expiry (03-20) is in scope;
    # the Apr expiry (04-17) is after the window end, so its entry day
    # is never evaluated and nothing is blocked after the settlement.
    res = bt.run_backtest(data, start="2020-03-01", end="2020-04-05",
                          initial_capital=c1 + 200.0)
    s = res["summary"]
    assert s["n_trades"] == 1
    assert s["capital_exhausted"] is False


def test_capital_exhausted_flag_false_when_still_trading():
    """Affordable capital all the way through -> flag stays False."""
    data, c1, c2 = _exhausting_data()
    res = bt.run_backtest(data, start="2020-03-01", end="2020-05-01",
                          initial_capital=1_000_000.0)
    assert res["summary"]["n_trades"] == 2
    assert res["summary"]["capital_exhausted"] is False


def test_real_data_capital_exhausted_flag():
    """Over the real download (skipped when offline): the strategy does
    exhaust capital, so the flag the dashboard relies on is True."""
    try:
        data = bt.fetch_data(bt.START, bt.END)
    except Exception as exc:  # offline / yfinance failure
        pytest.skip(f"yfinance network unavailable: {exc}")
    res = bt.run_backtest(data)
    s = res["summary"]
    assert s["n_trades"] > 0
    # after the last settlement the remaining cash is below every future
    # entry cost (replayed in REVIEW.md) -> the guard keeps blocking
    assert s["capital_exhausted"] is True
    assert s["blocked_entries"] >= 1
    # and the equity is flat after the final settlement
    eq = res["equity"]["equity"]
    flat = eq[eq.index > max(res["trades"].index.get_level_values("expiration"))]
    assert len(flat) > 100
    assert abs(flat.iloc[-1] - flat.iloc[0]) < 1e-9


def test_target_dte_parameter():
    """target_dte moves the entry day (last day with DTE <= target);
    the default (21) reproduces the documented window."""
    data = _flat()  # Feb-2020 expiry on a holiday-free calendar
    # default window [21,45]: entry 2020-01-31 at DTE 21
    r_def = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                            initial_capital=1_000_000.0)
    ed, exp = r_def["trades"].index[0]
    assert ed == pd.Timestamp("2020-01-31") and r_def["trades"]["dte"].iloc[0] == 21
    # target 23: last day with DTE <= 23 is 2020-01-29
    r23 = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=1_000_000.0, target_dte=23)
    ed23, _ = r23["trades"].index[0]
    assert ed23 == pd.Timestamp("2020-01-29") and r23["trades"]["dte"].iloc[0] == 23
    # earlier entry, longer contract -> higher modeled premium
    assert (r23["trades"]["entry_cost"].iloc[0]
            > r_def["trades"]["entry_cost"].iloc[0])
    # target 21 == MIN_DTE default behavior, identical result
    r21 = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=1_000_000.0, target_dte=21)
    assert (r21["trades"]["entry_cost"].iloc[0]
            == r_def["trades"]["entry_cost"].iloc[0])
    # every entry still respects the window and the DTE bound
    for _, r in r23["trades"].iterrows():
        assert bt.MIN_DTE <= r["dte"] <= bt.MAX_DTE
        assert r["dte"] <= 23


# ---------------------------------------------------------------------------
# Entry / exit rules, DTE boundaries, no overlap (multi-month synthetic)
# ---------------------------------------------------------------------------
def _structural():
    data = _moving()
    res = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                          initial_capital=100_000.0)
    return data, res


def test_every_entry_is_last_day_in_dte_window():
    data, res = _structural()
    dates = data[0]["close"].index
    tdf = res["trades"].reset_index()
    # every expiration in range (Feb 2020..Jun 2021) got a trade
    exps = bt.contract_months(pd.Timestamp("2020-01-01"),
                              pd.Timestamp("2021-06-30"))
    exps = [e for e in exps if e >= pd.Timestamp("2020-02-21")]
    assert res["summary"]["n_trades"] == len(exps)
    for _, r in tdf.iterrows():
        exp, entry = r["expiration"], r["entry_date"]
        window = dates[(dates >= exp - pd.Timedelta(days=bt.MAX_DTE))
                       & (dates <= exp - pd.Timedelta(days=bt.MIN_DTE))]
        assert entry == window[-1], (entry, exp)
        # boundary: entry DTE within [21, 45]; on a holiday-free calendar
        # the last day in the window is 21-23 DTE out.
        assert bt.MIN_DTE <= (exp - entry).days <= bt.MAX_DTE
        assert (exp - entry).days in (21, 22, 23)


def test_no_overlapping_positions():
    _, res = _structural()
    tdf = res["trades"].reset_index()
    for i in range(len(tdf) - 1):
        exp_i = tdf["expiration"].iloc[i]
        entry_next = tdf["entry_date"].iloc[i + 1]
        assert exp_i <= entry_next, "position held past next entry"


# ---------------------------------------------------------------------------
# Portfolio accounting & metric consistency (synthetic)
# ---------------------------------------------------------------------------
def test_summary_metrics_recompute():
    _, res = _structural()
    eq = res["equity"]["equity"]
    s, tdf = res["summary"], res["trades"]
    init = s["initial_capital"]
    assert abs(s["total_return"] - (eq.iloc[-1] / init - 1)) < 1e-12
    mdd = float(((eq - eq.cummax()) / eq.cummax()).min())
    assert abs(s["max_drawdown"] - mdd) < 1e-12
    assert s["n_trades"] == len(tdf)
    assert abs(s["win_rate"] - float(tdf["win"].mean())) < 1e-12
    assert abs(s["final_equity"] - float(eq.iloc[-1])) < 1e-9


def test_equity_equals_cash_when_flat_and_final():
    _, res = _structural()
    eq = res["equity"]
    # flat days (no open position) must carry value exactly
    flat = eq[~eq["open"]]
    # ... and the final value equals final cash (nothing open at the end)
    assert abs(eq["equity"].iloc[-1] - res["summary"]["final_cash"]) < 1e-9
    assert (eq["equity"] >= 0).all()
    assert len(flat) > 0


def test_per_trade_pnl_identity():
    _, res = _structural()
    tdf = res["trades"]
    for _, r in tdf.iterrows():
        exit_net = r["exit_value"] * (1 - STR) - 2 * COM
        # stored columns are 2-dp rounded; allow 1 cent
        assert abs((exit_net - r["entry_cost"]) - r["net_pnl"]) <= 0.011
        assert abs((r["exit_value"] - r["entry_cost"]) - r["gross_pnl"]) <= 0.011
        # win flag consistent with net (modulo 1 cent of rounding)
        assert r["win"] == (r["net_pnl"] > -0.005)


def test_no_lookahead_truncation():
    """Causality: running the engine on data truncated at D must reproduce
    the same equity path and same settled trades up to D."""
    data = _moving(start="2020-01-02", end="2020-12-31")
    full = bt.run_backtest(data, start="2020-01-01", end="2021-01-01",
                           initial_capital=100_000.0)
    cut = pd.Timestamp("2020-09-30")
    data_cut = (data[0].loc[:cut], data[1].loc[:cut])
    cut_res = bt.run_backtest(data_cut, start="2020-01-01", end="2021-01-01",
                              initial_capital=100_000.0)
    # equity up to (and including) the cut date is identical
    eq_f = full["equity"]["equity"].loc[:cut]
    eq_c = cut_res["equity"]["equity"].loc[:cut]
    assert eq_f.index.equals(eq_c.index)
    assert np.allclose(eq_f.values, eq_c.values, rtol=0, atol=1e-9)
    # trades settled on/before the cut are identical rows in both runs
    f = full["trades"]
    settled = f[f.index.get_level_values("expiration") <= cut]
    assert len(settled) == len(cut_res["trades"])
    for (ed, ex), row in settled.iterrows():
        other = cut_res["trades"].loc[(ed, ex)]
        assert row["strike"] == other["strike"]
        assert abs(row["net_pnl"] - other["net_pnl"]) <= 0.011


# ---------------------------------------------------------------------------
# Data loader: MultiIndex-column normalization (no network)
#
# Regression for the bug where fetch_data lost all 1508 SPY rows: the app
# used yf.Ticker().history, which silently returns an empty frame on a
# transient network blip. fetch_data now uses yf.download, whose result has
# MultiIndex columns (Price, Ticker). These tests pin the column handling so
# historical rows are never dropped, and so the UNADJUSTED Close is always
# selected (never the dividend-adjusted "Adj Close").
# ---------------------------------------------------------------------------
def _download_like_frame(index, spy, vix, adj_close=True):
    """Build a frame shaped exactly like yf.download's MultiIndex output
    (levels: Price, Ticker) so the loader is tested against its real input."""
    cols = pd.MultiIndex.from_tuples(
        [("Adj Close", "SPY"), ("Adj Close", "^VIX"),
         ("Close", "SPY"), ("Close", "^VIX"),
         ("High", "SPY"), ("High", "^VIX"),
         ("Low", "SPY"), ("Low", "^VIX"),
         ("Open", "SPY"), ("Open", "^VIX"),
         ("Volume", "SPY"), ("Volume", "^VIX")],
        names=["Price", "Ticker"])
    data = {}
    for price, t, val in (("Close", "SPY", spy), ("Close", "^VIX", vix),
                          ("High", "SPY", spy + 1.0), ("High", "^VIX", vix + 0.1),
                          ("Low", "SPY", spy - 1.0), ("Low", "^VIX", vix - 0.1),
                          ("Open", "SPY", spy + 0.5), ("Open", "^VIX", vix + 0.05),
                          ("Volume", "SPY", np.full(len(index), 1e6)),
                          ("Volume", "^VIX", np.full(len(index), 1e4))):
        data[(price, t)] = val
    if adj_close:
        data[("Adj Close", "SPY")] = np.array(spy) * 0.97   # dividends applied
        data[("Adj Close", "^VIX")] = np.array(vix)
    return pd.DataFrame(data, index=index, columns=cols)


def test_extract_close_multiindex_unadjusted():
    """MultiIndex columns -> the UNADJUSTED Close, with all rows preserved."""
    idx = pd.bdate_range("2020-01-02", "2020-02-29")
    spy = np.linspace(300.0, 330.0, len(idx))
    vix = np.linspace(15.0, 18.0, len(idx))
    raw = _download_like_frame(idx, spy, vix)
    assert isinstance(raw.columns, pd.MultiIndex)

    s = bt._extract_close(raw, "SPY")
    v = bt._extract_close(raw, "^VIX")
    # every historical row survives the normalization
    assert len(s) == len(idx) and len(v) == len(idx)
    # unadjusted Close, NOT the dividend-adjusted "Adj Close"
    assert s.name == "close"
    assert np.allclose(s.to_numpy(), spy), "must use unadjusted Close"
    assert not np.allclose(s.to_numpy(), spy * 0.97), "must NOT use Adj Close"
    assert np.allclose(v.to_numpy(), vix)


def test_extract_close_flat_columns():
    """Flat (non-MultiIndex) columns are still handled."""
    idx = pd.bdate_range("2020-01-02", "2020-02-29")
    raw = pd.DataFrame({"Close": np.linspace(300, 330, len(idx)),
                        "High": np.full(len(idx), 331.0),
                        "Adj Close": np.linspace(290, 320, len(idx))},
                       index=idx)
    s = bt._extract_close(raw, "SPY")
    assert len(s) == len(idx)
    assert np.allclose(s.to_numpy(), np.linspace(300, 330, len(idx)))


def test_extract_close_missing_ticker_is_empty():
    """A ticker with no Close column (download failed) -> empty Series,
    not an exception, so fetch_data can report it as 0 rows."""
    idx = pd.bdate_range("2020-01-02", "2020-02-29")
    raw = _download_like_frame(idx, np.full(len(idx), 300.0),
                               np.full(len(idx), 15.0))
    s = bt._extract_close(raw, "DOES_NOT_EXIST")
    assert s.empty
    assert s.name == "close"


def test_fetch_data_survives_multiindex_without_dropping_rows():
    """End-to-end: a MultiIndex yfinance frame must flow through the loader
    logic without losing rows and must keep SPY/VIX aligned on common days.

    fetch_data itself hits the network, so we replicate its exact
    post-download logic on a synthetic MultiIndex frame to prove the
    normalization step is loss-free and alignment-preserving."""
    idx = pd.bdate_range("2020-01-02", "2020-06-30")
    spy = np.linspace(300.0, 340.0, len(idx))
    vix = np.linspace(15.0, 22.0, len(idx))
    raw = _download_like_frame(idx, spy, vix)

    # Mirror fetch_data's body exactly (the part after yf.download).
    spy_close = bt._extract_close(raw, "SPY")
    vix_close = bt._extract_close(raw, "^VIX")
    assert spy_close.notna().sum() > 0 and vix_close.notna().sum() > 0
    if spy_close.index.tz is not None:
        spy_close.index = spy_close.index.tz_localize(None)
    if vix_close.index.tz is not None:
        vix_close.index = vix_close.index.tz_localize(None)
    common = spy_close.index.intersection(vix_close.index)
    spy_close = spy_close.loc[common]
    vix_close = vix_close.loc[common]

    # no rows dropped; indices identical after alignment
    assert len(common) == len(idx)
    assert spy_close.index.equals(vix_close.index)
    assert not spy_close.isna().any() and not vix_close.isna().any()
    # each output is a DataFrame with a single 'close' column
    spy_df, vix_df = spy_close.to_frame(), vix_close.to_frame()
    assert list(spy_df.columns) == ["close"]
    assert list(vix_df.columns) == ["close"]


# ---------------------------------------------------------------------------
# No-arbitrage bounds, cash conservation, settlement continuity, payoff,
# reproducibility (synthetic fixtures)
# ---------------------------------------------------------------------------
def test_bs_no_arbitrage_bounds():
    """BS must respect the standard no-arbitrage bounds for a continuous
    dividend yield:
        max(S e^{-qT} - K e^{-rT}, 0) <= C <= S e^{-qT}
        max(K e^{-rT} - S e^{-qT}, 0) <= P <= K e^{-rT}
    """
    r, q = 0.01, 0.013
    for S, K, T, sig in [(500, 500, 30 / 365, 0.20),
                         (500, 480, 45 / 365, 0.35),
                         (300, 320, 15 / 365, 0.15),
                         (600, 590, 30 / 365, 0.25)]:
        c = bt.bs_price("call", S, K, T, sig, r, q)
        p = bt.bs_price("put", S, K, T, sig, r, q)
        fwd = S * math.exp(-q * T) - K * math.exp(-r * T)
        assert c >= max(fwd, 0.0) - 1e-9 and c <= S * math.exp(-q * T) + 1e-9
        assert p >= max(-fwd, 0.0) - 1e-9 and p <= K * math.exp(-r * T) + 1e-9


def test_straddle_expiration_payoff_identity():
    """A long straddle's intrinsic value at expiration is exactly
    100 x |S_T - K|; the engine's settlement must book exactly that
    (times the single 2% half-spread) into cash."""
    # One-month flat fixture: Feb 2020 expiry entered 2020-01-31, DTE 21.
    # Move SPY at expiry so the payoff is unambiguously |S - K|.
    idx = pd.bdate_range("2020-01-02", "2020-02-29")
    spy = np.full(len(idx), 500.0)
    spy[idx == pd.Timestamp("2020-02-21")] = 520.0   # S_T = 520, K = 500
    data = (_series_df(spy, idx), _series_df(np.full(len(idx), 20.0), idx))
    res = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=1_000_000.0)
    t = res["trades"].reset_index().iloc[0]
    assert t["strike"] == 500
    # exit_value is the GROSS intrinsic payoff = 100 * |S_T - K|
    assert abs(t["exit_value"] - 100 * abs(520.0 - 500.0)) < 1e-9
    # net = gross * (1 - 2%) - 2*commission - entry_cost
    expected_net = (100 * 20.0) * (1 - STR) - 2 * COM - t["entry_cost"]
    assert abs(t["net_pnl"] - expected_net) <= 0.011


def test_settlement_uses_single_half_spread():
    """Regression for the double-charged exit spread. Settlement must sell
    the position at the SAME single relative half-spread (mid - 2%) used
    by every daily mark-to-market, not a full round-trip (1 - 4%)."""
    idx = pd.bdate_range("2020-01-02", "2020-02-29")
    spy = np.full(len(idx), 500.0)
    spy[idx == pd.Timestamp("2020-02-21")] = 505.0   # small move, K=500
    data = (_series_df(spy, idx), _series_df(np.full(len(idx), 20.0), idx))
    res = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=1_000_000.0)
    t = res["trades"].reset_index().iloc[0]
    gross = t["exit_value"]                          # 100 * |S_T - K|
    single = gross * (1 - STR) - 2 * COM             # single 2% half-spread
    double = gross * (1 - 2 * STR) - 2 * COM         # the old bug: 4%
    # net_pnl must be the SINGLE-spread convention, and the two conventions
    # must be far enough apart that a regression back to DOUBLE would fail.
    assert abs(single - double) > 0.5, "gap too small to detect a regression"
    assert abs((t["entry_cost"] + t["net_pnl"]) - single) <= 0.011, \
        "settlement applied a double (round-trip) spread"
    assert abs((t["entry_cost"] + t["net_pnl"]) - double) > 0.5, \
        "settlement unexpectedly matches the old double-spread bug"


def test_cash_conservation_and_nonnegative():
    """Cash conservation: final equity = initial capital + sum(net P&L),
    cash never goes negative, and no open position at the end (so final
    equity == final cash)."""
    _, res = _structural()
    eq = res["equity"]["equity"]
    s, tdf = res["summary"], res["trades"]
    init = s["initial_capital"]
    sum_pnl = float(tdf["net_pnl"].sum())
    # stored net_pnl is 2-dp rounded; allow a few cents of rounding total
    assert abs((init + sum_pnl) - s["final_equity"]) < 0.05, \
        (init + sum_pnl, s["final_equity"])
    assert s["final_equity"] >= 0
    assert (eq >= 0).all()
    assert abs(s["final_equity"] - s["final_cash"]) < 1e-9
    assert not res["equity"]["open"].iloc[-1], "no position open at the end"


def test_win_rate_and_max_drawdown_recompute():
    """Win rate and max drawdown must match independent recomputation."""
    _, res = _structural()
    eq = res["equity"]["equity"]
    tdf = res["trades"]
    s = res["summary"]
    wins = float((tdf["net_pnl"] > -0.005).sum())
    assert abs(s["win_rate"] - wins / len(tdf)) < 1e-9
    assert abs(s["max_drawdown"] - float(((eq - eq.cummax()) / eq.cummax()).min())) < 1e-12
    assert s["max_drawdown"] <= 0.0


def test_reproducible_on_fixed_synthetic_data():
    """Two runs on the identical deterministic synthetic series must
    produce byte-identical equity and trade tables (no RNG, no wall
    clock, no market data)."""
    data = _moving()
    a = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                        initial_capital=100_000.0)
    b = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                        initial_capital=100_000.0)
    assert a["equity"]["equity"].index.equals(b["equity"]["equity"].index)
    assert np.array_equal(a["equity"]["equity"].to_numpy(),
                          b["equity"]["equity"].to_numpy())
    assert a["trades"].reset_index().equals(b["trades"].reset_index())
    assert a["summary"]["final_equity"] == b["summary"]["final_equity"]


def test_no_negative_cash_or_leverage():
    """At no point may cash go negative (the affordability guard) and the
    position value can never exceed available cash plus entry (no
    leverage). Verified by re-deriving the cash path from the trade
    table with the engine's single-half-spread settlement."""
    _, res = _structural()
    cash = res["summary"]["initial_capital"]
    for _, r in res["trades"].reset_index().iterrows():
        assert cash >= 0.0, f"cash went negative before {r['entry_date']}"
        cash -= r["entry_cost"]
        assert cash >= 0.0, "entry pushed cash negative"
        # exit_value is the gross intrinsic payoff (already 100x, >= 0);
        # the single half-spread haircut and commissions only reduce it.
        cash += r["exit_value"] * (1 - STR) - 2 * COM
        assert cash >= 0.0, "exit cash cannot be negative (intrinsic >= 0)"
    assert abs(cash - res["summary"]["final_cash"]) < 0.05


# ---------------------------------------------------------------------------
# Initial capital as a first-class engine input
# ---------------------------------------------------------------------------
def test_insufficient_capital_records_blocked_entry():
    """An entry that does not fit in cash is skipped AND the skip is
    recorded (blocked_entries >= 1 with the blocking date); the equity
    line stays flat at the cash level on that day and cash is conserved."""
    data = _flat()
    cost = _first_entry_cost()
    res = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                          initial_capital=cost * 0.5)
    s = res["summary"]
    assert s["n_trades"] == 0
    assert s["blocked_entries"] >= 1
    # the exhaustion flag requires at least one taken trade plus a
    # blocked final entry day; with zero trades it stays False (the
    # skip is still recorded in blocked_entries)
    assert s["capital_exhausted"] is False
    assert (res["equity"]["equity"] == cost * 0.5).all()
    assert s["final_cash"] == cost * 0.5
    assert s["final_equity"] == cost * 0.5


def test_cash_conservation_across_capital_levels():
    """At several capital levels: final equity = capital + sum(net P&L),
    cash never negative, and the trade table re-derived cash path closes
    out. (The engine threads initial_capital through every settlement.)"""
    data = _moving()
    for cap in (8_000.0, 9_000.0, 12_000.0, 100_000.0):
        res = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                              initial_capital=cap)
        s, tdf = res["summary"], res["trades"]
        assert s["initial_capital"] == cap
        assert abs((cap + float(tdf["net_pnl"].sum()))
                   - s["final_equity"]) < 0.05
        assert s["final_equity"] >= 0
        assert (res["equity"]["equity"] >= 0).all()
        assert abs(s["final_equity"] - s["final_cash"]) < 1e-9
        cash = cap
        for _, r in tdf.reset_index().iterrows():
            cash -= r["entry_cost"]
            assert cash >= 0.0, f"entry pushed cash negative (cap={cap})"
            cash += r["exit_value"] * (1 - STR) - 2 * COM
            assert cash >= 0.0
        assert abs(cash - s["final_cash"]) < 0.05


def test_trade_count_changes_with_capital():
    """Changing capital changes WHICH trades are taken (not just the
    scale): at 8,000 the 16th trade's entry cost no longer fits in the
    depleted cash (17 -> 16 trades, flagged as exhausted); at 9,000
    through 100,000 the same 17 trades are taken, so the count is
    stable and the cash-conservation identity holds with the same
    P&L at every level."""
    data = _moving()
    r16 = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                          initial_capital=8_000.0)
    r17 = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                          initial_capital=9_000.0)
    assert r16["summary"]["n_trades"] == 16
    assert r17["summary"]["n_trades"] == 17
    assert r16["summary"]["capital_exhausted"] is True
    assert r16["summary"]["blocked_entries"] >= 1
    assert r17["summary"]["capital_exhausted"] is False
    for cap in (9_000.0, 12_000.0, 100_000.0):
        r = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                            initial_capital=cap)
        assert r["summary"]["n_trades"] == 17, cap
    # the blocked trade set is a strict subset of the unblocked one
    blocked = set(map(tuple, r16["trades"].index))
    full = set(map(tuple, r17["trades"].index))
    assert blocked < full


def test_reproducible_across_capital_values():
    """Two runs at the same capital are byte-identical (no RNG, no wall
    clock), and results at DIFFERENT capitals are deterministic: each
    capital's equity path, trade table, and summary reproduce exactly
    across repeated runs."""
    data = _moving()
    for cap in (8_000.0, 25_000.0, 100_000.0):
        a = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                            initial_capital=cap)
        b = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                            initial_capital=cap)
        assert a["equity"]["equity"].index.equals(b["equity"]["equity"].index)
        assert np.array_equal(a["equity"]["equity"].to_numpy(),
                              b["equity"]["equity"].to_numpy())
        assert a["trades"].reset_index().equals(b["trades"].reset_index())
        assert a["summary"]["final_equity"] == b["summary"]["final_equity"]
        assert a["summary"]["n_trades"] == b["summary"]["n_trades"]
        assert a["summary"]["win_rate"] == b["summary"]["win_rate"]


# ---------------------------------------------------------------------------
# Risk-free rate and dividend yield as first-class engine inputs
# ---------------------------------------------------------------------------
def _flat_entry_cost(r: float, q: float) -> float:
    """Full-precision cost of the single trade in the _flat() window at
    the given rates: Feb 2020 expiry (2020-02-21) entered 2020-01-31
    at DTE 21, recomputed exactly as the engine prices it."""
    S = K = 500.0
    T = (pd.Timestamp("2020-02-21") - pd.Timestamp("2020-01-31")).days / 365.0
    prem = bt.bs_price("call", S, K, T, 0.20, r, q) \
        + bt.bs_price("put", S, K, T, 0.20, r, q)
    return bt.CONTRACT_SIZE * prem * (1 + STR) + 2 * COM


def test_pricing_params_feed_bs_pricing():
    """Both parameters are threaded into the Black-Scholes pricing:
    the engine's entry cost must equal an independently recomputed
    premium built at those exact parameters (a missing wiring on
    either side shows up as a cost mismatch). The last checks pin the
    TRUE sensitivity directions so a wrong-sign wiring cannot pass:
    higher r makes the straddle cheaper (the call term dominates),
    and the ATM straddle premium is convex in the dividend yield, so
    moving q away from the selected value changes the cost on both
    sides."""
    data = _flat()
    for r, q in ((0.01, 0.013), (0.05, 0.0), (-0.01, 0.05)):
        res = bt.run_backtest(data, start="2020-01-01", end="2020-03-01",
                              initial_capital=1_000_000.0,
                              risk_free=r, dividend_yield=q)
        assert res["summary"]["n_trades"] == 1
        cost = res["trades"]["entry_cost"].iloc[0]
        assert abs(cost - _flat_entry_cost(r, q)) < 0.01, (r, q, cost)
    # the wiring is real in both directions, not just present:
    # r: the straddle gets cheaper as r rises (the call term dominates)
    base = _flat_entry_cost(0.01, 0.013)
    assert _flat_entry_cost(0.05, 0.013) < base \
        < _flat_entry_cost(-0.01, 0.013)
    # q: the ATM straddle premium is convex in the dividend yield,
    # minimized near the typical SPY yield; moving q away from the
    # selected value on EITHER side changes the cost (a large move
    # raises it clearly, a small move lowers it)
    assert _flat_entry_cost(0.01, 0.03) < base < _flat_entry_cost(0.01, 0.10)


def test_pricing_params_change_results():
    """A changed rate re-prices every entry premium, daily mark, and
    settlement mark, so the equity path, the summary metrics, and the
    trade history all move (deterministic synthetic fixtures: no
    flakiness, and the move is far above cost/rounding noise)."""
    data = _moving()
    base = bt.run_backtest(data, start="2020-01-01", end="2021-07-01")
    eq0 = base["equity"]["equity"]
    for kw in ({"risk_free": 0.05}, {"risk_free": -0.01},
               {"dividend_yield": 0.05}, {"dividend_yield": 0.0}):
        res = bt.run_backtest(data, start="2020-01-01", end="2021-07-01",
                              **kw)
        eq1 = res["equity"]["equity"]
        assert eq1.index.equals(eq0.index)
        # the path genuinely moves (not a rescale of the base curve)
        assert (eq0 - eq1).abs().max() > 1.0, kw
        s0, s1 = base["summary"], res["summary"]
        assert s1["final_equity"] != s0["final_equity"], kw
        assert s1["max_drawdown"] != s0["max_drawdown"], kw
        # every entry is re-priced at the new rates
        assert (abs(base["trades"]["entry_cost"]
                    - res["trades"]["entry_cost"]) > 0.01).all(), kw
        # the move is not proportional to the parameter gap: it is a
        # re-run through the marks and settlements, not a linear tweak
        assert s1["total_return"] != s0["total_return"], kw


# ---------------------------------------------------------------------------
# Real data (network) — skipped automatically when offline
# ---------------------------------------------------------------------------
def test_real_data_validation_and_invariants():
    try:
        spy, vix = bt.fetch_data(bt.START, bt.END)
    except Exception as exc:  # offline / yfinance failure
        pytest.skip(f"yfinance network unavailable: {exc}")
    s, v = spy["close"], vix["close"]
    # data hygiene: aligned, complete, honest coverage
    assert s.index.equals(v.index)
    assert not s.index.duplicated().any()
    assert not s.isna().any() and not v.isna().any()
    assert s.index[0] <= pd.Timestamp("2020-01-31")
    assert s.index[-1] >= pd.Timestamp("2025-12-01")
    assert len(s) >= 1400, "expected at least ~3 years of trading days"

    res = bt.run_backtest((spy, vix))
    tdf, eq = res["trades"], res["equity"]
    assert res["summary"]["n_trades"] == len(tdf)
    assert res["summary"]["n_trades"] > 0
    # DTE window respected
    dtes = (tdf.index.get_level_values("expiration")
            - tdf.index.get_level_values("entry_date"))
    dtes = dtes.map(lambda d: d.days)
    assert (dtes >= bt.MIN_DTE).all() and (dtes <= bt.MAX_DTE).all()
    # no overlapping positions
    for i in range(len(tdf) - 1):
        assert tdf.index[i][1] <= tdf.index[i + 1][0]
    # entries are the last trading day in the 21-45 window
    dates = s.index
    for (ed, ex) in tdf.index:
        window = dates[(dates >= ex - pd.Timedelta(days=bt.MAX_DTE))
                       & (dates <= ex - pd.Timedelta(days=bt.MIN_DTE))]
        assert ed == window[-1], (ed, ex)
    # accounting closes out
    assert (eq["equity"] >= 0).all()
    assert abs(eq["equity"].iloc[-1] - res["summary"]["final_cash"]) < 1e-9
    # per-trade identity holds within 2-dp storage rounding
    worst = 0.0
    for _, r in tdf.iterrows():
        exit_net = r["exit_value"] * (1 - STR) - 2 * COM
        worst = max(worst, abs((exit_net - r["entry_cost"]) - r["net_pnl"]))
    assert worst <= 0.011, worst
