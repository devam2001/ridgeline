// Control panel front end: plain JS, no build step.

const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  return data;
}

// ---------- formatting ----------
const fmtMoney = (v, cur = "USDT") =>
  v == null || isNaN(v) ? "–" : `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })} ${cur}`;
const fmtPct = (v, digits = 2) => (v == null || isNaN(v) ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(digits)}%`);
const signClass = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function tile(label, value, cls = "", sub = "") {
  return `<div class="tile"><div class="label">${esc(label)}</div><div class="value ${cls}">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function table(el, columns, rows, { empty = "Nothing yet.", onRow } = {}) {
  if (!rows || !rows.length) {
    el.innerHTML = `<tr><td class="empty">${esc(empty)}</td></tr>`;
    return;
  }
  const head = `<thead><tr>${columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead>`;
  const body = rows
    .map((r, i) => `<tr ${onRow ? `class="clickable" data-i="${i}"` : ""}>${columns
      .map((c) => {
        const v = c.render ? c.render(r) : esc(r[c.key]);
        return `<td class="${c.num ? "num" : ""} ${c.cls ? c.cls(r) : ""}">${v}</td>`;
      })
      .join("")}</tr>`)
    .join("");
  el.innerHTML = head + `<tbody>${body}</tbody>`;
  if (onRow) el.querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", () => onRow(rows[tr.dataset.i])));
}

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
    try { localStorage.setItem("tab", btn.dataset.tab); } catch {}
    if (btn.dataset.tab === "research") loadResearch();
    if (btn.dataset.tab === "replay") loadReplayHistory();
  })
);

// ---------- strategies ----------
async function loadStrategies() {
  const list = await api("/api/strategies");
  let preferred = "TrendRider4h";
  document.querySelectorAll(".strategy-select").forEach((sel) => {
    sel.innerHTML = list.map((s) => `<option ${s === preferred ? "selected" : ""}>${esc(s)}</option>`).join("");
  });
}

// =====================================================================
// Paper trading
// =====================================================================
let paperBusy = false;

function showPaperAlert(msg, kind = "") {
  const el = $("#paper-alert");
  el.hidden = !msg;
  el.className = `alert ${kind}`;
  el.textContent = msg || "";
}

async function refreshPaper() {
  let st;
  try {
    st = await api("/api/paper/status");
  } catch (e) {
    showPaperAlert(`Panel API error: ${e.message}`, "error");
    return;
  }
  if (st.freqtrade_ui) $("#frequi-link").href = st.freqtrade_ui;

  const stateEl = $("#paper-state");
  const session = st.session || st.last_session;
  if (st.managed) {
    stateEl.textContent = st.api_up ? "Running" : "Starting…";
    stateEl.className = "pill on";
  } else if (st.external) {
    stateEl.textContent = "External bot running";
    stateEl.className = "pill ext";
  } else {
    stateEl.textContent = "Stopped";
    stateEl.className = "pill off";
  }

  const rows = [["Status", stateEl.outerHTML]];
  if (st.bot) {
    rows.push(["Bot", esc(st.bot.bot_name)], ["Strategy", esc(st.bot.strategy)],
      ["Mode", st.bot.dry_run ? "Dry-run (fake money)" : "<b class='neg'>LIVE</b>"],
      ["Timeframe", esc(st.bot.timeframe)], ["Max open trades", esc(st.bot.max_open_trades)]);
  } else if (session) {
    rows.push(["Strategy", esc(session.strategy)], ["Starting balance", fmtMoney(session.balance)]);
  }
  if (session) {
    rows.push([st.managed ? "Session" : "Last session", `${esc(session.session_id)}`]);
    rows.push(["Report", `<a href="/api/report?session=${encodeURIComponent(session.session_id)}" target="_blank" rel="noopener">Performance tear sheet ↗</a>`]);
  }
  $("#paper-session").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");

  if (st.external && !st.api_up) {
    showPaperAlert(st.api_error || "A bot the panel didn't start is using the API port. Stop it (Ctrl+C in its terminal) to run a panel-managed paper session.");
  } else if (st.external) {
    showPaperAlert("Showing a bot that was started outside the panel. Start/Stop/Reset only control panel-managed sessions.");
  } else if (st.api_error && st.managed) {
    showPaperAlert(st.api_error);
  } else {
    showPaperAlert("");
  }

  $("#paper-start").disabled = paperBusy || st.managed || st.external || monitorMode;
  $("#paper-stop").disabled = paperBusy || !st.managed || monitorMode;
  $("#paper-reset").disabled = paperBusy || st.external || monitorMode;
  ["#risk-pause", "#risk-resume", "#risk-flatten"].forEach((id) => ($(id).disabled = paperBusy || !st.api_up));
  if (st.api_up) refreshRisk(); else $("#risk-meters").innerHTML = `<p class="muted small">Available while a bot is running.</p>`;
  refreshAudit();

  // KPIs
  const tiles = $("#paper-tiles");
  if (st.api_up && st.balance && st.profit) {
    const b = st.balance, p = st.profit;
    // The session's chosen balance; Freqtrade's starting_capital is net of
    // tradable_balance_ratio (99%).
    const start = st.session?.balance ?? b.starting_capital;
    const total = b.total;
    const cur = b.stake;
    tiles.innerHTML = [
      tile(`Wallet value (${cur})`, fmtMoney(total, ""), signClass(total - start), `started at ${fmtMoney(start, "")}`),
      tile(`Total P&L (${cur})`, fmtMoney(p.profit_all_coin, ""), signClass(p.profit_all_coin), fmtPct(p.profit_all_percent) + " incl. open"),
      tile(`Realised P&L (${cur})`, fmtMoney(p.profit_closed_coin, ""), signClass(p.profit_closed_coin), `${p.closed_trade_count} closed trades`),
      tile("Win rate", p.closed_trade_count ? `${(p.winrate * 100).toFixed(1)}%` : "–", "", `${p.winning_trades} W / ${p.losing_trades} L`),
      tile("Open trades", String(st.open_trades.length), "", `max ${st.bot?.max_open_trades ?? "?"}`),
      tile("Max drawdown", p.max_drawdown != null ? `${(p.max_drawdown * 100).toFixed(2)}%` : "–"),
    ].join("");
  } else {
    tiles.innerHTML = "";
  }

  table($("#paper-open"), [
    { label: "Pair", key: "pair" },
    { label: "Opened", render: (t) => esc((t.open_date || "").slice(0, 16)) },
    { label: "Stake", num: true, render: (t) => fmtMoney(t.stake_amount, "") },
    { label: "Entry", num: true, key: "open_rate" },
    { label: "Now", num: true, key: "current_rate" },
    { label: "P&L", num: true, render: (t) => fmtPct(t.profit_pct), cls: (t) => signClass(t.profit_pct) },
    { label: "P&L USDT", num: true, render: (t) => fmtMoney(t.profit_abs, ""), cls: (t) => signClass(t.profit_abs) },
  ], st.open_trades, { empty: st.api_up ? "No open positions - waiting for an entry signal." : "Bot not running." });

  const closed = (st.trades || []).filter((t) => !t.is_open).reverse();
  table($("#paper-closed"), [
    { label: "Pair", key: "pair" },
    { label: "Opened", render: (t) => esc((t.open_date || "").slice(0, 16)) },
    { label: "Closed", render: (t) => esc((t.close_date || "").slice(0, 16)) },
    { label: "Exit", key: "exit_reason" },
    { label: "P&L", num: true, render: (t) => fmtPct(t.profit_pct), cls: (t) => signClass(t.profit_pct) },
    { label: "P&L USDT", num: true, render: (t) => fmtMoney(t.profit_abs, ""), cls: (t) => signClass(t.profit_abs) },
  ], closed, { empty: "No closed trades in this session yet." });
}

async function paperAction(fn) {
  paperBusy = true;
  await refreshPaper();
  try {
    await fn();
    showPaperAlert("");
  } catch (e) {
    showPaperAlert(e.message, "error");
  } finally {
    paperBusy = false;
    setTimeout(refreshPaper, 300);
  }
}

$("#paper-form").addEventListener("submit", (e) => {
  e.preventDefault();
  paperAction(() => api("/api/paper/start", { method: "POST", body: { balance: +$("#paper-balance").value, strategy: $("#paper-strategy").value } }));
});
$("#paper-stop").addEventListener("click", () => paperAction(() => api("/api/paper/stop", { method: "POST" })));
$("#paper-reset").addEventListener("click", () => {
  const bal = +$("#paper-balance").value;
  if (!confirm(`Start a fresh paper session with ${bal} USDT? The current session's trades are kept in its own archive file.`)) return;
  paperAction(() => api("/api/paper/reset", { method: "POST", body: { balance: bal, strategy: $("#paper-strategy").value } }));
});

// =====================================================================
// Replay
// =====================================================================
let chart;

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function renderReplay(job) {
  const r = job.result;
  const m = r.metrics;
  $("#replay-result").hidden = false;
  $("#replay-status").innerHTML = `${esc(job.strategy)} · ${esc(job.timerange)} · ` +
    `<a href="/api/replay/${encodeURIComponent(job.id)}/report" target="_blank" rel="noopener">Open tear sheet ↗</a>`;
  $("#replay-tiles").innerHTML = [
    tile("Final balance (USDT)", fmtMoney(m.final_balance, ""), signClass(m.final_balance - m.starting_balance), `from ${fmtMoney(m.starting_balance, "")}`),
    tile("Strategy return", fmtPct(m.profit_pct), signClass(m.profit_pct), `CAGR ${fmtPct(m.cagr_pct)}`),
    tile("Market (buy & hold)", fmtPct(m.market_change_pct), signClass(m.market_change_pct), "same pairs, same dates"),
    tile("Max drawdown", `${m.max_drawdown_pct.toFixed(2)}%`, m.max_drawdown_pct > 20 ? "neg" : ""),
    tile("Profit factor", m.profit_factor.toFixed(2), m.profit_factor >= 1 ? "pos" : "neg", `Calmar ${m.calmar}`),
    tile("Trades", m.trades, "", `${m.winrate_pct}% winners`),
  ].join("");

  const labels = r.curve.map((p) => p.date);
  const data = {
    labels,
    datasets: [
      { label: `${m.strategy}`, data: r.curve.map((p) => p.equity), borderColor: cssVar("--chart-equity"), borderWidth: 2, pointRadius: 0, tension: 0.1 },
      { label: "Buy & hold (equal weight)", data: r.curve.map((p) => p.benchmark), borderColor: cssVar("--chart-bench"), borderWidth: 1.5, borderDash: [4, 4], pointRadius: 0, tension: 0.1 },
    ],
  };
  const textColor = cssVar("--muted");
  const gridColor = cssVar("--border");
  if (chart) chart.destroy();
  if (window.Chart) {
    chart = new Chart($("#replay-chart"), {
      type: "line",
      data,
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: textColor, boxWidth: 12 } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmtMoney(c.parsed.y)}` } },
        },
        scales: {
          x: { ticks: { color: textColor, maxTicksLimit: 8 }, grid: { display: false } },
          y: { ticks: { color: textColor }, grid: { color: gridColor } },
        },
      },
    });
  }

  const exits = Object.entries(m.exit_reasons).filter(([k]) => k !== "TOTAL").map(([k, v]) => ({ reason: k, ...v }));
  table($("#replay-exits"), [
    { label: "Exit reason", key: "reason" },
    { label: "Trades", num: true, key: "count" },
    { label: "Profit USDT", num: true, render: (x) => fmtMoney(x.profit_abs, ""), cls: (x) => signClass(x.profit_abs) },
  ], exits);

  table($("#replay-trades"), [
    { label: "Pair", key: "pair" },
    { label: "Opened", key: "open_date" },
    { label: "Closed", key: "close_date" },
    { label: "Stake", num: true, render: (t) => fmtMoney(t.stake, "") },
    { label: "Exit", key: "exit_reason" },
    { label: "P&L", num: true, render: (t) => fmtPct(t.profit_pct), cls: (t) => signClass(t.profit_pct) },
    { label: "P&L USDT", num: true, render: (t) => fmtMoney(t.profit_abs, ""), cls: (t) => signClass(t.profit_abs) },
  ], [...r.trades].reverse(), { empty: "No trades in this window." });
}

async function pollReplay(id) {
  const status = $("#replay-status");
  const started = Date.now();
  for (;;) {
    const job = await api(`/api/replay/${id}`);
    if (job.status === "done") {
      status.textContent = `Done in ${((Date.now() - started) / 1000).toFixed(0)}s.`;
      renderReplay(job);
      break;
    }
    if (job.status === "error") {
      status.innerHTML = `<span class="neg">Replay failed:</span> <code>${esc(job.error).slice(0, 600)}</code>`;
      break;
    }
    status.textContent = `Simulating… ${((Date.now() - started) / 1000).toFixed(0)}s`;
    await new Promise((r) => setTimeout(r, 1500));
  }
  $("#replay-run").disabled = false;
  loadReplayHistory();
}

$("#replay-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#replay-run").disabled = true;
  $("#replay-status").textContent = "Starting…";
  try {
    const mot = $("#replay-mot").value;
    const { id } = await api("/api/replay", {
      method: "POST",
      body: {
        strategy: $("#replay-strategy").value,
        balance: +$("#replay-balance").value,
        start: $("#replay-start").value,
        end: $("#replay-end").value,
        max_open_trades: mot ? +mot : null,
      },
    });
    pollReplay(id);
  } catch (err) {
    $("#replay-status").innerHTML = `<span class="neg">${esc(err.message)}</span>`;
    $("#replay-run").disabled = false;
  }
});

async function loadReplayHistory() {
  const rows = await api("/api/replays").catch(() => []);
  table($("#replay-history"), [
    { label: "When", render: (j) => esc((j.created || "").replace("T", " ").slice(0, 16)) },
    { label: "Strategy", key: "strategy" },
    { label: "Range", key: "timerange" },
    { label: "Balance", num: true, render: (j) => fmtMoney(j.balance, "") },
    { label: "Return", num: true, render: (j) => (j.status === "done" ? fmtPct(j.profit_pct) : esc(j.status)), cls: (j) => signClass(j.profit_pct) },
    { label: "Max DD", num: true, render: (j) => (j.max_drawdown_pct != null ? `${j.max_drawdown_pct}%` : "–") },
  ], rows, {
    empty: "No replays yet.",
    onRow: async (j) => {
      if (j.status !== "done") return;
      renderReplay(await api(`/api/replay/${j.id}`));
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
  });
}

// =====================================================================
// Research
// =====================================================================
async function loadResearch() {
  const body = $("#research-body");
  const r = await api("/api/research").catch(() => ({}));
  if (!r.strategies) {
    body.innerHTML = `<div class="card"><p class="muted">No research report yet. Run <code>.venv\\Scripts\\python.exe scripts\\research.py gates</code> from the project folder.</p></div>`;
    return;
  }
  const bm = r.benchmarks;
  let html = `<div class="card"><dl class="kv">
    <dt>Generated</dt><dd>${esc(r.generated.replace("T", " "))}</dd>
    <dt>In-sample</dt><dd>${esc(r.periods.is.join(" → "))} &nbsp;<span class="muted">buy &amp; hold ${fmtPct(bm.is.equal_weight_pct)}, BTC ${fmtPct(bm.is.btc_pct)}</span></dd>
    <dt>Out-of-sample</dt><dd>${esc(r.periods.oos.join(" → "))} &nbsp;<span class="muted">buy &amp; hold ${fmtPct(bm.oos.equal_weight_pct)}, BTC ${fmtPct(bm.oos.btc_pct)}</span></dd>
  </dl></div>`;

  const entries = Object.entries(r.strategies).sort((a, b) => b[1].oos.calmar - a[1].oos.calmar);
  for (const [name, s] of entries) {
    const passed = s.gates.filter((g) => g.pass).length;
    const badge = s.eligible_for_paper
      ? `<span class="pill on">Eligible for 4-week paper test</span>`
      : `<span class="pill ext">${passed}/${s.gates.length} gates passed</span>`;
    const maxAbs = Math.max(...s.walkforward.windows.map((w) => Math.abs(w.profit_pct)), 1);
    const bars = s.walkforward.windows.map((w) => {
      const h = Math.max(3, (Math.abs(w.profit_pct) / maxAbs) * 56);
      return `<div class="wf-bar" title="${esc(w.window)}: ${fmtPct(w.profit_pct)} (market ${fmtPct(w.market_change_pct)})" style="height:${h}px;background:${w.profit_pct >= 0 ? "var(--pos)" : "var(--neg)"}"></div>`;
    }).join("");
    html += `<div class="card strategy-card">
      <h2>${esc(name)} ${badge}</h2>
      <div class="grid-2">
        <div>
          <ul class="gate-list">${s.gates.map((g) => `<li><span class="mark ${g.pass ? "pos" : "neg"}">${g.pass ? "✓" : "✗"}</span><span>${esc(g.label)} <span class="muted">(${esc(g.value)})</span></span></li>`).join("")}
            <li><span class="mark muted">○</span><span>4+ weeks of paper trading close to replay <span class="muted">(manual)</span></span></li>
          </ul>
        </div>
        <div>
          <div class="table-wrap"><table>
            <thead><tr><th></th><th class="num">Return</th><th class="num">Max DD</th><th class="num">PF</th><th class="num">Trades</th></tr></thead>
            <tbody>
              ${[["In-sample", s.is], ["Out-of-sample", s.oos], ["OOS, +50% fees", s.stress]].map(([l, m]) =>
                `<tr><td>${l}</td><td class="num ${signClass(m.profit_pct)}">${fmtPct(m.profit_pct)}</td><td class="num">${m.max_drawdown_pct}%</td><td class="num">${m.profit_factor}</td><td class="num">${m.trades}</td></tr>`).join("")}
            </tbody>
          </table></div>
          <p class="muted small" style="margin-top:10px">Walk-forward (3-month windows): ${s.walkforward.pct_profitable}% profitable, worst ${fmtPct(s.walkforward.worst_pct)}</p>
          <div class="wf-bars">${bars}</div>
        </div>
      </div>
    </div>`;
  }
  body.innerHTML = html;
}

// =====================================================================
// Risk controls & audit log
// =====================================================================
let monitorMode = false;

function meter(label, value, limit) {
  const frac = limit > 0 ? Math.min(1, value / limit) : 0;
  const cls = frac >= 1 ? "bad" : frac >= 0.8 ? "warn" : "";
  return `<div class="meter"><div class="row"><span>${esc(label)}</span><b>${value.toFixed(2)}% / ${limit}%</b></div>
    <div class="track"><div class="fill ${cls}" style="width:${Math.max(2, frac * 100)}%"></div></div></div>`;
}

async function refreshRisk() {
  let r;
  try {
    r = await api("/api/risk");
  } catch (e) {
    $("#risk-meters").innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
    return;
  }
  const u = r.usage, l = r.limits;
  $("#risk-meters").innerHTML = [
    meter("Drawdown from peak (halt)", u.drawdown_pct, l.max_drawdown_halt_pct),
    meter("Today's loss (UTC)", u.day_loss_pct, l.daily_loss_limit_pct),
    meter("Open risk if all stops hit", u.open_risk_pct, l.max_open_risk_pct),
  ].join("") + `<p class="muted small">Max single position: ${l.max_position_pct}% of equity. Bot state: <b>${esc(r.bot_state)}</b></p>`;
  const halt = $("#risk-halt");
  halt.hidden = !r.halt?.halted;
  if (r.halt?.halted) {
    halt.className = "pill ext";
    halt.textContent = "HALTED - review, then Resume";
    halt.title = r.halt.reason || "";
  }
}

async function refreshAudit() {
  const a = await api("/api/audit?limit=30").catch(() => null);
  if (!a) return;
  const chain = $("#audit-chain");
  chain.className = `pill ${a.chain_ok ? "on" : "ext"}`;
  chain.textContent = a.chain_ok ? "chain verified" : "CHAIN BROKEN";
  chain.title = a.problem || "Every record's hash matches its contents and the record before it.";
  table($("#audit-table"), [
    { label: "Time (UTC)", render: (e) => esc(e.ts.replace("T", " ").slice(0, 19)) },
    { label: "Actor", key: "actor" },
    { label: "Event", key: "event" },
    { label: "Details", render: (e) => `<span class="muted small">${esc(e.details.message || JSON.stringify(e.details)).slice(0, 140)}</span>` },
  ], a.events, { empty: "No events yet." });
}

async function riskAction(path, body, okMsg) {
  const note = $("#risk-note");
  note.textContent = "Working…";
  try {
    await api(path, { method: "POST", body });
    note.textContent = okMsg;
  } catch (e) {
    note.innerHTML = `<span class="neg">${esc(e.message)}</span>`;
  }
  refreshPaper();
}

$("#risk-pause").addEventListener("click", () => riskAction("/api/bot/pause", undefined, "New entries paused. Open trades are still managed."));
$("#risk-resume").addEventListener("click", () => riskAction("/api/bot/resume", undefined, "Trading resumed."));
$("#risk-flatten").addEventListener("click", () => {
  const typed = prompt("KILL SWITCH\n\nThis stops new entries, market-closes EVERY open position, and stops the bot.\n\nType FLATTEN to confirm:");
  if (typed === null) return;
  riskAction("/api/bot/flatten", { confirm: typed.trim() }, "All positions closed and the bot stopped.");
});

// ---------- boot ----------
(async function init() {
  monitorMode = (await api("/api/mode").catch(() => ({}))).monitor === true;
  if (monitorMode) {
    $("#paper-form").insertAdjacentHTML("beforeend",
      `<p class="muted small" style="margin-top:10px">Monitor mode: the bot runs as a Docker service, so Start/Stop/Reset are disabled here. Risk controls still work.</p>`);
  }
  await loadStrategies().catch(() => {});
  let tab = "paper";
  try { tab = localStorage.getItem("tab") || "paper"; } catch {}
  document.querySelector(`.tab[data-tab="${tab}"]`)?.click();
  refreshPaper();
  setInterval(() => { if (!paperBusy && $("#tab-paper").classList.contains("active")) refreshPaper(); }, 5000);
})();
