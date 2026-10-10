"""Build the case study (research-paper format, black theme) as a PDF.

The market background is the earlier case study's analysis, condensed, with its
sources kept. The empirical sections are generated from the latest research
results, so re-running this after the monthly `research.py gates` keeps the
paper current:
  - user_data/research/latest.json      gates at 1000 USDT
  - user_data/research/latest-65.json   gates at 65 USDT (~100 AUD), if present

Usage (from the project root, with the venv Python):
    python scripts/case_study.py [--browser PATH] [-o "Crypto Trading Bot Case Study.pdf"]

Needs Microsoft Edge, Google Chrome or Chromium to print the PDF. The HTML is
kept in build/case-study/.
"""

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ridgeline import USERDIR, __version__  # noqa: E402

RESEARCH = USERDIR / "research"
OUT = ROOT / "Crypto Trading Bot Case Study.pdf"
BUILD = ROOT / "build" / "case-study"

# One-off experiments run at 65 USDT, recorded in RESEARCH.md.
RISK_SCALING = [  # risk per trade, portfolio caps, IS %, OOS %, OOS max DD %, OOS PF
    ("1.5% (default)", "35% / 4.5% / 3% / 15%", 5.76, 17.47, 9.66, 1.44),
    ("3.0%", "50% / 9% / 6% / 25%", 26.92, 20.73, 19.15, 1.26),
    ("4.5%", "70% / 13.5% / 9% / 30%", -20.33, 13.09, 27.05, 1.14),
]
ROTATION_LOOKBACK = [(7, -15.36), (10, 25.10), (14, 37.17), (21, -3.96), (30, -7.56), (60, -5.23)]

BLUE, ORANGE = "#3987e5", "#d95926"  # validated on the black surface
GOOD, BAD = "#0ca30c", "#d03b3b"

REFERENCES = [
    ("Top 7 Crypto Trading Bots in 2026 - TradeSanta", "https://tradesanta.com/top-crypto-bots/"),
    ("Best Crypto Trading Bots 2026: Types & How to Choose | Bitsgap blog", "https://bitsgap.com/blog/best-crypto-trading-bots-in-2026-strategies-types-and-how-to-choose"),
    ("Best Crypto Trading Bots for Beginners (2026 Guide)", "https://www.tv-hub.org/guide/crypto-trading-bots-beginners"),
    ("AI Crypto Trading Bots Comparison 2026: 6 Bots Ranked - Tacavar", "https://tacavar.com/blog/ai-crypto-trading-bot-comparison-table-2026/"),
    ("Best Automated Trading Platforms for Crypto in 2026: Bots vs Strategy Builders", "https://www.coinquant.ai/compare/best-automated-trading-platforms-for-crypto-in-2026-bots-vs-strategy-builders-ranked"),
    ("Explained: The 3Commas Breach (December 2022) - Halborn", "https://www.halborn.com/blog/post/explained-the-3commas-breach-december-2022"),
    ("8 Significant API Breaches of Recent Years - Nordic APIs", "https://nordicapis.com/8-significant-api-breaches-of-recent-years/"),
    ("Digital assets: Financial products and services - ASIC", "https://www.asic.gov.au/regulatory-resources/digital-transformation/digital-assets-financial-products-and-services"),
    ("Best Automated Trading Platforms Australia 2026 | EAs & Bots", "https://www.compareforexbrokers.com.au/platforms/automated-trading/"),
    ("Keeping crypto records | Australian Taxation Office", "https://www.ato.gov.au/individuals-and-families/investments-and-assets/crypto-asset-investments/keeping-crypto-records"),
    ("Best Crypto Trading Bots in 2026 | altFINS", "https://altfins.com/best/crypto-trading-and-investing/crypto-trading-bots/"),
    ("3Commas Review 2026: Trading Bots, Pricing, Security and AI Tools", "https://coinbureau.com/review/3commas-review"),
    ("AI Trading Bot Risk Management: Complete 2025 Guide - 3Commas", "https://3commas.io/blog/ai-trading-bot-risk-management-guide-2025"),
    ("3Commas Review - Gainium", "https://gainium.io/review/3commas"),
    ("Exploring Signal Bots and DCA Bots on 3Commas", "https://3commas.io/blog/exploring-signal-bots-and-dca-bots-on-3commas-guide"),
    ("2026 Cryptohopper Review - Features & How To Get Started", "https://coinsutra.com/cryptohopper-review/"),
    ("Cryptohopper Review - Gainium", "https://gainium.io/review/cryptohopper"),
    ("Cryptohopper Review, Pricing, Features and Alternatives - Growlonix", "https://www.growlonix.com/support/article/cryptohopper-review-pricing-features-and-alternatives"),
    ("3Commas: Crypto Trading Bots & Automation Platform", "https://3commas.io/"),
    ("We're Improving Our Subscription Plans - 3Commas", "https://3commas.io/blog/were-improving-our-subscription-plans"),
    ("AI Trading Bots: Features & Analysis for Different Trading Styles", "https://3commas.io/blog/essential-features-of-ai-trading-bots-in-2025-comp"),
    ("Cryptohopper Review: The Dutch Cloud Bot for Strategy Tinkerers", "https://airdropalert.com/blogs/cryptohopper-review/"),
    ("Cryptohopper Reviews 2026: Details, Pricing, & Features - G2", "https://www.g2.com/products/cryptohopper/reviews"),
    ("Cryptohopper Reviews - Honest User Feedback & Ratings", "https://www.cryptohopper.com/reviews"),
    ("Pionex Review 2026: Features, Fees, and Free Trading Bots", "https://coinbureau.com/review/pionex-review"),
    ("Pionex Review 2026: 12 Free Bots, 0.05% Fees - Is It Worth It?", "https://gainium.io/review/pionex"),
    ("Is Pionex Safe? (Read Before You Trade) - CoinLedger", "https://coinledger.io/learn/is-pionex-safe"),
    ("Read Customer Service Reviews of bitsgap.com - Trustpilot Reviews", "https://au.trustpilot.com/review/bitsgap.com"),
    ("Bitsgap Review 2026 - FAQs, Pricing, Discount & My Experience", "https://coinsutra.com/bitsgap-review/"),
    ("Bitsgap Review 2025: Honest Look at Bots, Features, and Competitors", "https://finestel.com/blog/bitsgap-review/"),
    ("2026 Bitsgap Review: Pros, Cons, Pricing, and More! - BitDegree.org", "https://www.bitdegree.org/crypto/bitsgap-review"),
    ("The 10 Best Crypto Trading Bots in 2026 (Reviewed) - CoinLedger", "https://coinledger.io/tools/best-crypto-trading-bots"),
    ("Crypto Trading Bot Database - Every Bot Ranked & Compared", "https://gainium.io/bots"),
    ("3Commas API leak FAQ for customers", "https://3commas.io/blog/api-security-incident-faq"),
    ("3Commas CEO Confirms API Database Leak - Binance", "https://www.binance.com/en/square/post/12-29-2022-3commas-ceo-confirms-api-database-leak-140737"),
    ("Update on investigation into API keys and attacks on exchanges", "https://3commas.io/blog/december-10-update-on-investigation-api-key-exchange-attacks"),
    ("RE: False Rumors on API Leaks or Exposure of our Database", "https://3commas.io/blog/response-to-false-rumors-api-leaks"),
    ("Notice on API data disclosure incident - 3Commas", "https://3commas.io/blog/notice-on-api-data-disclosure-incident"),
    ("The FBI is reportedly probing 3Commas after API keys leaked", "https://www.bitdefender.com/en-us/blog/hotforsecurity/the-fbi-is-reportedly-probing-3commas-after-api-keys-leaked-on-twitter"),
    ("3Commas hit with second security breach in a year - Blockworks", "https://blockworks.com/news/3commas-security-breach"),
    ("Are you providing digital assets financial services unlicensed? Act now", "https://hamiltonlocke.com.au/are-you-providing-digital-assets-financial-services-unlicensed-act-now/"),
    ("Does Your Crypto Business Need an AFS Licence? A 101 Guide", "https://afslhouse.com.au/insights/does-your-crypto-business-need-an-australian-financial-services-afs-licence/"),
    ("ASIC extends no-action position for digital asset businesses to 30 September 2026", "https://www.asic.gov.au/about-asic/news-centre/news-items/asic-extends-no-action-position-for-digital-asset-businesses-to-30-september-2026"),
    ("Deadline looms for digital asset businesses to apply for a licence", "https://www.asic.gov.au/about-asic/news-centre/news-items/deadline-looms-for-digital-asset-businesses-to-apply-for-a-licence"),
    ("ASIC strengthens AI trading safeguards and streamlines market integrity rules", "https://www.asic.gov.au/about-asic/news-centre/find-a-media-release/2026-releases/26-226mr-asic-strengthens-ai-trading-safeguards-and-streamlines-market-integrity-rules"),
    ("7 Best AI Trading Bots Australia 2026 | ASIC Regulated", "https://theinvestorscentre.com/best-trading-platforms/best-ai-trading-bots-australia/"),
    ("Crypto Tax Australia Guide 2026: ATO Rules - Count On Sheep", "https://countonsheep.com/crypto-tax-by-country/australia/crypto-tax-guide"),
    ("How to work out and report CGT on crypto | Australian Taxation Office", "https://www.ato.gov.au/individuals-and-families/investments-and-assets/crypto-asset-investments/how-to-work-out-and-report-cgt-on-crypto"),
    ("MSB license : r/Pionex - Reddit", "https://www.reddit.com/r/Pionex/comments/17izuv4/msb_license/"),
    ("Disclaimer - Pionex Blog", "https://www.pionex.com/blog/disclaimer/"),
    ("Freqtrade documentation", "https://www.freqtrade.io/en/stable/"),
    ("Ridgeline source code and research results", "https://github.com/devam2001/ridgeline"),
]
FREQTRADE_REF, REPO_REF = 51, 52


# ---------------------------------------------------------------- formatting

def esc(text) -> str:
    return html.escape(str(text))


def pct(v, digits: int = 1, sign: bool = True) -> str:
    if v is None:
        return "–"
    return f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"


def cite(*numbers: int) -> str:
    return f'<sup class="cite">[{", ".join(str(n) for n in numbers)}]</sup>'


def table(head: list[str], rows: list[list], *, num_from: int = 1, caption: str = "", cls: str = "") -> str:
    cells = "".join(f'<th class="{"num" if i >= num_from else ""}">{h}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if i >= num_from else ""}">{c}</td>' for i, c in enumerate(r))
                   + "</tr>" for r in rows)
    cap = f"<caption>{caption}</caption>" if caption else ""
    return f'<table class="{cls}">{cap}<thead><tr>{cells}</tr></thead><tbody>{body}</tbody></table>'


def mark(passed: bool) -> str:
    return (f'<span class="mark" style="color:{GOOD}">✓</span>' if passed
            else f'<span class="mark" style="color:{BAD}">✗</span>')


# ---------------------------------------------------------------- charts

def _hbar(x0: float, x1: float, y: float, h: float, color: str, r: float = 4) -> str:
    """Horizontal bar from baseline x0 to x1, rounded at the data end only."""
    left, right = min(x0, x1), max(x0, x1)
    r = min(r, (right - left) / 2, h / 2)
    if x1 >= x0:  # rounded on the right
        d = (f"M{left:.1f},{y:.1f} H{right - r:.1f} Q{right:.1f},{y:.1f} {right:.1f},{y + r:.1f} "
             f"V{y + h - r:.1f} Q{right:.1f},{y + h:.1f} {right - r:.1f},{y + h:.1f} H{left:.1f} Z")
    else:  # rounded on the left
        d = (f"M{right:.1f},{y:.1f} H{left + r:.1f} Q{left:.1f},{y:.1f} {left:.1f},{y + r:.1f} "
             f"V{y + h - r:.1f} Q{left:.1f},{y + h:.1f} {left + r:.1f},{y + h:.1f} H{right:.1f} Z")
    return f'<path d="{d}" fill="{color}"/>'


def _vbar(x: float, base: float, top: float, w: float, color: str, r: float = 4) -> str:
    """Vertical bar from baseline y=base to y=top, rounded at the data end only."""
    hi, lo = min(base, top), max(base, top)
    r = min(r, (lo - hi) / 2, w / 2)
    if top <= base:  # positive: rounded at the top
        d = (f"M{x:.1f},{lo:.1f} V{hi + r:.1f} Q{x:.1f},{hi:.1f} {x + r:.1f},{hi:.1f} "
             f"H{x + w - r:.1f} Q{x + w:.1f},{hi:.1f} {x + w:.1f},{hi + r:.1f} V{lo:.1f} Z")
    else:  # negative: rounded at the bottom
        d = (f"M{x:.1f},{hi:.1f} V{lo - r:.1f} Q{x:.1f},{lo:.1f} {x + r:.1f},{lo:.1f} "
             f"H{x + w - r:.1f} Q{x + w:.1f},{lo:.1f} {x + w:.1f},{lo - r:.1f} V{hi:.1f} Z")
    return f'<path d="{d}" fill="{color}"/>'


def chart_oos_returns(rows: list[tuple[str, float]], refs: list[tuple[str, float]]) -> str:
    """Out-of-sample return per strategy, with buy-and-hold reference lines."""
    width, label_w, pad_r, bar_h, gap = 720, 150, 60, 22, 14
    plot_bottom = 30 + len(rows) * (bar_h + gap)
    height = plot_bottom + 14 * len(refs) + 8
    lo = min([0.0, *(v for _, v in rows), *(v for _, v in refs)]) - 5
    hi = max([0.0, *(v for _, v in rows)]) + 5

    def x(v):
        return label_w + (width - label_w - pad_r) * (v - lo) / (hi - lo)

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
             f'aria-label="Out-of-sample return by strategy">']
    for t in range(int(lo // 10) * 10, int(hi) + 1, 10):
        if t < lo:
            continue
        parts.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="26" y2="{plot_bottom}" class="grid"/>'
                     f'<text x="{x(t):.1f}" y="16" class="axis" text-anchor="middle">{t:+d}%</text>')
    parts.append(f'<line x1="{x(0):.1f}" x2="{x(0):.1f}" y1="26" y2="{plot_bottom}" class="zero"/>')
    for k, (name, v) in enumerate(refs):
        label_y = plot_bottom + 12 + 14 * k
        parts.append(f'<line x1="{x(v):.1f}" x2="{x(v):.1f}" y1="26" y2="{plot_bottom}" class="ref"/>'
                     f'<text x="{x(v) + 4:.1f}" y="{label_y}" class="ref-label">{esc(name)} {v:+.1f}%</text>')
    for i, (name, v) in enumerate(rows):
        y = 34 + i * (bar_h + gap)
        parts.append(f'<text x="{label_w - 10}" y="{y + bar_h * 0.7:.1f}" class="label" text-anchor="end">{esc(name)}</text>')
        parts.append(_hbar(x(0), x(v), y, bar_h, BLUE))
        # positive: label past the bar end; negative: inside the bar end, clear of the reference lines
        tx = x(v) + 6
        parts.append(f'<text x="{tx:.1f}" y="{y + bar_h * 0.7:.1f}" class="value">{v:+.1f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def chart_walkforward(windows: list[str], a: tuple[str, list[float]], b: tuple[str, list[float]],
                      market: list[float]) -> str:
    """Return per walk-forward window for two strategies (grouped bars)."""
    width, pad_l, pad_r, top, plot_h = 720, 44, 8, 34, 190
    height = top + plot_h + 54
    values = a[1] + b[1]
    lo, hi = min(0.0, *values) - 2, max(0.0, *values) + 2
    n = len(windows)
    band = (width - pad_l - pad_r) / n
    bar_w = min(12.0, (band - 8) / 2)

    def y(v):
        return top + plot_h * (hi - v) / (hi - lo)

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
             f'aria-label="Walk-forward window returns">']
    parts.append(f'<g class="legend"><rect x="{pad_l}" y="6" width="10" height="10" rx="2" fill="{BLUE}"/>'
                 f'<text x="{pad_l + 15}" y="15" class="label">{esc(a[0])}</text>'
                 f'<rect x="{pad_l + 140}" y="6" width="10" height="10" rx="2" fill="{ORANGE}"/>'
                 f'<text x="{pad_l + 155}" y="15" class="label">{esc(b[0])}</text></g>')
    step = 10 if hi - lo > 40 else 5
    for t in range(int(lo // step) * step, int(hi) + 1, step):
        if t < lo:
            continue
        parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
                     f'<text x="{pad_l - 6}" y="{y(t) + 3:.1f}" class="axis" text-anchor="end">{t:+d}%</text>')
    parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="zero"/>')
    for i, label in enumerate(windows):
        cx = pad_l + band * i + band / 2
        parts.append(_vbar(cx - bar_w - 1, y(0), y(a[1][i]), bar_w, BLUE))
        parts.append(_vbar(cx + 1, y(0), y(b[1][i]), bar_w, ORANGE))
        parts.append(f'<text x="{cx:.1f}" y="{top + plot_h + 16}" class="axis" text-anchor="middle">{esc(label)}</text>'
                     f'<text x="{cx:.1f}" y="{top + plot_h + 30}" class="axis muted" text-anchor="middle">{market[i]:+.0f}%</text>')
    parts.append(f'<text x="{pad_l - 6}" y="{top + plot_h + 30}" class="axis muted" text-anchor="end">mkt</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- document

CSS = """
@page { size: A4; margin: 17mm 16mm 18mm; background: #000;
  @bottom-center { content: "Ridgeline case study · page " counter(page) " of " counter(pages);
                   color: #8a8a86; font: 8pt "Segoe UI", system-ui, sans-serif; } }
html, body { background: #000; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; box-sizing: border-box; }
body { margin: 0; color: #e4e4e1; font: 10.4pt/1.55 Cambria, Georgia, "Times New Roman", serif; }
h1, h2, h3, .sans, caption, table, .chart text, .meta, .abstract h2 { font-family: "Segoe UI", system-ui, sans-serif; }
h1 { font-size: 21pt; line-height: 1.2; margin: 0 0 6pt; color: #fff; font-weight: 650; }
.subtitle { font-size: 12pt; color: #c3c2b7; margin: 0 0 14pt; }
.meta { color: #a3a29a; font-size: 9pt; margin-bottom: 16pt; }
h2 { font-size: 13pt; color: #fff; margin: 20pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid #333; break-after: avoid; }
h3 { font-size: 10.8pt; color: #f2f2ee; margin: 13pt 0 4pt; break-after: avoid; }
p { margin: 0 0 7pt; text-align: justify; hyphens: auto; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; } li { margin-bottom: 2pt; }
a { color: #86b6ef; text-decoration: none; overflow-wrap: anywhere; }
.cite { color: #86b6ef; font-size: 7pt; }
.abstract { border: 1px solid #333; border-radius: 6px; padding: 10pt 13pt; margin: 0 0 12pt; background: #0d0d0d; }
.abstract h2 { margin: 0 0 4pt; border: 0; font-size: 10pt; text-transform: uppercase; letter-spacing: .06em; color: #c3c2b7; }
.keywords { font-size: 9pt; color: #a3a29a; margin-top: 6pt; }
table { width: 100%; border-collapse: collapse; margin: 6pt 0 12pt; font-size: 8.6pt; break-inside: avoid; }
caption { caption-side: top; text-align: left; color: #c3c2b7; font-size: 8.6pt; padding-bottom: 4pt; }
th, td { padding: 3.5pt 6pt; border-bottom: 1px solid #262626; text-align: left; vertical-align: top; }
th { color: #a3a29a; font-weight: 500; border-bottom-color: #444; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.mark { font-weight: 700; }
figure { margin: 8pt 0 14pt; break-inside: avoid; }
figcaption { font: 8.6pt "Segoe UI", system-ui, sans-serif; color: #c3c2b7; margin-top: 4pt; }
.chart { width: 100%; height: auto; }
.chart .grid { stroke: #262626; } .chart .zero { stroke: #8a8a86; }
.chart .ref { stroke: #a3a29a; stroke-dasharray: 4 3; }
.chart .ref-label, .chart .axis { font-size: 10px; fill: #a3a29a; } .chart .muted { fill: #6f6e69; }
.chart .label { font-size: 11px; fill: #e4e4e1; } .chart .value { font-size: 11px; fill: #fff; font-weight: 600; }
.callout { border-left: 3px solid #3987e5; background: #0d0d0d; padding: 7pt 11pt; margin: 8pt 0 12pt; }
.small { font-size: 8.6pt; color: #a3a29a; }
.refs { font-size: 8.2pt; color: #c3c2b7; padding-left: 22pt; } .refs li { margin-bottom: 2.5pt; }
.pagebreak { break-before: page; }
"""


def build_html(report: dict, small: dict | None) -> str:
    strategies = report["strategies"]
    bm = report["benchmarks"]
    is_range, oos_range = report["periods"]["is"], report["periods"]["oos"]
    order = sorted(strategies, key=lambda s: -sum(g["pass"] for g in strategies[s]["gates"]))
    best = order[0]
    b = strategies[best]
    n_gates = len(b["gates"])
    eligible = [s for s in order if strategies[s]["eligible_for_paper"]]
    rot = strategies.get("MomentumRotation1d")
    today = date.today().strftime("%d %B %Y")

    def passed(s):
        return sum(g["pass"] for g in strategies[s]["gates"])

    summary_rows = [[f"<b>{esc(s)}</b>" if s == best else esc(s),
                     pct(strategies[s]["is"]["profit_pct"]), pct(strategies[s]["oos"]["profit_pct"]),
                     pct(strategies[s]["oos"]["max_drawdown_pct"], sign=False), f'{strategies[s]["oos"]["profit_factor"]:.2f}',
                     strategies[s]["oos"]["trades"], pct(strategies[s]["walkforward"]["pct_profitable"], 0, sign=False),
                     f"{passed(s)} / {n_gates}"] for s in order]
    gate_rows = []
    for i, g in enumerate(b["gates"]):
        gate_rows.append([esc(g["label"])] + [f'{mark(strategies[s]["gates"][i]["pass"])} {esc(_short(strategies[s]["gates"][i]["value"]))}'
                                              for s in order])
    wf_windows = [w["window"] for w in b["walkforward"]["windows"]]
    wf_labels = [_window_label(w) for w in wf_windows]
    wf_table = [[esc(w.replace(" -> ", " → "))] + [pct(strategies[s]["walkforward"]["windows"][i]["profit_pct"]) for s in order]
                + [pct(b["walkforward"]["windows"][i]["market_change_pct"], 0)] for i, w in enumerate(wf_windows)]

    small_section = ""
    if small:
        rows = []
        for s in order:
            if s not in small["strategies"]:
                continue
            o = small["strategies"][s]["oos"]
            rows.append([esc(s), f'{o["starting_balance"]:.0f} → {o["final_balance"]:.2f}', pct(o["profit_pct"]),
                         pct(strategies[s]["oos"]["profit_pct"]), pct(o["max_drawdown_pct"], sign=False), o["trades"],
                         f'{sum(g["pass"] for g in small["strategies"][s]["gates"])} / {n_gates}'])
        small_section = table(["Strategy", "OOS balance (USDT)", "OOS return at 65", "OOS return at 1000", "Max DD", "Trades", "Gates"],
                              rows, caption="Table 6. The same out-of-sample test with a 65 USDT (≈100 AUD) account.")

    figure1 = chart_oos_returns([(s, strategies[s]["oos"]["profit_pct"]) for s in order],
                                [("Equal-weight buy & hold", bm["oos"]["equal_weight_pct"]), ("BTC", bm["oos"]["btc_pct"])])
    figure2 = ""
    if rot is not None and best != "MomentumRotation1d":
        figure2 = chart_walkforward(
            wf_labels, (best, [w["profit_pct"] for w in b["walkforward"]["windows"]]),
            ("MomentumRotation1d", [w["profit_pct"] for w in rot["walkforward"]["windows"]]),
            [w["market_change_pct"] for w in b["walkforward"]["windows"]])

    wf_b = b["walkforward"]["windows"]
    up = max(range(len(wf_b)), key=lambda i: wf_b[i]["market_change_pct"])
    down = min(range(len(wf_b)), key=lambda i: wf_b[i]["market_change_pct"])
    regime_text = (f"{esc(best)}'s edge is mainly <i>not losing money in downturns</i>: in the market's worst quarter "
                   f"({wf_b[down]['window'][:7]}, {pct(wf_b[down]['market_change_pct'], 0)}) it returned {pct(wf_b[down]['profit_pct'])}, "
                   f"because the BTC regime filter keeps it out of broad declines. In the strongest quarter "
                   f"({wf_b[up]['window'][:7]}, {pct(wf_b[up]['market_change_pct'], 0)}) it made {pct(wf_b[up]['profit_pct'])}, "
                   "catching only part of the move.")
    if rot is not None:
        wf_r = rot["walkforward"]["windows"]
        regime_text += (f" MomentumRotation1d was designed to hold the leaders through rallies, but the walk-forward does not show "
                        f"that: it made only {pct(wf_r[up]['profit_pct'])} in that strongest quarter (and {pct(wf_r[down]['profit_pct'])} "
                        "in the worst one). Its rally-capture rationale is not supported by these results.")

    verdict = (f"{len(eligible)} of {len(strategies)} strategies passed every gate" if eligible
               else f"none of the {len(strategies)} strategies passed every gate, so none is eligible for real money")
    rot_text = ""
    if rot is not None:
        rot_text = (f"A new cross-sectional momentum-rotation candidate returned {pct(rot['oos']['profit_pct'])} out of sample "
                    f"with a {pct(rot['oos']['max_drawdown_pct'], sign=False)} maximum drawdown and passed {passed('MomentumRotation1d')} of "
                    f"{n_gates} gates, but its in-sample results were highly sensitive to its one key parameter. ")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Ridgeline case study</title><style>{CSS}</style></head><body>

<h1>Automated Cryptocurrency Trading for Retail Accounts</h1>
<p class="subtitle">A market case study and an out-of-sample evaluation of an open-source, self-hosted trading system</p>
<div class="meta">devam2001 · Ridgeline project{cite(REPO_REF)} · {today} · software v{__version__}<br>
Research format revision of the earlier product case study “The 2026 Landscape of Automated Cryptocurrency Trading Platforms”.</div>

<div class="abstract"><h2>Abstract</h2>
<p>Algorithmic execution has become mainstream in cryptocurrency markets: the trading-bot sector was valued at an estimated
US$47.43 billion in 2026, with roughly 42% of active traders automating to some degree{cite(1)}. Commercial platforms sell
infrastructure, templates and strategy marketplaces, but their fixed subscription costs weigh heavily on small accounts, and
marketplace strategies are often curve-fitted to past data{cite(4, 17)}. This paper first condenses a landscape analysis of bot
architectures, platforms, security failures and Australian regulation. It then evaluates Ridgeline, an open-source system built on
Freqtrade{cite(FREQTRADE_REF)}, under a deliberately strict protocol: point-in-time pair selection, a strict in-sample /
out-of-sample split ({esc(is_range[0])} to {esc(is_range[1])} / {esc(oos_range[0])} to {esc(oos_range[1])}), quarterly walk-forward windows,
fee stress, lookahead and warm-up checks, and nine pass/fail go-live gates, all under portfolio risk limits. Over an
out-of-sample period in which an equal-weight basket of the same coins fell {pct(bm['oos']['equal_weight_pct'])}, the best
strategy, {esc(best)}, returned {pct(b['oos']['profit_pct'])} with a {pct(b['oos']['max_drawdown_pct'], sign=False)} maximum drawdown.
It passed {passed(best)} of {n_gates} gates. {rot_text}Overall, {verdict}. An account of about 100 AUD trades the same
signals with the same percentage results, but doubling the risk per trade added little return while doubling drawdown.</p>
<p class="keywords"><b>Keywords:</b> algorithmic trading, cryptocurrency, backtesting, out-of-sample testing, walk-forward analysis,
point-in-time universe, risk management, Freqtrade, retail investors, Australia</p></div>

<h2>1. Introduction</h2>
<p>Unlike equity and foreign-exchange venues, cryptocurrency exchanges trade continuously across a fragmented global liquidity
network{cite(2)}. Manual traders face fatigue and well-documented behavioural failures (panic selling in drawdowns, chasing
parabolic rallies, revenge trading after losses), which automated, rule-based execution removes{cite(2)}. The industry has grown
from open-source command-line scripts into software-as-a-service ecosystems with visual strategy builders, backtesting engines and,
increasingly, agentic AI layers{cite(4)}. That growth brought second-order problems: centralised storage of exchange API keys created
large attack surfaces{cite(6)}, and retail automation now meets financial-services licensing, tax and anti-money-laundering rules{cite(8)}.</p>
<p>Marketing for trading bots tends to report backtests, which are easy to make look good. This study asks whether a strategy still
shows an edge when it is tested the way a sceptical reviewer would test it, and whether that holds for a very small account.
Four questions guide it:</p>
<ol>
<li><b>RQ1.</b> How is the 2026 automated-trading market structured, and what risks does it carry for retail users?</li>
<li><b>RQ2.</b> Does any of four rule-based strategies show an edge that survives point-in-time, out-of-sample testing and nine go-live gates?</li>
<li><b>RQ3.</b> Can the system trade an account of about 100 AUD, and with what results?</li>
<li><b>RQ4.</b> Does taking more risk per trade produce proportionally more profit?</li>
</ol>

<h2>2. Background: the automated-trading landscape</h2>
<h3>2.1 Algorithmic architectures</h3>
<p>Bots are specialised tools that suit particular market regimes{cite(2)}. Grid bots layer limit orders around a price and harvest
range-bound oscillation, but in a sustained trend they either accumulate a falling asset or sell out of a rally too early{cite(2, 3, 12)}.
Dollar-cost-averaging (DCA) bots add geometrically scaled “safety orders” as price moves against a position; this lowers the average
entry but can consume capital rapidly in a crash, so professional use needs drawdown thresholds and caps on safety orders{cite(12, 13, 14)}.
Signal bots execute alerts from external charting platforms and depend on fragile webhook plumbing{cite(3, 12)}. Arbitrage and
market-making bots chase spreads that institutional high-frequency firms close in milliseconds, which makes them the highest-risk
category for retail users{cite(3, 17)}. Trend-following bots ride momentum but are whipsawed in sideways markets{cite(2)}.</p>
{table(["Architecture", "Mechanism", "Best market", "Main systemic risk", "Complexity"], [
    ["Grid", "Layered limit orders above and below price", "Sideways, ranging", "Breakouts into a one-way trend", "Low to medium"],
    ["DCA", "Scaled buying into adverse moves", "Choppy, mildly bearish", "Runaway capital use in crashes", "Low"],
    ["Signal", "Executes external webhook alerts", "Depends on the signal", "Latency and delivery failures", "Medium"],
    ["Arbitrage", "Cross-venue price spreads", "Volatile, inefficient", "Spreads eroded by fees and latency", "Very high"],
    ["Trend-following", "Breakouts and moving averages", "Strong directional trends", "Whipsaw losses in sideways chop", "Medium to high"],
], num_from=99, caption="Table 1. Taxonomy of trading-bot architectures (after the original case study" + cite(2, 3, 12, 13) + ").")}

<h3>2.2 Commercial platforms</h3>
<p>The commercial market has stratified by user experience, custody model and pricing{cite(4)}. 3Commas, the most widely adopted
API-driven platform (over 624,000 registered traders), restructured its plans in 2025–26 into Starter, Pro and Expert tiers at
US$20, US$50 and US$140 a month, gating full developer API access behind the top tier, and launched the agentic QuantPilot
environment{cite(4, 11, 14)}. Cryptohopper differentiates on a no-code designer and a strategy marketplace whose incentives
reward curve-fitted strategies{cite(4, 16, 17)}. Pionex embeds free bots in its own exchange, earning a 0.05% trading fee at the
cost of ecosystem lock-in{cite(4, 11, 25)}. Bitsgap focuses on multi-exchange portfolio management and a futures “COMBO” bot{cite(2, 4, 11)}.
At the technical extreme, HaasOnline offers walk-forward backtesting (up to US$749 a month), Gunbot is self-hosted on a one-time
licence, and open-source frameworks such as Freqtrade are free but demand programming skill{cite(4, 11, 33)}.</p>
<div class="callout"><b>Fixed costs and small accounts.</b> A US$29 monthly subscription on a US$1,000 account needs a 34.8% annual
net return just to break even on software{cite(17)}. On a 100 AUD account no subscription is viable at all, which is why this study
evaluates a self-hosted, open-source system with no fixed cost.</div>

<h3>2.3 Security</h3>
<p>Cloud bots hold exchange API keys centrally, which makes them single points of failure{cite(22)}. In late 2022 3Commas users
reported unauthorised trades. The company first attributed them to phishing, until a threat actor published about 100,000 API keys
on 28 December 2022 and the CEO confirmed the leak{cite(34, 35, 36)}. Estimated losses exceeded US$20 million. Because the keys
could not withdraw, attackers used “contra-trading”: they forced victim accounts to buy illiquid coins at inflated prices from the
attackers' own sell orders{cite(6)}. The industry responded with OAuth-style “Fast Connect” authorisation and mandatory IP
whitelisting, yet 3Commas suffered a further breach in October 2023{cite(36, 38, 40)}. Self-hosting keeps keys on the user's own
machine, which is the model Ridgeline follows. Its setup guide restricts keys to spot trading with withdrawals disabled, and keys are
needed only for live trading.</p>

<h3>2.4 Regulation and tax in Australia</h3>
<p>Running a bot on one's own account is generally permitted without a licence, but selling algorithms with performance claims,
offering copy-trading, or pooling client funds can require an Australian Financial Services Licence and may form a managed
investment scheme{cite(8, 9, 42)}. ASIC's no-action position for digital-asset businesses required licence applications by 30 June
2026 and ended on 30 September 2026{cite(41, 43, 44)}, and ASIC has updated its market-integrity rules to strengthen safeguards
around AI-enabled trading{cite(45)}. The ATO treats crypto as a capital-gains-tax asset, so every automated trade is a separate CGT
event that must be recorded{cite(10, 48)}. In the United States, platforms that transmit customer funds must register with FinCEN
and hold state licences; Pionex.US reached consent orders with four states in May 2025{cite(25, 27)}.</p>

<h3>2.5 Toward agentic AI</h3>
<p>LLM-based agents can read news, policy statements and on-chain flows, but they can also hallucinate, for example mistaking satire for a
bankruptcy and liquidating a portfolio{cite(4, 13)}. The emerging consensus is that deterministic risk controls (hard stops, drawdown
thresholds and volume filters) must govern final execution whatever proposes the trade{cite(4, 32)}. Ridgeline applies that principle
to rule-based strategies: a separate risk layer can veto or shrink any trade.</p>

<h2 class="pagebreak">3. System design</h2>
<p>Ridgeline{cite(REPO_REF)} is built on Freqtrade{cite(FREQTRADE_REF)} and trades Binance spot USDT pairs, long only. It has four parts:</p>
<ul>
<li><b>Strategies.</b> Each strategy sizes positions so that a stop-out loses about 1.5% of equity, using ATR-based stops.</li>
<li><b>Portfolio risk layer.</b> Shared by every strategy and enforced in backtests too:
  <ul>
  <li>at most 35% of equity in one position</li>
  <li>at most 4.5% of equity at risk across open stops</li>
  <li>a 3% daily loss limit</li>
  <li>a 15% drawdown halt that only a human can lift</li>
  </ul></li>
<li><b>Operations.</b>
  <ul>
  <li>a paper-trading control panel with a kill switch</li>
  <li>a hash-chained audit trail</li>
  <li>investor-style tear sheets</li>
  <li>a watchdog with alerts</li>
  <li>Docker deployment and a Windows installer</li>
  </ul></li>
<li><b>Research pipeline</b> (Section 4). It ends with the gates in Table 2 and a paper-versus-replay check.</li>
</ul>
<p>The panel can only run dry-run (paper) bots. Live trading is a separate, manual step that the software never takes on its own.</p>

<h2>4. Methodology</h2>
<h3>4.1 Data and universe</h3>
<p>The study uses daily, 4-hour and 1-hour Binance spot candles from June 2022 to {esc(oos_range[1])}. Choosing pairs by today's
trading volume leaks the future into a backtest, because coins that later rallied are over-represented. In an earlier run, a
today's-top-26 list inflated the out-of-sample return to +35.7%{cite(REPO_REF)}. Each backtest window therefore trades the top 25 USDT
pairs by quote volume over the 30 days <i>before</i> it starts. Stablecoins (by name, or price within ±5% of US$1 for 90 days), wrapped and
leveraged tokens, and coins listed less than 210 days earlier are excluded. Delisted coins cannot be downloaded, so some survivorship
bias remains.</p>
<h3>4.2 Protocol</h3>
<p>The in-sample period ({esc(is_range[0])} to {esc(is_range[1])}) is the only data used for design and tuning. The out-of-sample period
({esc(oos_range[0])} to {esc(oos_range[1])}) is used only to judge the result. Every backtest:</p>
<ul>
<li>starts with 1,000 USDT</li>
<li>allows at most three open trades</li>
<li>pays a 0.1% fee per side</li>
<li>has Freqtrade's protections and the portfolio risk limits switched on</li>
</ul>
<p>Walk-forward analysis repeats the test in rolling three-month windows, each with its own point-in-time universe.</p>
<p>The bias checks are:</p>
<ul>
<li><b>Lookahead.</b> Freqtrade's lookahead analysis.</li>
<li><b>Indicator warm-up stability.</b> Freqtrade's recursive analysis, measured at the strategy's own warm-up length or longer.</li>
<li><b>Lookahead for cross-sectional strategies.</b> The Freqtrade tool reloads one pair at a time, which cannot evaluate a ranking
across pairs. For those strategies, the backtest is re-run with later data removed from every pair at once, and every earlier
trade must be identical.</li>
</ul>
{table(["Gate", "Target"], [[esc(g[0]), esc(g[1])] for g in [
    ("Out-of-sample profit", "> 0%"), ("Out-of-sample profit factor", "≥ 1.3"), ("Out-of-sample maximum drawdown", "≤ 20%"),
    ("Out-of-sample Calmar ratio", "≥ 1.0"), ("Out-of-sample trades", "≥ 100"), ("Profitable walk-forward windows", "≥ 60%"),
    ("Out-of-sample profit with fees +50%", "> 0%"), ("Lookahead bias", "none"), ("Indicator deviation at the strategy's warm-up", "≤ 1%"),
    ("Paper trading (4+ weeks) vs replay of the same weeks", "4 checks: ≥ 28 days, ≥ 80% trades matched, return within 2 points, fills ≤ 0.3% per side"),
]], num_from=99, caption="Table 2. Go-live gates. A strategy must pass all nine backtest gates, then the paper-trading check, before any real money is used.")}
<p>Two further experiments address RQ3 and RQ4. The full gate suite is re-run with a 65 USDT starting balance (about 100 AUD at roughly
0.65 USD per AUD, an assumed rate). The best strategy is also re-run at two and three times the risk per trade, with proportionally
looser portfolio caps.</p>

<h3>4.3 Strategies</h3>
<ul>
<li><b>TrendRider4h:</b> a 4-hour Donchian breakout with an ADX trend filter. It trades only when the coin is above its daily EMA200 and
BTC is above its own daily EMA200, and exits on an ATR “chandelier” trailing stop with no profit cap.</li>
<li><b>MomentumRotation1d</b> (new): each day it ranks the whole universe by 14-day volatility-adjusted momentum and holds the top
three while BTC is in an uptrend. A position is sold when its coin drops out of the top six, or on its trailing stop. All settings
were chosen on in-sample data only.</li>
<li><b>EmaRsiMomentum:</b> a 1-hour EMA crossover with RSI, ADX and 4-hour EMA200 filters and a take-profit ladder.</li>
<li><b>TrendDipBuyer1h:</b> buys 1-hour pullbacks inside an established uptrend (mean reversion).</li>
</ul>

<h2 class="pagebreak">5. Results</h2>
<h3>5.1 Headline results</h3>
<p>The two periods were very different markets. In-sample, an equal-weight buy-and-hold of the same point-in-time coins returned
{pct(bm['is']['equal_weight_pct'])} and BTC {pct(bm['is']['btc_pct'])}. Out of sample, the equal-weight basket lost
{pct(bm['oos']['equal_weight_pct'])} and BTC {pct(bm['oos']['btc_pct'])}.</p>
{table(["Strategy", "IS return", "OOS return", "OOS max DD", "OOS PF", "OOS trades", "WF profitable", "Gates"], summary_rows,
       caption="Table 3. In-sample (IS) and out-of-sample (OOS) results with portfolio risk limits (1,000 USDT start). PF = profit factor; WF = walk-forward.")}
<figure>{figure1}
<figcaption>Figure 1. Out-of-sample return by strategy. Dashed lines mark buy-and-hold of the same coins over the same period.</figcaption></figure>

<h3>5.2 Go-live gates</h3>
{table(["Gate"] + [esc(s) for s in order], gate_rows, caption="Table 4. Gate results (✓ pass, ✗ fail), with the measured value.", cls="gates")}
<p>{esc(best)} fails only the sample-size gate ({b['oos']['trades']} trades against 100). The gate stays as it is. Each additional
month of data adds roughly six out-of-sample trades, so the monthly re-run will settle it honestly.
{"MomentumRotation1d fails the sample-size and walk-forward gates." if rot is not None else ""}</p>

<h3>5.3 Consistency across market regimes</h3>
{f'<figure>{figure2}<figcaption>Figure 2. Return per three-month walk-forward window. The row under the labels shows the equal-weight market change (“mkt”). The full table is in Appendix A.</figcaption></figure>' if figure2 else ""}
<p>{regime_text}</p>

<h3>5.4 Small accounts (RQ3)</h3>
<p>Binance requires a minimum order value. With 65 USDT, the risk-based position sizes ranged from about 6 to 26 USDT (median about 8),
which stayed above that minimum, so the strategies placed almost exactly the same trades as with 1,000 USDT and percentage
results are nearly identical. Exchange fees are proportional, and a self-hosted system has no subscription, so a small account carries no
structural cost penalty here. In absolute terms, though, +17% over the 16-month out-of-sample period on 100 AUD is about 17 AUD.</p>
{small_section}

<h3>5.5 More risk per trade (RQ4)</h3>
{table(["Risk per trade", "Caps (position / open risk / daily loss / halt)", "IS return", "OOS return", "OOS max DD", "OOS PF"],
       [[esc(r[0]), esc(r[1]), pct(r[2]), pct(r[3]), pct(r[4], sign=False), f"{r[5]:.2f}"] for r in RISK_SCALING],
       num_from=2, caption="Table 5. TrendRider4h at higher risk (65 USDT start).")}
<p>Doubling the risk added about three points of out-of-sample return but doubled the drawdown and pushed the profit factor
below the 1.3 gate. Tripling it lost money in-sample. More risk did not create more edge. It mostly scaled losses and triggered the
drawdown halts.</p>

<h3>5.6 Parameter sensitivity of the rotation strategy</h3>
{table(["Momentum lookback (days)"] + [str(n) for n, _ in ROTATION_LOOKBACK], [["In-sample return"] + [pct(v) for _, v in ROTATION_LOOKBACK]],
       caption="Table 7. MomentumRotation1d in-sample return by lookback (top 3, 65 USDT start, measured before the warm-up fix).")}
<p>The 14-day setting was the in-sample best, but neighbouring settings fell sharply, from +37% at 14 days to −4% at 21 days. A
peak this narrow suggests part of the in-sample result is luck, so its out-of-sample figure should be read with that caution.</p>

<h2>6. Discussion</h2>
<p><b>RQ1.</b> The market sells infrastructure rather than proven edges. Fixed fees, curve-fitted marketplace strategies and centralised
key storage all work against small retail users{cite(4, 6, 17)}, and Australian rules leave own-account automation largely untouched
but regulate selling it to others{cite(8, 9)}.</p>
<p><b>RQ2.</b> Under strict testing, edges are small and fragile. The protocol itself mattered more than any single strategy idea:</p>
<ul>
<li>moving from hand-picked to point-in-time pairs cut TrendRider4h's in-sample return from +82.5% to +10.8% (before risk limits)</li>
<li>the risk layer then cost some return on the good strategy and halved the losses of the bad one{cite(REPO_REF)}</li>
</ul>
<p>The best strategy's out-of-sample result is respectable against a falling market, but it rests on about 100 trades.</p>
<p><b>RQ3.</b> A 100 AUD account is technically viable on Binance spot with this design. What it cannot do is produce large absolute
profits. <b>RQ4.</b> Raising risk is not a shortcut to “high profit”: return per unit of drawdown got worse.</p>

<h2>7. Limitations</h2>
<ul>
<li><b>Survivorship bias.</b> Coins delisted before the data was downloaded are missing.</li>
<li><b>No slippage model.</b> Backtests fill at candle prices plus fees. Paper trading and the paper-versus-replay gate exist to measure the gap.</li>
<li><b>Small samples and in-sample selection.</b> Both are discussed above. TrendRider4h's parameters were also tuned on an earlier
hand-picked pair list.</li>
<li><b>Untested assumptions.</b> The 65 USDT conversion uses an assumed exchange rate.</li>
<li><b>Unverified market sources.</b> The figures in Section 2 come from the secondary web sources listed and were not independently verified.</li>
</ul>

<h2>8. Conclusion</h2>
<p>Automated trading moves execution from a person to a machine; it does not remove market risk. Measured honestly, with
point-in-time pairs, untouched out-of-sample data, walk-forward windows, stress and bias checks, {verdict}. The best candidate
is close, and the remaining steps are procedural:</p>
<ol>
<li>keep re-running the gates monthly</li>
<li>paper-trade it for at least four weeks</li>
<li>require the paper results to match a replay of the same weeks</li>
</ol>
<p>For small accounts, the practical lessons are to avoid fixed costs, keep risk per trade small, and judge any strategy on data it was
not designed on.</p>
<p class="small">This document describes software and research results. It is not financial advice, and backtests do not predict
future returns. Only risk money you can afford to lose.</p>

<h2 class="pagebreak">Appendix A. Walk-forward windows</h2>
{table(["Window"] + [esc(s) for s in order] + ["Market"], wf_table, caption="Table A1. Return per three-month window (point-in-time pairs, risk limits on, 1,000 USDT start).")}

<h2>References</h2>
<ol class="refs">{"".join(f'<li>{esc(t)}. <a href="{esc(u)}">{esc(u)}</a></li>' for t, u in REFERENCES)}</ol>
</body></html>"""


def _short(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _window_label(window: str) -> str:
    start = window.split(" -> ")[0]
    year, month = start[2:4], int(start[5:7])
    return f"Q{(month - 1) // 3 + 1} {year}"


def find_browser(explicit: str | None) -> str:
    candidates = [explicit] if explicit else [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    sys.exit("No Edge/Chrome/Chromium found to print the PDF; pass --browser PATH.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser", help="Edge, Chrome or Chromium executable")
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    args = ap.parse_args()

    report = json.loads((RESEARCH / "latest.json").read_text())
    small_path = RESEARCH / "latest-65.json"
    small = json.loads(small_path.read_text()) if small_path.exists() else None
    BUILD.mkdir(parents=True, exist_ok=True)
    page = BUILD / "case-study.html"
    page.write_text(build_html(report, small), encoding="utf-8")

    browser = find_browser(args.browser)
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={profile}",
                        f"--print-to-pdf={args.out.resolve()}", page.resolve().as_uri()],
                       check=True, capture_output=True, timeout=180)
    print(args.out)


if __name__ == "__main__":
    main()
