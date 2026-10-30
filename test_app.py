"""UI smoke tests for app.py via Streamlit's AppTest (in-process).

These tests render the actual dashboard script and assert on the rendered
elements — header, controls (mode, Target DTE, Initial Capital), cards,
charts, series selection, the trade table, the capital-exhaustion note,
and the methodology & assumptions section (structure + values bound to
the live configuration, including the initial capital). They
are NOT a browser/visual check: no pixel-level or layout verification
is performed.

Run:  python -m pytest test_app.py -v
"""

import base64
import html as html_mod
import json
import re

import numpy as np
from streamlit.testing.v1 import AppTest

import backtest as bt


def _trace_y(t):
    """Decode a plotly y array as serialized in the AppTest proto spec."""
    y = t["y"]
    if isinstance(y, dict):
        if "bdata" in y:
            return np.frombuffer(base64.b64decode(y["bdata"]), dtype=y["dtype"])
        if "i8" in y:
            return np.array(y["i8"])
        raise AssertionError(f"unexpected y encoding: {y}")
    return np.array(y, dtype=float)


def _traces(at):
    fig = json.loads(at.get("plotly_chart")[0].proto.spec)
    return fig["data"], fig["layout"]


def _all_text(at):
    """All rendered text: markdown, captions, and info boxes."""
    return "\n".join(b.value for blocks in (
        at.get("markdown"), at.get("caption"), at.get("info"),
        at.get("title"), at.get("subheader")) for b in blocks)


def _cards(at):
    return [m.value for m in at.get("markdown")
            if m.value.lstrip().startswith('<div class="metric-card')]


def _trades_html(at):
    return next(m.value for m in at.get("markdown")
                if m.value.lstrip().startswith(
                    '<div class="trades-table-wrap'))


def _mo_html(at):
    """The methodology & assumptions section (HTML, rendered inline)."""
    return next(m.value for m in at.get("markdown")
                if m.value.lstrip().startswith('<div class="mo-section'))


def _run():
    at = AppTest.from_file("app.py", default_timeout=600)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def test_default_render():
    at = _run()

    # header: title + one short subtitle, no long paragraph
    assert at.title[0].value == "SPY Long Straddle Backtest"
    text = _all_text(at)
    assert "Monthly SPY options strategy | 2020-2025 | Modeled prices" in text
    # the old verbose strategy paragraph is gone
    assert "at-the-money call + one at-the-money put" not in text

    # strategy controls: segmented mode + two compact sliders
    # (21–45, step 1, default 30; ~400px wide each via CSS beside the
    # selector — the old full-width slider was compacted, not removed)
    seg = at.segmented_control[0]
    assert seg.options == ["Fixed DTE", "Optimal DTE (TBD)"]
    assert seg.value == "Fixed DTE"
    sl = at.slider[0]
    assert sl.label == "Target DTE"
    assert sl.min == bt.MIN_DTE
    assert sl.max == bt.MAX_DTE
    assert sl.step == 1
    assert sl.value == 30
    # the capital slider sits next to the DTE slider: $5,000-$100,000
    # in $5,000 steps, default $10,000, displayed in USD
    cap = at.slider[1]
    assert cap.label == "Initial Capital"
    assert (cap.min, cap.max, cap.step, cap.value) == \
        (5_000, 100_000, 5_000, 10_000)
    assert cap.proto.format == "$%,d"

    # two secondary "Pricing Assumptions" number inputs (not sliders)
    # below the primary sliders, defaulting to the engine rates in
    # percent units; the risk-free rate may be negative, the dividend
    # yield is bounded at zero
    nis = at.number_input
    assert len(nis) == 2, len(nis)
    rf = next(n for n in nis if n.label == "Risk-Free Rate (%)")
    assert (rf.min, rf.max, rf.step, rf.value) == (-10.0, 20.0, 0.1, 1.0)
    assert rf.proto.format == "%.1f"
    dy = next(n for n in nis if n.label == "Dividend Yield (%)")
    assert (dy.min, dy.max, dy.step, dy.value) == (0.0, 10.0, 0.1, 1.3)
    assert dy.proto.format == "%.1f"
    # defaults reproduce the engine constants
    assert rf.value == bt.RISK_FREE_RATE * 100.0
    assert dy.value == bt.DIVIDEND_YIELD * 100.0

    # four metric cards with the expected labels
    cards = _cards(at)
    assert len(cards) == 4, cards
    for label in ("Total Return", "Max Drawdown", "Win Rate", "Trades"):
        assert any(f'metric-label">{label}<' in c for c in cards), label
    # tones: return is colored by sign, the rest are neutral
    tones = [re.search(r'metric-card (\w+)', c).group(1) for c in cards]
    values = [re.search(r'metric-value">([^<]+)<', c).group(1) for c in cards]
    expected_ret = "neg" if values[0].startswith("-") else "pos"
    assert tones == [expected_ret, "neutral", "neutral", "neutral"], tones

    # series selector: SPY only (no SPX or other underlyings), both
    # series selected by default
    ms = at.multiselect[0]
    assert ms.options == ["Strategy", "SPY"]
    assert ms.value == ms.options, "default selection is both series"
    assert "SPX" not in " ".join(ms.options)

    # equity chart: two traces in fixed order, white background, 400px
    data, layout = _traces(at)
    names = [t["name"] for t in data]
    assert names == ["Strategy", "SPY"]
    assert layout["height"] == 400
    assert layout["plot_bgcolor"] == "#ffffff"
    assert layout["paper_bgcolor"] == "#ffffff"
    assert layout["hovermode"] == "x unified"
    # the chart title is the fixed label "Portfolio Equity"; the capital
    # framing lives in the y-axis and the tooltip, not in the title
    assert layout["title"]["text"] == "Portfolio Equity"
    # y axis reads real dollars; the old "normalized to $..." subtitle
    # is gone
    assert layout["yaxis"]["title"]["text"] == "Portfolio Value (USD)"
    # explicit professional palette (not Plotly's default cycle):
    # the strategy is dominant (blue, 3px, solid); SPY solid orange
    expected_style = {"Strategy": ("#2563EB", 3.0, "solid"),
                      "SPY": ("#EA580C", 2.0, "solid")}
    for t in data:
        c, w, d = t["line"]["color"], t["line"]["width"], t["line"]["dash"]
        assert (c, w, d) == expected_style[t["name"]], (t["name"], c, w, d)
        y = _trace_y(t)
        first = float(y[~np.isnan(y)][0])
        assert abs(first - 10_000.0) < 1e-6, (t["name"], first)

    # drawdown chart: muted red #DC515B with a subtle fill, 190px
    dd = json.loads(at.get("plotly_chart")[1].proto.spec)
    assert dd["layout"]["height"] == 190
    assert dd["data"][0]["line"]["color"] == "#DC515B"
    assert dd["data"][0]["fill"] == "tozeroy"
    assert len(dd["data"]) == 1

    # trade table: the exact requested columns, one row per trade
    html = _trades_html(at)
    for col in ("Entry Date", "Expiration", "Strike", "DTE",
                "Entry Cost", "Exit Value", "Net P&L"):
        # app.py HTML-escapes the header, so match the escaped form
        assert f"<th>{html_mod.escape(col)}</th>" in html, col
    n_trades = int(re.search(r'metric-value">(\d+)<', cards[3]).group(1))
    assert html.count("<tr>") - 1 == n_trades, \
        "table rows must equal the Trades card"
    # no removed columns leak back in
    for gone in ("VIX at Entry", "Win", "Exit Value (Gross)"):
        assert gone not in html

    # capital-exhaustion note: shown only when the engine's flag is True
    # (recomputed at the dashboard's slider default, 30), and it must
    # distinguish skipped entries from a permanent strategy change (it
    # says entries were skipped until the window ended — it never claims
    # the strategy stopped permanently)
    info = "\n".join(b.value for b in at.get("info"))
    if _cards_capital_flag(at):
        assert "Insufficient capital" in info
        assert "entry" in info and "window" in info
    else:
        assert "Insufficient capital" not in info

    # methodology: compact summary + disclosure + three accordions
    # collapsed by default, values bound to engine constants + the live
    # slider (default 30)
    mo = _mo_html(at)
    # summary line: strategy name, Modeled badge, four facts
    assert "SPY Long Straddle" in mo
    assert "Modeled</span>" in mo
    assert "Entry: <b>Monthly</b>" in mo
    assert "Target DTE: <b>30 days</b>" in mo
    assert "Capital: <b>$10,000</b>" in mo
    assert "Exit: <b>Expiration</b>" in mo
    # the critical disclosure, visible outside the accordions
    assert ("Option prices are modeled, not historical "
            "market quotes.") in mo
    # one expandable group + three accordions, all collapsed by default
    assert "Pricing, execution &amp; limitations" in mo
    for acc in ("Entry &amp; exit rules", "Pricing &amp; costs",
                "Limitations &amp; data"):
        assert acc in mo, acc
    assert mo.count("<details") == 4  # outer group + 3 accordions
    assert re.search(r"<details[^>]*open", mo) is None, \
        "accordions must be collapsed by default"
    # the accordions state the actual implementation; values read from
    # engine constants and the live slider, none hardcoded
    for needle in (
        "Last trading day whose DTE is still inside the "
        f"30-{bt.MAX_DTE}-day window",
        "Nearest $1 strike", "ties go to the lower strike",
        "3rd Friday (2nd Friday when the 3rd is Good Friday)",
        "Held to expiration, marked to intrinsic value",
        f"{bt.CONTRACT_SIZE}-share multiplier",
        "Black-Scholes modeled values",
        "30-day index proxy, not option IV",
        # pricing rates bound to the live inputs (not hardcoded): the
        # rows state the input range and the currently selected value
        f"Input range -10.0% to 20.0% in 0.1% steps; live value "
        f"{bt.RISK_FREE_RATE * 100.0:.1f}% flat. May be negative.",
        f"Input range 0.0% to 10.0% in 0.1% steps; live value "
        f"{bt.DIVIDEND_YIELD * 100.0:.1f}% continuous. Bounded at zero.",
        "$5 per contract per leg",
        "2% relative half-spread",
        f"{bt.CONTRACT_SIZE} shares per contract",
        f"Slider range {bt.MIN_DTE}-{bt.MAX_DTE} days",
        f"live window is 30-{bt.MAX_DTE} days",
        "End of day (close)",
        "No look-ahead: on any backtest date",
        "European Black-Scholes value is a close approximation",
        "Unadjusted SPY close and VIX close from Yahoo Finance (yfinance)",
        "not indicative of future performance",
        "not investment advice", "README.md",
        # the live capital value, and the fixed-sizing disclosure
        "Capital: <b>$10,000</b>",
        "Slider range $5,000-$100,000 in $5,000 steps",
        "no automatic position scaling",
        "one call and one put",
    ):
        assert needle in mo, needle
    # visible UI text must not use em dashes or en dashes
    assert "–" not in mo and "—" not in mo


_REAL_DATA: dict = {}


def _real_data():
    if not _REAL_DATA:
        _REAL_DATA["d"] = bt.fetch_data(bt.START, bt.END)
    return _REAL_DATA["d"]


def _cards_capital_flag(at):
    """Recompute the engine's flag (same data, dashboard slider default
    of 30) to check the info box."""
    res = bt.run_backtest(_real_data(), target_dte=30)
    return bool(res["summary"]["capital_exhausted"])


def test_series_selection():
    at = _run()
    at.multiselect[0].set_value(["SPY"])
    at.run()
    assert not at.exception
    assert [t["name"] for t in _traces(at)[0]] == ["SPY"]
    at.multiselect[0].set_value(["Strategy"])
    at.run()
    assert [t["name"] for t in _traces(at)[0]] == ["Strategy"]
    at.multiselect[0].set_value([])
    at.run()
    assert not at.exception
    # nothing rendered when nothing is selected -> annotation, no crash
    fig = json.loads(at.get("plotly_chart")[0].proto.spec)
    assert not fig["data"]
    assert any("Select at least one series" in a.get("text", "")
               for a in fig.get("layout", {}).get("annotations", [])
               if isinstance(a, dict))


def test_dte_mode_controls():
    """Slider is live in Fixed DTE; Optimal DTE hides all results and
    the DTE selection survives the round trip."""
    at = _run()
    # changing the slider re-runs the engine with the new target DTE
    at.slider[0].set_value(35)
    at.run()
    assert not at.exception
    assert at.slider[0].value == 35
    n35 = re.search(r'metric-value">(\d+)<', _cards(at)[3]).group(1)
    # the methodology section is bound to the live configuration: the
    # moved slider must be reflected in the summary and the entry window
    mo = _mo_html(at)
    assert "Target DTE: <b>35 days</b>" in mo
    assert "35-45-day window" in mo and "live window is 35-45 days" in mo
    assert "30 days" not in mo  # the old default must not leak in
    # switching to the not-implemented mode hides every results section
    at.segmented_control[0].set_value("Optimal DTE (TBD)")
    at.run()
    assert not at.exception
    assert "Optimal DTE selection is not implemented yet." in _all_text(at)
    assert not at.slider and not at.number_input  # slider hidden in TBD
    assert not at.get("plotly_chart")              # no equity/drawdown
    assert not _cards(at)                          # no metric cards
    assert not any(m.value.lstrip().startswith(
        '<div class="trades-table-wrap') for m in at.get("markdown")), \
        "trade table must be hidden in TBD mode"
    # stale Fixed DTE results must not leak in
    info = "\n".join(b.value for b in at.get("info"))
    assert "Insufficient capital" not in info
    # repeated switching back restores the slider, the same DTE (35),
    # and the same results as before the switch
    for _ in range(2):
        at.segmented_control[0].set_value("Fixed DTE")
        at.run()
        assert not at.exception
        assert at.slider[0].value == 35, "DTE must survive the round trip"
        n_back = re.search(r'metric-value">(\d+)<', _cards(at)[3]).group(1)
        assert n_back == n35
        at.segmented_control[0].set_value("Optimal DTE (TBD)")
        at.run()
        assert "Optimal DTE selection is not implemented yet." \
            in _all_text(at)


def test_capital_slider_reruns_accounting():
    """Moving the Initial Capital slider re-runs the full engine
    (trade eligibility, cash, equity, return, win rate, trade history)
    rather than rescaling the $10,000 result. Verified against a direct
    engine re-run on the same real data with the same DTE."""
    at = _run()
    d = _real_data()
    ref10 = bt.run_backtest(d, initial_capital=10_000.0, target_dte=30)
    ref100 = bt.run_backtest(d, initial_capital=100_000.0, target_dte=30)
    s10 = ref10["summary"]

    # at the default $10,000 the dashboard matches the engine exactly
    for card, key in (("Total Return", "total_return"),
                      ("Max Drawdown", "max_drawdown"),
                      ("Win Rate", "win_rate"),
                      ("Trades", "n_trades")):
        val = next(re.search(rf'metric-label">{card}<'
                             r'.*?metric-value">([^<]+)<', c, re.S)
                   .group(1) for c in _cards(at)
                   if f'metric-label">{card}<' in c)
        assert val == (f"{s10[key]:+.1%}" if "Return" in card
                       else f"{s10[key]:.1%}" if "Drawdown" in card
                       else (f"{s10[key]:.0%}" if key == "win_rate"
                             else str(s10[key]))), (card, val)

    # move the slider to $100,000 and re-run
    at.slider[1].set_value(100_000)
    at.run()
    assert not at.exception
    s100 = ref100["summary"]
    # the trade set is genuinely capital-dependent: $100,000 takes every
    # trade the strategy can afford, a strict superset of the $10,000 set
    assert s100["n_trades"] > s10["n_trades"], \
        "capital must change which trades are taken, not just the scale"
    # every reported metric matches the independent engine re-run
    for card, key in (("Total Return", "total_return"),
                      ("Max Drawdown", "max_drawdown"),
                      ("Win Rate", "win_rate"),
                      ("Trades", "n_trades")):
        val = next(re.search(rf'metric-label">{card}<'
                             r'.*?metric-value">([^<]+)<', c, re.S)
                   .group(1) for c in _cards(at)
                   if f'metric-label">{card}<' in c)
        assert val == (f"{s100[key]:+.1%}" if "Return" in card
                       else f"{s100[key]:.1%}" if "Drawdown" in card
                       else (f"{s100[key]:.0%}" if key == "win_rate"
                             else str(s100[key]))), (card, val)
    # the trade history row count follows the new trade set
    html = _trades_html(at)
    assert html.count("<tr>") - 1 == s100["n_trades"]
    # the result is a real re-run, not a rescale of the $10,000 return:
    # the two returns are not proportional to the capital ratio
    assert s100["total_return"] != \
        s10["total_return"] * 100_000.0 / 10_000.0
    # the title is fixed; the chart follows the live capital instead:
    # the actual equity curve and the buy-and-hold SPY benchmark both
    # start at the selected $100,000
    data100, layout = _traces(at)
    assert layout["title"]["text"] == "Portfolio Equity"
    for t in data100:
        first = float(_trace_y(t)[~np.isnan(_trace_y(t))][0])
        assert abs(first - 100_000.0) < 1e-6, (t["name"], first)
    mo = _mo_html(at)
    assert "Capital: <b>$100,000</b>" in mo
    assert "Capital: <b>$10,000</b>" not in mo
    # the capital-exhaustion note is factual: it must appear only when
    # the engine says so for the live configuration (True at 10k,
    # False at 100k over this data)
    info = "\n".join(b.value for b in at.get("info"))
    assert "Insufficient capital" not in info  # 100k: flag is False
    at.slider[1].set_value(10_000)
    at.run()
    assert not at.exception
    n10 = re.search(r'metric-value">(\d+)<', _cards(at)[3]).group(1)
    assert n10 == str(s10["n_trades"]), \
        "restoring $10,000 must restore the original trade count"
    info = "\n".join(b.value for b in at.get("info"))
    assert "Insufficient capital" in info  # 10k: flag is True


def test_pricing_inputs_rerun_pricing():
    """Moving either Pricing Assumptions input re-runs the engine at
    the new rates (not a rescale): every card, the trade history, and
    the equity curve must match an independent engine re-run on the same
    data, DTE, and capital. The methodology rows follow the live values.
    The DTE and capital selections are preserved."""
    at = _run()
    d = _real_data()
    s10 = bt.run_backtest(d, initial_capital=10_000.0, target_dte=30)["summary"]

    def card_val(name):
        return next(re.search(rf'metric-label">{name}<'
                              r'.*?metric-value">([^<]+)<', c, re.S)
                    .group(1) for c in _cards(at)
                    if f'metric-label">{name}<' in c)

    def cards_match(s):
        assert card_val("Total Return") == f"{s['total_return']:+.1%}"
        assert card_val("Max Drawdown") == f"{s['max_drawdown']:.1%}"
        assert card_val("Win Rate") == f"{s['win_rate']:.0%}"
        assert card_val("Trades") == str(s["n_trades"])

    # baseline: the default inputs (1.0 / 1.3) reproduce the engine
    cards_match(s10)
    # the primary configuration is unchanged
    assert at.slider[0].value == 30 and at.slider[1].value == 10_000

    # risk-free 1.0 -> 3.0: a full re-run at r=3%, q=1.3%
    ref = bt.run_backtest(d, initial_capital=10_000.0, target_dte=30,
                          risk_free=0.03, dividend_yield=0.013)
    srf = ref["summary"]
    # the move is material, not rounding: return and final equity differ
    assert abs(srf["total_return"] - s10["total_return"]) > 0.001
    at.number_input[0].set_value(3.0)
    at.run()
    assert not at.exception
    cards_match(srf)
    assert _trades_html(at).count("<tr>") - 1 == srf["n_trades"]
    # the equity curve follows the re-run and still starts at the capital
    data, layout = _traces(at)
    for t in data:
        y = _trace_y(t)
        first = float(y[~np.isnan(y)][0])
        assert abs(first - 10_000.0) < 1e-6, (t["name"], first)
    # the methodology row shows the live value, not the default
    mo = _mo_html(at)
    assert "live value 3.0% flat" in mo
    assert "live value 1.0% flat" not in mo
    # the other input and the primary controls are preserved
    assert at.number_input[1].value == 1.3
    assert at.slider[0].value == 30 and at.slider[1].value == 10_000

    # dividend yield 1.3 -> 3.0 (with r restored to 1.0): another re-run
    ref2 = bt.run_backtest(d, initial_capital=10_000.0, target_dte=30,
                           risk_free=0.01, dividend_yield=0.03)
    at.number_input[0].set_value(1.0)
    at.number_input[1].set_value(3.0)
    at.run()
    assert not at.exception
    cards_match(ref2["summary"])
    assert _trades_html(at).count("<tr>") - 1 == ref2["summary"]["n_trades"]
    mo2 = _mo_html(at)
    assert "live value 1.0% flat" in mo2
    assert "live value 3.0% continuous" in mo2
    assert "live value 1.3% continuous" not in mo2
    assert "live value 3.0% flat" not in mo2
