# Operations runbook

How to run ridgeline 24/7, watch it, and act when something goes wrong.

## Contents
- [Risk limits](#risk-limits)
- [Kill switch: pause vs flatten](#kill-switch-pause-vs-flatten)
- [Audit trail](#audit-trail)
- [Alerts (Telegram)](#alerts-telegram)
- [Deploying on a VPS (Docker)](#deploying-on-a-vps-docker)
- [Incident checklist](#incident-checklist)
- [Monthly routine](#monthly-routine)
- [Reports](#reports)

---

## Risk limits

Every strategy inherits `RiskMixin` (`ridgeline/freqtrade_risk.py`), which
enforces these limits on top of each strategy's own stops and sizing:

| Limit | Default | When it's hit |
|---|---|---|
| Max single position | 35% of equity | The stake is reduced to the cap |
| Max open risk | 4.5% of equity | New stakes shrink to fit; at the limit, no new entries. This is the loss if every open trade hit its stop now. |
| Daily loss | 3% (from UTC midnight) | No new entries until the next UTC day |
| Drawdown halt | 15% from peak | New entries **halted until you press Resume**. Drawdown is then measured from equity at resumption. |

To override the defaults, add this to `user_data/config.json`:

```json
"ridgeline_risk": { "daily_loss_limit_pct": 2, "max_drawdown_halt_pct": 10 }
```

Blocked entries, capped stakes and halts are logged, written to the audit
trail and sent to Telegram (live and paper only). Backtests apply the same
limits, so research results already include them. A backtest models the
halt-then-review step as an automatic resume after `halt_review_days`
(7 by default).

## Kill switch: pause vs flatten

The controls are in the panel's **Paper trading → Risk controls** card. They
work for any bot the panel can log in to, including the Docker `bot` service.

| Button | What happens | Use when |
|---|---|---|
| **Pause entries** | The bot stops opening trades. Open trades keep their stops and exits. | Something looks odd and you want time to investigate: a news event, an exchange incident, unexplained behaviour. |
| **Resume** | Trading restarts and a drawdown halt is cleared. | You've reviewed why it paused or halted. |
| **Flatten all** | Stop entries, then market-sell **every** open position, then stop the bot. Needs `FLATTEN` typed. | Something is clearly wrong and you want zero exposure *now*: a bug, a compromised key, a market crash you won't sit through. |

Flatten sells at market, so in a fast or thin market fills can be poor.
Always check the result in FreqUI or on the exchange. If a position couldn't
be closed, the panel says so, and you must close it manually.

## Audit trail

`user_data/audit/audit-YYYY-MM.jsonl` is an append-only log of every control
action and risk event:
- session start, stop and reset
- pause, resume and flatten
- blocked entries, capped stakes and halts
- watchdog alerts

Each record contains the hash of the record before it, so any edit, deletion
or reordering is detectable. The panel shows "chain verified" when the chain
is intact. To check it from the command line:

```bash
python -c "from ridgeline import audit; print(audit.verify())"
```

`(True, None)` means intact. Otherwise you get the file and line where the
chain breaks. Keep these files: back them up with the trade databases in
`user_data/paper/` and `user_data/tradesv3*.sqlite`.

## Alerts (Telegram)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Send any message to your new bot. Then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Put both in `user_data/config.private.json`:
   ```json
   "telegram": { "enabled": true, "token": "<TOKEN>", "chat_id": "<CHAT_ID>" }
   ```
4. Run `python scripts/watchdog.py --test-alert`. You should get a message.

Freqtrade then sends every entry and exit natively. The **watchdog** adds
out-of-band alerts for:
- the bot unreachable
- the bot stalled (no cycle for 10 minutes)
- the bot not running
- a halt
- any risk limit at 80% or more of its threshold

Each alert fires once, then again as "resolved" when it clears.

## Deploying on a VPS (Docker)

Any small Linux VPS (1 vCPU, 2 GB RAM) is enough.

```bash
# on the server (Ubuntu)
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
git clone https://github.com/devam2001/ridgeline.git && cd ridgeline
python3 scripts/init_private_config.py      # API secrets; then add Telegram keys
mkdir -p user_data/logs && sudo chown -R 1000:1000 user_data   # the image runs as uid 1000
docker compose up -d --build
docker compose ps                            # bot should become "healthy" within ~3 min
```

Data and the strategy's pair list (see the README's Data section) can be
downloaded inside the container:

```bash
docker compose run --rm bot freqtrade download-data --userdir user_data --timeframes 1h 4h 1d --timerange 20220601-
```

**Access.** All ports are published on `127.0.0.1` only, so nothing is
exposed to the internet. From your own computer, open a tunnel:

```bash
ssh -N -L 8090:127.0.0.1:8090 -L 8080:127.0.0.1:8080 you@your-server
```

Then the panel is at http://127.0.0.1:8090 and FreqUI at http://127.0.0.1:8080.

**Firewall.** Allow SSH only (`sudo ufw allow OpenSSH && sudo ufw enable`).

**Services** (`docker-compose.yml`):
- `bot`: trades `TrendRider4h`. Override with `RIDGELINE_STRATEGY=... docker compose up -d`. It runs in whatever mode `config.json` says: **dry-run unless you change it**.
- `panel`: runs in monitor mode. Status, risk controls, reports and replay all work. Start, Stop and Reset are disabled because Docker owns the process.
- `watchdog`: the alerts described above.

**Updating:**
```bash
git pull && docker compose up -d --build
```

## Incident checklist

1. **Contain.**
   - Pause entries if you're unsure.
   - Flatten if you're sure exposure itself is the problem.
2. **Look.**
   - The panel's audit log and risk meters
   - `docker compose logs --tail 200 bot`, or `user_data/logs/`
   - FreqUI's trade list
   - Your exchange account directly
3. **Exchange-side problems** (suspected key leak, unexpected orders): revoke
   the API key on Binance first, then investigate. The key should already be
   spot-only with withdrawals disabled.
4. **Record what happened and what you changed.** Control actions are audited
   automatically; add context in your own notes.
5. **Resume** only once the cause is understood. After a drawdown halt, write
   down why you're resuming.

## Monthly routine

```bash
python scripts/research.py universe --download --write-config   # refresh data + live pair list
python scripts/research.py gates                                 # re-test every strategy on new OOS data
python scripts/report.py --db user_data/paper/<session>.sqlite --month YYYY-MM
```

If you change the pair list or the strategy, restart the bot: `docker compose
restart bot`, or Reset in the panel.

## Reports

Two ways to get a tear sheet:
- **From the panel:**
  - Paper trading → **Performance tear sheet**
  - Replay → **Open tear sheet** after a replay
- **From the command line:**
  - `scripts/report.py --db ...` for paper or live sessions
  - `scripts/report.py --backtest ...` for backtest results

Each report is a single HTML file with no external assets. Print → Save as PDF
gives a PDF. Returns are on a realised basis, and the report says so.
