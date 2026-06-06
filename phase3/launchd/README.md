# V1-F — launchd daily autotrade triggers

## What's in this directory

| File | Purpose |
|---|---|
| `com.autotrade.v1.t7.plist.template` | 07:20 KST T7 prefetch (generates recommendations + sends own email). Cache fresh while US is closed. |
| `com.autotrade.v1.daily.plist.template` | 22:35 KST trade fire (reuses morning T7 run, runs intents + paper_submit + apply). Sends the R11B trading digest. |
| `install_v1.sh` | One-shot installer that renders BOTH templates, copies them to `~/Library/LaunchAgents/`, and loads them via `launchctl bootstrap` |
| `README.md` | This file |

## Daily flow once installed

```
07:15 KST       (Mac at idle, awake)
07:20 KST       (launchd com.autotrade.v1.t7) — T7 prefetch fires
                  Stage 1  env gate → KIS_ENV=paper only (no broker contact)
                  Stage 2  T7 generate_recommendations (subprocess)
                          • FMP cache refresh (prior-day closes stable since
                            04:00 KST = US close)
                          • signal scoring on the frozen universe
                          • recommendations.csv + run_meta.json on disk
                          • T7 own email sent (the same one the manual T7
                            button has always produced)

07:25–07:30     (operator) — inbox has the T7 recommendation email
                  Reads tonight's picks while having coffee.
                  Decides: arm tonight? skip tonight?
                  $ python -m phase3.autotrade.v1_runner arm-today --note "ok"
                    OR
                  (do nothing; the trade fire safely no-ops without a token)

22:30 KST       US market open

22:35 KST       (launchd com.autotrade.v1.daily) — trade fires
                  Stage 0  arm gate → require_armed_for_today
                  Stage 1  env gate → KIS_ENV=paper, SUBMIT/CANCEL/APPLY=true
                  Stage 1b discover today's T7 prefetch run on disk
                          • Hard-fails rc=2 (with email) if no run for today
                          • NEVER re-runs T7 — the morning run is authoritative
                  Stage 3  generate_intents (in-process, batch)
                  Stage 4  paper_submit + apply_t10 (one subprocess)
                  Stage 5  R11B EOD digest — trading-only mail
                          (T7 email was sent separately at 07:20)

22:37–22:40 KST (operator) — inbox has [Autotrade paper V1-trade] digest
```

If the operator forgets `arm-today`, the 22:35 fire exits rc=0
with a one-line "no arm token for YYYY-MM-DD" message in
`runtime/v1_launchd.out.log`. **No trades, no R11B mail, no harm.**
The T7 prefetch email still arrives every morning regardless.

## Why two fires instead of one (V1-F)

V1-E ran T7 inline with the 22:35 fire. 22:35 KST = 09:35 EDT,
which is **5 minutes after US market open**. At that moment some
prior-day closes are still settling on FMP, so a T7 invocation
minutes later (when the cache had stabilised) produced *different
recommendations*. Non-determinism in the recommendation source is
unacceptable — the same trading day must always produce the same
signals, regardless of which clock minute the operator clicked.

V1-F separates:

* **07:20 KST prefetch** — FMP has been stable for 3+ hours since
  US close at 04:00 KST (= 16:00 EDT prior day). T7 generates the
  signal once, deterministically.
* **22:35 KST trade fire** — picks up that morning's `*_daily`
  run_dir on disk and just runs the trading portion. T7 is never
  re-run, so non-determinism is structurally impossible.

If the morning T7 fire failed for any reason, the trade fire
hard-fails (rc=2) and emails the operator. Using yesterday's
recommendations is the **exact** bug V1-F exists to prevent.

## macOS permission popup — one-time, expected

On the **first fire of each agent**, macOS may show a
"python wants to make changes" / "wants to access network" popup.
This is GateKeeper validating a newly-launched binary. Click
**Allow**; the choice is recorded under
*System Settings → Privacy & Security* permanently. Subsequent
fires (and reboots) do not prompt again.

Notes:
* The plists set `ProcessType=Background` so macOS uses a less
  intrusive validation path where possible.
* If you see the popup *outside* the 07:20 / 22:35 windows, that
  is a `test-fire` from the panel — same one-time prompt rules apply.

## Safety contract

| Guarantee | Where enforced |
|---|---|
| Live keys are never used | Both plists hardcode `KIS_ENV=paper`; `check_env_gates` refuses anything else |
| One-day arm = one-day trading | Filename schema `v1_armed_<KST-YYYY-MM-DD>.json`; yesterday's token does not satisfy today's gate |
| Race-free arm | Atomic `.tmp` + `Path.replace` write; `gc_old_tokens` never deletes today |
| No retry storm | `KeepAlive=false`; non-zero exit means "operator wake up", not "launchd hammer" |
| Trade can't use stale picks | Trade fire scans `daily_runs/` for a run matching TODAY's KST date — falls through with rc=2 + email if absent |
| T7 prefetch can't submit | T7 plist does NOT set `KIS_PAPER_SUBMIT_OK` / `KIS_PAPER_CANCEL_OK` / `AUTOTRADE_T10_APPLY_OK`; T7 codepath has no broker call anyway |
| Sanity surface | `runtime/v1_launchd.*.log`, `runtime/v1_t7_launchd.*.log`, and `runtime/v1_status.json` for the panel |

## Env vars the plists set

### T7 fire (com.autotrade.v1.t7, 07:20)

| Var | Value | Why |
|---|---|---|
| `PYTHONPATH` | repo root | so `python -m phase3.autotrade.v1_runner` resolves |
| `PYTHONUNBUFFERED` | `1` | line-buffered stdout → readable logs |
| `KIS_ENV` | `paper` | hard-coded paper-only |

Notably **no** SUBMIT/CANCEL/APPLY gates — T7 never calls the broker.

### Trade fire (com.autotrade.v1.daily, 22:35)

| Var | Value | Why |
|---|---|---|
| `PYTHONPATH` | repo root | so `python -m phase3.autotrade.v1_runner` resolves |
| `PYTHONUNBUFFERED` | `1` | line-buffered stdout → readable logs |
| `KIS_ENV` | `paper` | hard-coded paper-only |
| `KIS_PAPER_SUBMIT_OK` | `true` | enables `--paper-submit` in daily_runner |
| `KIS_PAPER_CANCEL_OK` | `true` | enables cancel/reprice path |
| `AUTOTRADE_T10_APPLY_OK` | `true` | enables `--apply-t10` |
| `AUTOTRADE_V1_SUPPRESS_T7_MAIL` | `true` | (defensive; trade fire never invokes T7 anyway) |

## Env vars the runner reads from `.env` at startup

`v1_runner.main` calls `_hydrate_env_from_dotenv` before any
subcommand runs. launchd's gui session does NOT inherit
`~/.zshrc`, so we read `.env` directly:

* `FMP_API_KEY` — FMP data calls during T7
* `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`,
  `KIS_ACCOUNT_PRODUCT_CODE` — KIS adapter
* `GMAIL_APP_PASSWORD` is optional here; if absent, `smtp_mailer`
  falls back to `phase3/config.local.yaml`'s `email.gmail_app_password`.

Precedence is `os.environ` > `.env`, so a shell-exported value
always wins for debugging.

## Install / uninstall

```bash
cd phase3/launchd
./install_v1.sh install        # render BOTH templates + launchctl bootstrap each
./install_v1.sh status         # print load state for both labels
./install_v1.sh test-fire      # one-off ad-hoc fire (--no-arm --no-mail)
./install_v1.sh uninstall      # bootout + remove BOTH rendered plists
```

The installer:

* picks the Python interpreter from `$PYTHON` or auto-probes
  `python3` in standard locations
* warns if the Mac timezone is NOT `Asia/Seoul`
* warns if any of the required credentials are missing from BOTH
  the current shell AND `.env`
* refuses to ship a plist with unresolved `__PYTHON__` /
  `__REPO_ROOT__` placeholders

## Debugging

* `runtime/v1_t7_launchd.out.log` / `.err.log` — 07:20 T7 fire output
* `runtime/v1_launchd.out.log` / `.err.log` — 22:35 trade fire output
* `runtime/v1_status.json` — last fire's stage-by-stage progress
  (the control panel reads this for the "Last fire" / "In progress" row)
* `launchctl print gui/$(id -u)/com.autotrade.v1.t7` — T7 agent state
* `launchctl print gui/$(id -u)/com.autotrade.v1.daily` — trade agent state
* `python -m phase3.autotrade.v1_runner status` — at-a-glance:
  today's arm state, env gate state

## Why an arm token at all?

Unattended automation = the Mac trades whenever it's on with the
right env vars and a working network. That's exactly what we want
on a normal trading day, and exactly what we DON'T want on:

* days the operator is on vacation
* days the FMP cache is known-broken
* macro-event days (CPI, FOMC, NFP) the operator wants to skip
* days the Mac is on but the operator is mid-laptop-migration

The arm token converts "trade by default" into "trade only when
the operator said today's a yes". One file = one day = explicit
operator intent. Forgetting to arm is the safe state.

T7 prefetch (the 07:20 fire) is NOT gated by the arm token — the
recommendation email is informational; the operator decides
whether to arm AFTER reading it.
