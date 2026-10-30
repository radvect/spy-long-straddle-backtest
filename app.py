"""SPY Long Straddle Backtest — Streamlit dashboard (single page).

Run:  streamlit run app.py
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backtest import (COMMISSION_PER_CONTRACT, CONTRACT_SIZE, DIVIDEND_YIELD,
                      END, MAX_DTE, MIN_DTE, RISK_FREE_RATE, SPREAD_REL_HALF,
                      START, fetch_data, run_backtest)

st.set_page_config(page_title="SPY Long Straddle Backtest", layout="wide")

st.title("SPY Long Straddle Backtest")
st.caption("Monthly SPY options strategy | 2020-2025 | Modeled prices")

# ---------------------------------------------------------------------------
# Minimal CSS: centered 1200px container, metric cards, compact trade table,
# compact DTE slider. (Streamlit's native layout can't express these.)
#
# The container selector is verified against the installed Streamlit 1.66
# live DOM:
#   <main id="stMain" data-testid="stMain">
#     <div class="stMainBlockContainer block-container" ...>
# Older Streamlit versions used <div id="main" data-testid="stMain"> in
# this spot. `data-testid="stMain"` is stable across both, so one rule
# covers them. (In wide mode the framework sets `max-width: initial` on
# the container — the classic `#main .block-container` rule matches
# nothing in this version. `!important` is required because emotion
# injects its own container styles after this stylesheet.)
#
# This block container is the single wrapper of the header, controls,
# cards, charts, and table — one width constraint here governs all of
# them, and no other rule may nest a competing max-width. On viewports
# narrower than 1200px the container is 100% minus the side padding, so
# small screens keep natural responsive behavior; vertical scrolling is
# untouched. The bottom footer strip (when rendered) is aligned to the
# same value, not a second nested constraint.
# ---------------------------------------------------------------------------
PAGE_CSS = """
[data-testid="stMain"] .block-container {
    max-width: 1200px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-left: 32px !important;
    padding-right: 32px !important;
}
/* Keep the bottom footer strip (when rendered) aligned with the column. */
[data-testid="stBottomBlockContainer"] {
    max-width: 1200px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-left: 32px !important;
    padding-right: 32px !important;
}
/* Compact DTE and capital sliders (~400px each), rendered beside the
   mode selector in a 3-column row. */
[data-testid="stSlider"] {
    max-width: 400px;
}
/* ---- Pricing Assumptions: subdued secondary controls ---------------
   A native caption label plus two compact number inputs in a tight,
   left-aligned row below the primary sliders. Streamlit 1.66 renders
   st.columns() as an stHorizontalBlock row of stColumn children (NOT
   an stColumns element), and each column is flex:1 1 calc(50% - 16px)
   with its input stretched to the full 560px. To pack the two inputs
   side by side instead of half a page apart, the :has() guard scopes
   these rules to the one row that actually holds number inputs (never
   the slider or KPI rows): each column stops growing and hugs its
   ~190px input, and the row wraps to a vertical stack when the
   container is too narrow for two. */
[data-testid="stHorizontalBlock"]:has([data-testid="stNumberInput"]) {
    gap: 20px;
}
[data-testid="stHorizontalBlock"]:has([data-testid="stNumberInput"])
    > [data-testid="stColumn"] {
    flex: 0 0 auto;
    /* The framework's own rule sets `width: calc(50% - 1rem)` on the
       column; flex-basis:auto would defer to it, so override width to
       let the column hug its ~190px input instead of holding half the
       row. */
    width: auto;
}
[data-testid="stHorizontalBlock"]:has([data-testid="stNumberInput"])
    [data-testid="stNumberInput"] {
    width: 190px;
    max-width: 100%;
}
.metric-card {
    width: 100%;
    box-sizing: border-box;
    background: var(--mc-bg);
    border: 1px solid var(--mc-border);
    border-radius: 8px;
    padding: 16px 18px;
    min-height: 92px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 8px;
}
.metric-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--mc-label);
}
.metric-value {
    font-size: 30px;
    font-weight: 640;
    line-height: 1.05;
    color: var(--mc-value);
    font-variant-numeric: tabular-nums;
}
.metric-card.pos .metric-value { color: var(--pos); }
.metric-card.neg .metric-value { color: var(--neg); }

.trades-table-wrap {
    max-height: 420px;
    overflow-y: auto;
    border: 1px solid var(--tbl-border);
    border-radius: 8px;
    background: var(--tbl-bg);
}
table.trades {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
    font-variant-numeric: tabular-nums;
}
.trades th {
    position: sticky;
    top: 0;
    background: var(--tbl-head-bg);
    color: var(--tbl-label);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 8px 14px;
    text-align: right;
    border-bottom: 1px solid var(--tbl-border);
}
.trades th:first-child, .trades td:first-child { text-align: left; }
.trades td {
    padding: 7px 14px;
    border-bottom: 1px solid var(--tbl-row-border);
    text-align: right;
    color: var(--tbl-text);
}
.trades tr:last-child td { border-bottom: none; }
.trades .neg { color: var(--neg); }
.trades .pos { color: var(--pos); }

/* ---- Methodology: compact summary + disclosure + accordions ------------
   Flat, card-free by design: one summary line, one disclosure line, and
   one expandable group of three collapsed accordions. Subtle dividers
   only; no warning borders, no icons, no nested cards. */
.mo-section { border-top: 1px solid var(--mc-border); padding-top: 4px; }
.mo-title {
    font-size: 17px; font-weight: 640; color: var(--mc-value);
    margin: 18px 0 0;
}
.mo-badge {
    display: inline-block; vertical-align: 3px; margin-left: 9px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.04em;
    text-transform: uppercase; color: var(--mc-label);
    border: 1px solid var(--mc-border); border-radius: 4px;
    padding: 2px 7px;
}
.mo-facts {
    font-size: 14px; line-height: 1.5; color: var(--mc-secondary);
    margin: 6px 0 0; font-variant-numeric: tabular-nums;
}
.mo-facts b { color: var(--mc-value); font-weight: 640; }
.mo-disc {
    font-size: 13px; line-height: 1.5; color: var(--mc-label);
    margin: 8px 0 10px;
}
.mo-outer { margin-top: 2px; }
.mo-outer > summary {
    cursor: pointer; list-style: none; user-select: none;
    font-size: 12.5px; font-weight: 600; letter-spacing: 0.05em;
    text-transform: uppercase; color: var(--mc-label); padding: 6px 0;
}
.mo-outer > summary::-webkit-details-marker { display: none; }
.mo-outer > summary::before { content: "▸ "; }
.mo-outer[open] > summary::before { content: "▾ "; }
.mo-outer[open] > summary { margin-bottom: 4px; }
.mo-acc { border-top: 1px solid var(--tbl-row-border); }
.mo-acc > summary {
    cursor: pointer; list-style: none; user-select: none;
    font-size: 14px; font-weight: 640; color: var(--mc-value);
    padding: 10px 0;
}
.mo-acc > summary::-webkit-details-marker { display: none; }
.mo-acc > summary::before { content: "▸ "; color: var(--mc-label); }
.mo-acc[open] > summary::before { content: "▾ "; }
.mo-body {
    padding: 0 0 14px 16px;
    font-size: 13px; line-height: 1.6; color: var(--mc-secondary);
}
.mo-item { padding: 4px 0; }
.mo-item b { color: var(--mc-value); font-weight: 640; }

:root {
    --mc-bg: #ffffff;
    --mc-border: #e5e7eb;
    --mc-label: #6b7280;
    --mc-value: #111827;
    --mc-secondary: #4b5563;
    --warn: #d97706;
    --pos: #16a34a;
    --neg: #dc2626;
    --tbl-bg: #ffffff;
    --tbl-border: #e5e7eb;
    --tbl-head-bg: #f9fafb;
    --tbl-row-border: #f1f3f5;
    --tbl-label: #6b7280;
    --tbl-text: #111827;
}
[data-theme="dark"] {
    --mc-bg: #16181d;
    --mc-border: #2b2f36;
    --mc-label: #9ca3af;
    --mc-value: #e5e7eb;
    --mc-secondary: #cbd5e1;
    --warn: #fbbf24;
    --pos: #4ade80;
    --neg: #f87171;
    --tbl-bg: #16181d;
    --tbl-border: #2b2f36;
    --tbl-head-bg: #1c1f26;
    --tbl-row-border: #262a31;
    --tbl-label: #9ca3af;
    --tbl-text: #e5e7eb;
}
"""
st.markdown(f"<style>{PAGE_CSS}</style>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Data — backtest calculations live in backtest.py (unchanged engine).
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="Downloading SPY and VIX from yfinance…")
def load_data() -> tuple:
    return fetch_data(START, END)          # (spy_df, vix_df)


try:
    spy_df, vix_df = load_data()
except Exception as exc:  # report data failures, never fake results
    st.error(
        "Could not download historical data (SPY/VIX via yfinance). "
        "The backtest was NOT run on fabricated data.\n\n"
        f"Error: {exc}"
    )
    st.stop()

spy_close = spy_df["close"]

# ---------------------------------------------------------------------------
# Strategy controls (compact)
# ---------------------------------------------------------------------------
c_mode, c_dte, c_cap = st.columns(3)
with c_mode:
    mode = st.segmented_control(
        "Expiration",
        ["Fixed DTE", "Optimal DTE (TBD)"],
        default="Fixed DTE",
    )

# Compact DTE and capital sliders (~400px wide each via CSS). Both use
# key + persist_state="session" so the selection survives in session
# state even while unrendered in Optimal DTE mode: switching back to
# Fixed DTE restores the same DTE and capital, and the engine re-runs on
# them, so exactly those results reappear without re-entry.
if mode == "Fixed DTE":
    with c_dte:
        target_dte = st.slider(
            "Target DTE",
            min_value=MIN_DTE, max_value=MAX_DTE,
            value=30, step=1,
            format="%d days",
            key="target_dte",
            persist_state="session",
        )
    with c_cap:
        initial_capital = st.slider(
            "Initial Capital",
            min_value=5_000, max_value=100_000,
            value=10_000, step=5_000,
            format="$%,d",
            key="initial_capital",
            persist_state="session",
        )

    # Secondary controls: the two Black-Scholes pricing assumptions.
    # Subdued by design (caption + two small number inputs, not sliders,
    # no cards), below the primary DTE and capital sliders. Decimal
    # percentages, persisted in session state like the sliders. The
    # risk-free rate may be negative (real rates have been); the
    # dividend yield is a yield, so it is bounded at zero.
    st.caption("Pricing Assumptions")
    # Two equal-width columns so the inputs sit next to each other on
    # desktop and wrap to a vertical stack on narrow screens (the CSS
    # above makes the children non-growing and caps each at ~190px).
    pa_rf, pa_dy = st.columns(2)
    with pa_rf:
        risk_free_pct = st.number_input(
            "Risk-Free Rate (%)",
            min_value=-10.0, max_value=20.0,
            value=float(RISK_FREE_RATE * 100.0), step=0.1,
            format="%.1f",
            key="risk_free_pct",
            persist_state="session",
        )
    with pa_dy:
        dividend_yield_pct = st.number_input(
            "Dividend Yield (%)",
            min_value=0.0, max_value=10.0,
            value=float(DIVIDEND_YIELD * 100.0), step=0.1,
            format="%.1f",
            key="dividend_yield_pct",
            persist_state="session",
        )
# In Optimal DTE mode the sliders and pricing inputs are unrendered
# (their values persist) and the backtest is not run; the single
# message below stops the page.

if mode == "Optimal DTE (TBD)":
    # No engine run and no stale results in this mode: one message,
    # then stop before any results section.
    st.info("Optimal DTE selection is not implemented yet.")
    st.stop()

# Run the engine (fast — a few hundred ms; re-runs live as the sliders
# move). initial_capital is a full engine input, not a rescale: the
# affordability guard (entry cost must fit in available cash) makes the
# SET of trades capital-dependent, so a new value re-runs the entire
# portfolio accounting: trade eligibility, cash path, equity curve,
# drawdown, return, win rate, and trade history. Position sizing is
# unchanged: exactly one call and one put per trade, no leverage, no
# fractional contracts.
# risk_free and dividend_yield are full engine inputs: they feed every
# Black-Scholes call (entry premium, daily marks, and the settlement
# mark), so a new value re-runs the entire pricing path and the results,
# equity curve, drawdown, and trade history move consistently.
result = run_backtest((spy_df, vix_df), initial_capital=initial_capital,
                      target_dte=target_dte,
                      risk_free=risk_free_pct / 100.0,
                      dividend_yield=dividend_yield_pct / 100.0)
equity: pd.DataFrame = result["equity"]
trades: pd.DataFrame = result["trades"]
s = result["summary"]

# ---------------------------------------------------------------------------
# Performance summary — four equal cards
# ---------------------------------------------------------------------------
def _metric_card(label: str, value: str, tone: str = "neutral") -> str:
    return (
        f'<div class="metric-card {tone}">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div>'
        f"</div>"
    )


total_return = s["total_return"]
ret_tone = "pos" if total_return >= 0 else "neg"
c1, c2, c3, c4 = st.columns(4)
c1.markdown(_metric_card("Total Return", f"{total_return:+.1%}", ret_tone),
            unsafe_allow_html=True)
c2.markdown(_metric_card("Max Drawdown", f"{s['max_drawdown']:.1%}"),
            unsafe_allow_html=True)
c3.markdown(
    _metric_card("Win Rate",
                 f"{s['win_rate']:.0%}" if s["n_trades"] else "n/a"),
    unsafe_allow_html=True)
c4.markdown(_metric_card("Trades", str(s["n_trades"])),
            unsafe_allow_html=True)

# Factual capital note — shown only when the backtest itself confirms it.
# The engine sets the flag only when the strategy ended flat AND its last
# entry day in the window was blocked for lack of cash — i.e. every entry
# from the last settlement onward was skipped. Earlier blocked months that
# the strategy recovered from do not count, and the message never claims
# a permanent change of strategy beyond what the data shows.
if s["capital_exhausted"]:
    st.info(
        "Insufficient capital for new positions: after the last "
        "settlement, cash was below the cost of a new straddle, so "
        "every subsequent entry was skipped and the strategy sat flat "
        "until the end of the window."
    )

# ---------------------------------------------------------------------------
# Portfolio Equity chart. The strategy curve is the actual backtested
# portfolio equity (the engine's daily mark-to-market of cash plus the
# marked open position); it starts exactly at the initial capital. The
# SPY series is a buy-and-hold benchmark: the same initial capital
# invested in SPY on the first trading day. Both curves therefore read in
# real dollars on one scale and both start at the selected initial capital.
# ---------------------------------------------------------------------------
BASE = float(initial_capital)


def _norm(series: pd.Series, base: float) -> pd.Series | None:
    """Buy-and-hold indexing: invest `base` dollars in the series on its
    first valid day; later values scale with the price level."""
    aligned = series.reindex(equity.index)
    start = aligned.dropna().iloc[0]
    if pd.isna(start) or start <= 0:
        return None
    return aligned / start * base


norm: dict[str, pd.Series] = {
    # actual backtested portfolio equity (cash + marked open position);
    # starts at the initial capital
    "Strategy": equity["equity"],
    # buy-and-hold: the initial capital invested in SPY
    "SPY": _norm(spy_close, BASE),
}

# Explicit, brand-stable palette (deliberately NOT Plotly's default cycle):
# the strategy is the dominant series; SPY stays distinct where the
# curves cross.
SERIES_ORDER = ["Strategy", "SPY"]
SERIES_STYLES = {
    "Strategy": dict(color="#2563EB", width=3.0, dash="solid"),
    "SPY":      dict(color="#EA580C", width=2.0, dash="solid"),
}
GRID = "#e5e7eb"
TICK = "#6b7280"

selected = st.multiselect(
    "Series",
    options=[name for name in SERIES_ORDER if name in norm],
    default=[name for name in SERIES_ORDER if name in norm],
)

fig = go.Figure()
for name in SERIES_ORDER:
    if name in selected:
        series = norm[name]
        st_ = SERIES_STYLES[name]
        fig.add_trace(go.Scatter(
            x=series.index, y=series.to_numpy(),
            name=name, mode="lines",
            line=dict(color=st_["color"], width=st_["width"],
                      dash=st_["dash"]),
            hovertemplate="<b>%{fullData.name}</b>  $%{y:,.0f}<extra></extra>",
        ))

if fig.data:
    fig.update_layout(
        title=dict(text="Portfolio Equity",
                   x=0, xanchor="left", font=dict(size=14, color="#111827")),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=GRID,
                        font=dict(color="#111827", size=12)),
        # compact horizontal legend, right-aligned on the title row: each
        # entry carries its series color (the swatch follows the trace, so
        # the legend and chart can't drift). The right edge stops short of
        # the corner to leave room for the (i) info note.
        legend=dict(orientation="h", xanchor="right", x=0.94,
                    yanchor="bottom", y=1.04,
                    font=dict(size=12, color="#374151")),
        xaxis=dict(hoverformat="%b %d, %Y", tickformat="%b %Y",
                   showgrid=False, nticks=8,
                   tickfont=dict(size=12, color=TICK)),
        yaxis=dict(title=dict(text="Portfolio Value (USD)",
                              font=dict(size=12, color=TICK)),
                   tickprefix="$", tickformat=",.0f",
                   showgrid=True, gridcolor=GRID,
                   zeroline=False, tickfont=dict(size=12, color=TICK)),
        font=dict(color="#374151", size=12),
        # subtle info note beside the title: the SPY benchmark starts with
        # the same capital the strategy is given (shown on hover, not in
        # the default view, so it stays out of the way)
        annotations=[dict(
            text="(i)",
            x=1.0, xanchor="right", y=1.04, yanchor="bottom",
            xref="paper", yref="paper",
            showarrow=False, align="left",
            font=dict(size=11, color="#94a3b8"),
            hovertext="SPY buy-and-hold benchmark assumes the same "
                      "initial investment as the strategy.",
        )],
    )
else:
    fig.add_annotation(
        text="Select at least one series", showarrow=False,
        font=dict(size=14, color="#94a3b8"),
    )
fig.update_layout(
    height=400,
    # top margin holds the title + right-aligned legend on one row (both
    # anchored to the top of the paper area) above the 336px plot
    margin=dict(l=8, r=12, t=56, b=8),
    plot_bgcolor="#ffffff",
    paper_bgcolor="#ffffff",
)
st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# Drawdown — directly below, minimal
# ---------------------------------------------------------------------------
dd = equity["equity"] / equity["equity"].cummax() - 1.0
fig_dd = go.Figure(go.Scatter(
    x=dd.index, y=dd.to_numpy(), mode="lines", name="Drawdown",
    line=dict(color="#DC515B", width=2.0),
    fill="tozeroy", fillcolor="rgba(220,81,91,0.10)",
    hovertemplate="%{x|%b %d, %Y}<br>%{y:.1%}<extra></extra>",
))
fig_dd.update_layout(
    height=190,
    margin=dict(l=8, r=12, t=10, b=8),
    showlegend=False,
    hoverlabel=dict(bgcolor="#ffffff", bordercolor=GRID,
                    font=dict(color="#111827", size=12)),
    xaxis=dict(hoverformat="%b %d, %Y", tickformat="%b %Y",
               showgrid=False, nticks=8,
               tickfont=dict(size=12, color=TICK)),
    yaxis=dict(title=dict(text="Drawdown", font=dict(size=12, color=TICK)),
               tickformat=".0%", showgrid=True, gridcolor=GRID,
               zeroline=True, tickfont=dict(size=12, color=TICK)),
    plot_bgcolor="#ffffff",
    paper_bgcolor="#ffffff",
    font=dict(color="#374151", size=12),
)
st.plotly_chart(fig_dd, width="stretch")

# ---------------------------------------------------------------------------
# Trade history — compact HTML table (color-coded P&L; limited height)
# ---------------------------------------------------------------------------
st.subheader("Trade History")

tdf = trades.reset_index()
_rows = []
for r in tdf.itertuples(index=False):
    pnl_cls = "pos" if r.net_pnl >= 0 else "neg"
    _rows.append(
        "<tr>"
        f"<td>{r.entry_date:%Y-%m-%d}</td>"
        f"<td>{r.expiration:%Y-%m-%d}</td>"
        f"<td>{int(r.strike):,}</td>"
        f"<td>{int(r.dte)}</td>"
        f"<td>${r.entry_cost:,.2f}</td>"
        f"<td>${r.exit_value:,.2f}</td>"
        f'<td class="{pnl_cls}">${r.net_pnl:+,.2f}</td>'
        "</tr>"
    )
_trades_html = (
    '<div class="trades-table-wrap"><table class="trades">'
    "<thead><tr>"
    "<th>Entry Date</th><th>Expiration</th><th>Strike</th><th>DTE</th>"
    "<th>Entry Cost</th><th>Exit Value</th><th>Net P&amp;L</th>"
    "</tr></thead>"
    f'<tbody>{"".join(_rows)}</tbody></table></div>'
)
st.markdown(_trades_html, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Methodology & Assumptions (compact, bottom of page)
# A short summary line (strategy name, "Modeled" badge, three facts bound
# to the live configuration), one always-visible disclosure, and a single
# expandable group of three collapsed accordions. All parameter values are
# read from the engine constants and the live sliders and pricing inputs;
# none are hardcoded.
# ---------------------------------------------------------------------------
def _mo_acc(title: str, items) -> str:
    lis = "".join(
        f'<div class="mo-item"><b>{t}.</b> {d}</div>' for t, d in items
    )
    return (
        f'<details class="mo-acc"><summary>{title}</summary>'
        f'<div class="mo-body">{lis}</div></details>'
    )


_mo_acc1 = _mo_acc("Entry &amp; exit rules", (
    ("Monthly cadence",
     "One long ATM straddle per contract month: one at-the-money call "
     "and one at-the-money put, same strike, same expiration."),
    ("Entry day",
     f"Last trading day whose DTE is still inside the "
     f"{target_dte}-{MAX_DTE}-day window before expiration (about "
     f"{target_dte} days out)."),
    ("Strike",
     "Nearest $1 strike to the entry-day SPY close; ties go to the "
     "lower strike."),
    ("Expiration",
     "Standard SPY monthly: 3rd Friday (2nd Friday when the 3rd is "
     "Good Friday)."),
    ("Exit",
     "Held to expiration, marked to intrinsic value and settled at the "
     "same 2% half-spread as the daily marks."),
    ("Position",
     "Exactly one standard contract per leg "
     f"({CONTRACT_SIZE}-share multiplier), fixed for every capital "
     "level: no leverage, no fractional contracts, and no automatic "
     "position scaling. At most one position at a time; a new straddle "
     "opens only after the previous expiration, and only when its full "
     "entry cost fits in available cash."),
    ("Initial capital",
     f"Slider range $5,000-$100,000 in $5,000 steps; live value "
     f"${initial_capital:,.0f}. A straddle is opened only if its full "
     "entry cost fits in available cash; otherwise the entry is "
     "skipped, the reason is recorded, and sizing is unchanged (one "
     "call and one put)."),
))

_mo_acc2 = _mo_acc("Pricing &amp; costs", (
    ("Premiums",
     "Black-Scholes modeled values (European, continuous dividend "
     "yield), not historical market quotes."),
    ("Volatility",
     "Same-day VIX close (30-day index proxy, not option IV), divided "
     "by 100."),
    ("Risk-free rate",
     f"Input range -10.0% to 20.0% in 0.1% steps; live value "
     f"{risk_free_pct:.1f}% flat. May be negative."),
    ("Dividend yield",
     f"Input range 0.0% to 10.0% in 0.1% steps; live value "
     f"{dividend_yield_pct:.1f}% continuous. Bounded at zero."),
    ("Bid-ask and slippage",
     f"{SPREAD_REL_HALF:.0%} relative half-spread (buy at mid + "
     f"{SPREAD_REL_HALF:.0%}, sell at mid - "
     f"{SPREAD_REL_HALF:.0%}); no separate slippage term."),
    ("Commission",
     f"${COMMISSION_PER_CONTRACT:.0f} per contract per leg, charged at "
     "entry and at exit."),
    ("Contract size", f"{CONTRACT_SIZE} shares per contract."),
    ("DTE window",
     f"Slider range {MIN_DTE}-{MAX_DTE} days; the live window is "
     f"{target_dte}-{MAX_DTE} days, and entry is the last trading day "
     "inside it."),
    ("Execution",
     "End of day (close); a disclosed approximation for daily data."),
))

_mo_period = (f"{spy_df.index[0]:%b %Y} - {spy_df.index[-1]:%b %Y}")
_mo_acc3 = _mo_acc("Limitations &amp; data", (
    ("European approximation",
     "SPY options are American; the European Black-Scholes value is a "
     "close approximation, not exact."),
    ("Modeled, not historical quotes",
     "Every premium is a Black-Scholes computation, not a traded quote."),
    ("Volatility timing / look-ahead",
     "No look-ahead: on any backtest date only that date's SPY and VIX "
     "closes are used (both published by that evening). The VIX is the "
     "30-day index volatility, not the straddle's own IV."),
    ("Execution and liquidity",
     "End-of-day fills; flat commission and spread on every trade; no "
     "market impact, queue, or overnight gap risk."),
    ("Data and reproducibility",
     f"Unadjusted SPY close and VIX close from Yahoo Finance "
     f"(yfinance), {_mo_period}. Simulated results are not indicative "
     "of future performance; not investment advice. Full details in "
     "README.md."),
))

_mo_facts = (
    '<div class="mo-facts">'
    "Entry: <b>Monthly</b> | "
    f"Target DTE: <b>{target_dte} days</b> | "
    f"Capital: <b>${initial_capital:,.0f}</b> | "
    "Exit: <b>Expiration</b>"
    "</div>"
)

_mo_html = (
    '<div class="mo-section">'
    '<div class="mo-title">SPY Long Straddle'
    '<span class="mo-badge">Modeled</span></div>'
    f"{_mo_facts}"
    '<div class="mo-disc">Option prices are modeled, not historical '
    "market quotes.</div>"
    '<details class="mo-outer">'
    "<summary>Pricing, execution &amp; limitations</summary>"
    f"{_mo_acc1}"
    f"{_mo_acc2}"
    f"{_mo_acc3}"
    "</details>"
    "</div>"
)
st.markdown(_mo_html, unsafe_allow_html=True)
