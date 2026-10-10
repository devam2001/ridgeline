"""Investor-style tear sheet as one self-contained HTML file.

Inline SVG charts and no external assets, so it can be emailed or archived as
is; the browser's Print -> Save as PDF gives a clean PDF.

Data sources (normalised to one trades DataFrame):
  - a paper/live Freqtrade SQLite database
  - a backtest result directory (also what the panel's Replay produces)

Performance is on a realised basis: equity moves when trades close. Open
positions at the report date are listed but not marked to market.
"""

import html
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from ridgeline import USERDIR

DATADIR = USERDIR / "data" / "binance"
COLUMNS = ["pair", "open_date", "close_date", "stake", "profit_abs", "exit_reason", "is_open"]


# ---------------------------------------------------------------- loading

def load_trades_db(path: Path) -> pd.DataFrame:
    with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True) as con:
        df = pd.read_sql_query(
            "SELECT pair, open_date, close_date, stake_amount AS stake, close_profit_abs AS profit_abs, "
            "exit_reason, is_open FROM trades", con)
    return _normalise(df)


def load_trades_backtest(directory: Path) -> tuple[pd.DataFrame, float]:
    from freqtrade.data.btanalysis import load_backtest_stats

    stats = load_backtest_stats(directory)["strategy"]
    strat = next(iter(stats.values()))
    df = pd.DataFrame(strat["trades"]).rename(columns={"stake_amount": "stake"})
    df["is_open"] = False
    return _normalise(df), float(strat["starting_balance"])


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("open_date", "close_date"):
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    df["is_open"] = df["is_open"].astype(bool)
    df["profit_abs"] = pd.to_numeric(df["profit_abs"], errors="coerce").fillna(0.0)
    return df[COLUMNS].sort_values("open_date").reset_index(drop=True)


# ---------------------------------------------------------------- metrics

def daily_equity(trades: pd.DataFrame, start_balance: float,
                 start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    closed = trades[~trades["is_open"] & trades["close_date"].notna()]
    pnl = closed.groupby(closed["close_date"].dt.floor("D"))["profit_abs"].sum()
    days = pd.date_range(start.floor("D"), end.floor("D"), freq="D", tz="UTC")
    return start_balance + pnl.reindex(days, fill_value=0.0).cumsum()


def risk_stats(equity: pd.Series) -> dict:
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    vol = rets.std() * math.sqrt(365) if len(rets) > 1 else 0.0
    downside = rets[rets < 0].std() * math.sqrt(365) if (rets < 0).sum() > 1 else 0.0
    mean = rets.mean() * 365 if len(rets) else 0.0
    dd = equity / equity.cummax() - 1
    max_dd = -dd.min() if len(dd) else 0.0
    return {
        "total_return": total, "cagr": cagr, "volatility": vol,
        "sharpe": mean / vol if vol > 0 else 0.0,
        "sortino": mean / downside if downside > 0 else 0.0,
        "max_drawdown": max_dd,
        "calmar": cagr / max_dd if max_dd > 0 else 0.0,
    }


def trade_stats(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    closed = trades[~trades["is_open"]]
    wins = closed[closed["profit_abs"] > 0]
    losses = closed[closed["profit_abs"] <= 0]
    pct = closed["profit_abs"] / closed["stake"].where(closed["stake"] > 0)
    gross_loss = -losses["profit_abs"].sum()
    # fraction of the period with at least one position open
    intervals = [(max(o, start), min(c if pd.notna(c) else end, end))
                 for o, c in zip(trades["open_date"], trades["close_date"])]
    covered = _union_seconds([iv for iv in intervals if iv[1] > iv[0]])
    span = max((end - start).total_seconds(), 1)
    hold = (closed["close_date"] - closed["open_date"]).dt.total_seconds() / 3600
    return {
        "trades": len(closed),
        "win_rate": len(wins) / len(closed) if len(closed) else 0.0,
        "profit_factor": wins["profit_abs"].sum() / gross_loss if gross_loss > 0 else float("inf") if len(wins) else 0.0,
        "avg_win_pct": pct[closed["profit_abs"] > 0].mean() if len(wins) else 0.0,
        "avg_loss_pct": pct[closed["profit_abs"] <= 0].mean() if len(losses) else 0.0,
        "avg_hold_hours": hold.mean() if len(hold) else 0.0,
        "time_in_market": covered / span,
        "open_positions": int(trades["is_open"].sum()),
    }


def _union_seconds(intervals: list[tuple]) -> float:
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total


def monthly_returns(equity: pd.Series) -> pd.DataFrame:
    month_end = equity.resample("ME").last()
    first = pd.Series([equity.iloc[0]], index=[equity.index[0] - pd.Timedelta(days=1)])
    rets = pd.concat([first, month_end]).pct_change().dropna()
    grid = pd.DataFrame({"year": rets.index.year, "month": rets.index.month, "ret": rets.values})
    return grid.pivot(index="year", columns="month", values="ret")


def benchmark(pair: str, index: pd.DatetimeIndex) -> Optional[pd.Series]:
    path = DATADIR / f"{pair.replace('/', '_')}-1d.feather"
    if not path.exists():
        return None
    df = pd.read_feather(path, columns=["date", "close"]).set_index("date")["close"]
    series = df.reindex(index, method="ffill")
    if series.isna().all():
        return None
    series = series.ffill().bfill()
    return series / series.iloc[0]


# ---------------------------------------------------------------- SVG

def _svg_lines(series: list[tuple[str, pd.Series, str]], height: int = 220, pct_axis: bool = False,
               fill_below_zero: bool = False, rebased: bool = False) -> str:
    width, pad_l, pad_b, pad_t = 760, 56, 22, 10
    values = pd.concat([s for _, s, _ in series])
    lo, hi = float(values.min()), float(values.max())
    if fill_below_zero:
        hi = max(hi, 0.0)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    idx = series[0][1].index
    n = max(len(idx) - 1, 1)

    def x(i):
        return pad_l + (width - pad_l - 8) * i / n

    def y(v):
        return pad_t + (height - pad_t - pad_b) * (1 - (v - lo) / (hi - lo))

    if rebased:  # growth factors, 1.0 = start
        fmt = (lambda v: f"{(v - 1) * 100:+.0f}%")
    elif pct_axis:
        fmt = (lambda v: f"{v * 100:.0f}%")
    else:
        fmt = (lambda v: f"{v:,.0f}")
    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        parts.append(f'<line x1="{pad_l}" x2="{width - 8}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                     f'<text x="{pad_l - 6}" y="{y(v) + 4:.1f}" class="axis" text-anchor="end">{fmt(v)}</text>')
    for k in (0, n // 2, n):
        parts.append(f'<text x="{x(k):.1f}" y="{height - 4}" class="axis" text-anchor="middle">'
                     f'{idx[k]:%Y-%m-%d}</text>')
    for label, s, color in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s.values))
        if fill_below_zero:
            parts.append(f'<polygon points="{x(0):.1f},{y(0):.1f} {pts} {x(n):.1f},{y(0):.1f}" '
                         f'fill="{color}" fill-opacity="0.18" stroke="none"/>')
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/>')
    parts.append("</svg>")
    legend = "".join(f'<span class="key"><i style="background:{c}"></i>{html.escape(lbl)}</span>'
                     for lbl, _, c in series)
    return f'<div class="legend">{legend}</div>' + "".join(parts)


# ---------------------------------------------------------------- HTML

def _pct(v: float, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "–"
    return f"{v * 100:+.{digits}f}%"


def _num(v: float, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return "∞" if math.isinf(v) else f"{v:.{digits}f}"


def build_tearsheet(trades: pd.DataFrame, start_balance: float, *, title: str, source_label: str,
                    month: Optional[str] = None, mode: str = "paper",
                    benchmark_pair: str = "BTC/USDT") -> str:
    """mode: "paper", "live" or "backtest" - sets the badge and disclosures."""
    if trades.empty:
        raise ValueError("No trades in this source yet - nothing to report.")
    start = trades["open_date"].min()
    end = trades[["open_date", "close_date"]].max().max()
    if month:
        # report "as of" the end of that month: later trades don't exist yet,
        # and trades closed after it were still open then
        m_end = pd.Timestamp(month + "-01", tz="UTC") + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23, minutes=59)
        end = min(m_end, pd.Timestamp.now(tz="UTC"))
        trades = trades[trades["open_date"] <= end].copy()
        if trades.empty:
            raise ValueError(f"No trades by the end of {month}.")
        later = trades["close_date"] > end
        trades.loc[later, "close_date"] = pd.NaT
        trades.loc[later, "is_open"] = True
    end = max(end, start + pd.Timedelta(days=1))

    eq = daily_equity(trades, start_balance, start, end)
    stats = risk_stats(eq)
    tstats = trade_stats(trades, start, end)
    grid = monthly_returns(eq)
    bench = benchmark(benchmark_pair, eq.index)
    dd = eq / eq.cummax() - 1

    last_month = grid.iloc[-1].dropna().iloc[-1] if not grid.empty else 0.0
    year = eq.index[-1].year
    ytd_base = eq[eq.index.year < year]
    ytd = eq.iloc[-1] / (ytd_base.iloc[-1] if len(ytd_base) else eq.iloc[0]) - 1

    closed = trades[~trades["is_open"]]
    by_pair = closed.groupby("pair")["profit_abs"].agg(["count", "sum"]).sort_values("sum", ascending=False)

    lines = [("Strategy equity", eq / eq.iloc[0], "#2f6fed")]
    if bench is not None:
        lines.append((f"{benchmark_pair} buy & hold", bench, "#98a2b3"))

    def card(label, value, sub=""):
        return f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div><div class="sub">{sub}</div></div>'

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    grid_rows = "".join(
        f"<tr><th>{y}</th>" + "".join(
            f'<td class="{"pos" if v > 0 else "neg" if v < 0 else ""}">{_pct(v) if pd.notna(v) else ""}</td>'
            for v in (grid.loc[y].get(mn) if mn in grid.columns else float("nan") for mn in range(1, 13)))
        + f'<td class="ytd">{_pct((1 + grid.loc[y].dropna()).prod() - 1)}</td></tr>'
        for y in grid.index)
    pair_rows = "".join(f"<tr><td>{html.escape(p)}</td><td>{int(r['count'])}</td>"
                        f'<td class="{"pos" if r["sum"] > 0 else "neg"}">{r["sum"]:+,.2f}</td></tr>'
                        for p, r in pd.concat([by_pair.head(5), by_pair.tail(5)]).drop_duplicates().iterrows())
    trade_rows = "".join(
        f"<tr><td>{html.escape(t.pair)}</td><td>{t.open_date:%Y-%m-%d %H:%M}</td>"
        f"<td>{'' if pd.isna(t.close_date) else f'{t.close_date:%Y-%m-%d %H:%M}'}</td>"
        f"<td>{t.stake:,.2f}</td><td class=\"{'pos' if t.profit_abs > 0 else 'neg' if t.profit_abs < 0 else ''}\">"
        f"{'' if t.is_open else f'{t.profit_abs:+,.2f}'}</td><td>{'open' if t.is_open else html.escape(str(t.exit_reason))}</td></tr>"
        for t in trades.itertuples())

    badge = {"paper": "PAPER TRADING (simulated fills, no real money)",
             "backtest": "BACKTEST (historical simulation)",
             "live": "LIVE TRADING"}[mode]
    basis = {"paper": "Simulated (paper) trading against live Binance prices; fills assume the quoted price and exchange fees, without slippage.",
             "backtest": "Historical backtest: simulated fills on past candle prices with exchange fees, without slippage. Backtests are hypothetical and benefit from hindsight in strategy design.",
             "live": "Live trading results from exchange fills, net of exchange fees."}[mode]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bench_ret = f"{benchmark_pair}: {_pct(bench.iloc[-1] - 1)}" if bench is not None else ""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
 :root {{ --text:#1a1d23; --muted:#667085; --border:#e2e5ea; --pos:#12805c; --neg:#c4320a; --bg:#fff; --soft:#f6f7f9; }}
 body {{ font:13px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; color:var(--text); background:var(--bg); margin:0; }}
 main {{ max-width:820px; margin:0 auto; padding:28px 24px 48px; }}
 h1 {{ font-size:22px; margin:0; }} h2 {{ font-size:14px; margin:26px 0 8px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
 .meta {{ color:var(--muted); margin:4px 0 18px; }}
 .badge {{ display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:99px; background:#fff7e6; color:#b54708; border:1px solid #f5d9a8; }}
 .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
 .card {{ border:1px solid var(--border); border-radius:8px; padding:10px 12px; }}
 .card .label {{ font-size:11px; color:var(--muted); }} .card .value {{ font-size:19px; font-weight:600; font-variant-numeric:tabular-nums; }}
 .card .sub {{ font-size:11px; color:var(--muted); }}
 table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
 th, td {{ padding:5px 7px; border-bottom:1px solid var(--border); text-align:right; white-space:nowrap; }}
 th:first-child, td:first-child {{ text-align:left; }} thead th {{ font-size:11px; color:var(--muted); font-weight:500; }}
 td.ytd {{ font-weight:600; background:var(--soft); }}
 .pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
 .two {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
 .chart {{ width:100%; height:auto; }} .chart .grid {{ stroke:var(--border); }} .chart .axis {{ font-size:10px; fill:var(--muted); }}
 .legend {{ display:flex; gap:14px; font-size:11px; color:var(--muted); }} .key i {{ display:inline-block; width:10px; height:3px; margin-right:5px; vertical-align:middle; }}
 .small {{ font-size:11px; color:var(--muted); }}
 .scroll {{ overflow-x:auto; }} .two > * {{ min-width:0; }} .two td:first-child {{ white-space:normal; }} .meta, h1 {{ overflow-wrap:anywhere; }}
 .disclosure {{ font-size:11px; color:var(--muted); border-top:1px solid var(--border); margin-top:28px; padding-top:10px; }}
 @media (max-width:640px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} .two {{ grid-template-columns:1fr; }} }}
 @media print {{ main {{ padding:0; max-width:none; }} h2 {{ break-after:avoid; }} .appendix {{ break-before:page; }} }}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<div class="meta">{start:%d %b %Y} – {end:%d %b %Y} · {html.escape(source_label)} · <span class="badge">{badge}</span></div>

<div class="cards">
 {card("Latest month", _pct(last_month), f"{eq.index[-1]:%B %Y}")}
 {card("Year to date", _pct(ytd), str(year))}
 {card("Since inception", _pct(stats["total_return"]), bench_ret)}
 {card("Equity", f"{eq.iloc[-1]:,.0f}", f"from {start_balance:,.0f} USDT")}
 {card("Max drawdown", _pct(-stats["max_drawdown"]), "peak to trough")}
 {card("Sharpe", _num(stats["sharpe"]), "annualised, rf = 0")}
 {card("Sortino", _num(stats["sortino"]), "annualised")}
 {card("Calmar", _num(stats["calmar"]), "CAGR / max DD")}
</div>

<h2>Equity vs benchmark</h2>
{_svg_lines(lines, rebased=True)}
<h2>Drawdown</h2>
{_svg_lines([("Drawdown", dd, "#c4320a")], height=140, pct_axis=True, fill_below_zero=True)}

<h2>Monthly returns</h2>
<div class="scroll"><table><thead><tr><th></th>{"".join(f"<th>{m}</th>" for m in month_names)}<th>Year</th></tr></thead><tbody>{grid_rows}</tbody></table></div>

<div class="two">
 <div><h2>Risk</h2><table><tbody>
  <tr><td>CAGR</td><td>{_pct(stats["cagr"])}</td></tr>
  <tr><td>Volatility (ann.)</td><td>{stats["volatility"] * 100:.1f}%</td></tr>
  <tr><td>Max drawdown</td><td>{stats["max_drawdown"] * 100:.1f}%</td></tr>
  <tr><td>Time in market</td><td>{tstats["time_in_market"] * 100:.0f}%</td></tr>
  <tr><td>Open positions at report date</td><td>{tstats["open_positions"]}</td></tr>
 </tbody></table></div>
 <div><h2>Trades</h2><table><tbody>
  <tr><td>Closed trades</td><td>{tstats["trades"]}</td></tr>
  <tr><td>Win rate</td><td>{tstats["win_rate"] * 100:.1f}%</td></tr>
  <tr><td>Profit factor</td><td>{_num(tstats["profit_factor"])}</td></tr>
  <tr><td>Average win / loss</td><td>{_pct(tstats["avg_win_pct"], 2)} / {_pct(tstats["avg_loss_pct"], 2)}</td></tr>
  <tr><td>Average holding time</td><td>{tstats["avg_hold_hours"]:.0f} h</td></tr>
 </tbody></table></div>
</div>

<h2>Best and worst pairs</h2>
<div class="scroll"><table><thead><tr><th>Pair</th><th>Trades</th><th>P&amp;L (USDT)</th></tr></thead><tbody>{pair_rows}</tbody></table></div>

<div class="appendix"><h2>Trade log</h2>
<div class="scroll"><table><thead><tr><th>Pair</th><th>Opened (UTC)</th><th>Closed (UTC)</th><th>Stake</th><th>P&amp;L</th><th>Exit</th></tr></thead>
<tbody>{trade_rows}</tbody></table></div></div>

<p class="disclosure"><b>Disclosures.</b> {basis}
Returns are calculated on a realised basis (equity changes when a trade closes); open positions are not marked to market.
Ratios use daily returns, 365-day annualisation and a zero risk-free rate. Past performance, simulated or live, does not indicate future results.
This document is for information only and is not an offer, solicitation or financial advice. Generated {generated} by ridgeline.</p>
</main></body></html>"""


def session_balance(db_path: Path) -> float:
    """Starting balance of a panel paper session (its <id>.config.json)."""
    cfg_path = Path(db_path).with_suffix(".config.json")
    try:
        return float(json.loads(cfg_path.read_text())["dry_run_wallet"])
    except (OSError, ValueError, KeyError):
        return 1000.0
