# AGENT_CHECKPOINT — coding-agent resume point (keep updated)

> **Purpose:** the 2026-09-02 16:50 ET session was killed mid-run by an
> upstream stream timeout (`BodyTimeoutError: UND_ERR_BODY_TIMEOUT` — Node/undici
> inside the coding CLI, NOT this repo's code; bot was unaffected). To make any
> future kill lossless: **the coding agent updates this file after every verified
> milestone** (edits made, tests run + results, deploy state, what's next), and a
> replacement session reads it first and finishes the remaining work without
> re-asking questions. Update BEFORE starting a risky step, and again after it.

---
## Snapshot — 2026-09-03 ~14:05 ET (MFE give-back stop implemented + deploy)

### What
- New gain-retention exit: `check_mfe_giveback_exit()` in `engine/execution/enhanced.py`, wired into the SoftwareStopPoller `_tick()` in `engine/orchestrator.py`. Config: `MFE_GIVEBACK_ENABLED/ARM_PROFIT_PCT/GIVEBACK_FRACTION/BREAKEVEN_FLOOR_PCT` in `engine/config.py`.
- Rule: once a same-day position's peak unrealized gain reaches +0.5%, exit when current gain falls below max(60% of peak, entry+0.1%) — breakeven-plus ratchet. Pure decision fn `_mfe_giveback_reason()` is unit-testable; short-mirrored; same-day scope; GTC re-arm fallback on close failure.
- Motivation: 9/3 morning post-mortem — 41 trips peaked +$90.56 unrealized, realized +$1.22 (1.3% MFE capture). Analysis tooling: `%TEMP%\apex_peak_hold_analysis.py` (read-only Alpaca).
- Also fixed 2 time-dependent tests (test_pending_entry_ema_recheck, test_staged_allocation) that failed whenever run during the 11:00-14:45 lunch window — added the module's own `in_lunch_break = lambda *_: False` shim. This was silently blocking midday deploys.

### Verification
- New `scripts/test_mfe_giveback.py` green (uses the real 9/3 CONL/SMMT/HOOD/ASST/PLTR cases).
- Full suite: ALL 49 runnable tests exit 0 (test_notifications still skipped by hand, runs in deploy gate).

---

## Snapshot — 2026-09-03 ~12:00 UTC (cleanup pass)

- Removed `engine/equity/scan.py.bak` (stale backup, was never imported; scan smoke + morning readiness re-passed after removal).
- Earlier same day: removed stray junk file `t-Path ..claude) {...}` at repo root.
- Working tree categorized for commit (see session log): source changes vs runtime-generated noise. NO commits made by agent.

---

## Snapshot — 2026-09-03 ~11:30 UTC (readiness validation pass, no code changes)

### Goal
"Check everything is good to go on the code" — full read-only validation.

### Result — ALL GREEN
- Interpreter: managed venv `%LOCALAPPDATA%\ApexTrader\venv\Scripts\python.exe` (Python 3.12.9). Core imports (alpaca, pandas, dotenv, requests, psutil, tenacity, pytz) OK via `scripts/test_scan_smoke.py` import chain.
- `python -m compileall`: no syntax errors in project code.
- Test suite: **all 48 runnable `scripts/test_*.py` exit 0** (skipped `test_notifications.py` — sends a real email). Verified with direct exit codes, not just job state.
- Live bot healthy: main PID 34084, watchdog PID 17484, heartbeat fresh.
- Cleanup: removed stray junk file `t-Path ..claude) {...}` at repo root (accidentally created by a malformed pasted PowerShell command).

### Notes / non-blockers
- pytest is NOT installed in either venv — tests here are script-style (`python scripts/test_x.py`), which is the convention; install pytest only if pytest-style tests are wanted.
- Working tree still has large uncommitted diff (62 files, +861/−7574 incl. options-module deletion — matches "options removed 2026-09-01"); commit when convenient.
- `engine/equity/scan.py.bak` leftover backup file — since removed (see 12:00 UTC snapshot above).
- LF→CRLF warnings on `git diff` are cosmetic (core.autocrlf).

### What's next
Nothing pending from this pass.

---


## Persistent codebase context — graphify knowledge graph (2026-09-03)

A graphify knowledge graph of the entire codebase lives at `graphify-out/`
(built from commit `4cc9faac`; 110 code files -> 1124 nodes, 2209 edges,
91 communities). Use it FIRST for architecture/codebase questions instead of
grepping — it survives LLM restarts because it is on disk.

- `graphify-out/GRAPH_REPORT.md` — plain-language audit report (god nodes,
  community hubs, surprising connections). Read for broad orientation.
- `graphify-out/graph.html` — interactive graph; open in a browser.
- CLI (venv: `.\apextrader\Scripts\graphify.exe`):
  - `graphify query <symbol>` — BFS neighborhood of a symbol/file
    (e.g. `query EnhancedExecutor`). Works with node names only.
  - `graphify path 'main.py' 'scan_and_trade()'` — shortest path; function
    labels need trailing `()`, files just the filename.
  - `graphify god-nodes --top 12` — architectural hubs
    (top: EnhancedExecutor, Signal, get_bars(), MarketState, AutoBotWatchdog).
  - `graphify update .` — refresh after code edits (AST-only, no API cost).
  - `graphify cluster-only .` — full recluster + report after big refactors.
  - NOTE: `graphify explain` is broken in installed graphify 0.9.32
    (ValueError in `_find_node_tiers`) — use `query` instead.
- Scoping: `.graphifyignore` excludes `apextrader/` (venv), `data/`,
  `predictions/`, logs, `.env`, caches — code + docs only.
- After changing code, run `graphify update .` so the graph stays current.

---


## Snapshot — 2026-09-02 ~22:40 ET (session complete, nothing pending)

### Goal
"Auto implement everything" — close out the remaining open recommendations from
the 09:25-morning-readiness work: (1) the .env-mtime restart storm vector, (2)
the `ti_primary.json is empty` log spam, (3) stage session work, (4) live-roll
both fixes.

### State — DONE, DEPLOYED LIVE, VERIFIED
- `engine/watchdog.py`: `.env` restart trigger now compares **content hash**
  (new `AutoBotWatchdog._env_hash()` staticmethod, sha256 of raw bytes, None
  when missing) instead of mtime. OneDrive sync touches `.env` mtime without
  changing content -> NO MORE main.py restart storm (this was the 2026-09-02
  morning 10-restart root cause; top open risk now CLOSED). Log line is now
  "Detected .env content change, restarting main.py".
- `engine/equity/scan.py` `get_scan_targets()`: universe-health notices
  (empty/too-small) rate-limited to once per 5 min via module-level
  `_UNIVERSE_HEALTH_LAST_LOG` (monotonic); limiter RE-ARMS when the universe is
  healthy again so a fresh outage logs immediately. "Static universe lists are
  empty" still logs every occurrence (true zero-coverage is critical). Was
  spamming every 5s overnight (10k+ lines/day, TTL 125-min expiry).
- Tests: `scripts/test_guardian_and_deploy.py` extended to 61 checks (new
  `test_watchdog_env_hash_gating`: missing->None, 64-hex, stable, **mtime-only
  touch does NOT change hash** regression proof, content change -> new hash,
  run-loop wiring uses `_env_hash()` not st_mtime). NEW
  `scripts/test_universe_health_ratelimit.py` (5 checks: logs once, suppressed
  second call, healthy re-arms, fresh episode logs, too-small=warning).
- **Full battery: 49 suites, 0 failures, run twice.** compileall clean.
- **Deployed live 22:28 ET** via controlled watchdog restart (needed because
  watchdog code changes only take effect when the watchdog itself restarts):
  `schtasks /End` + kill old tree (28440 watchdog, 27376 main.py, 4760 worker)
  + `schtasks /Run /TN ApexTraderAutoRun`. New watchdog pid 31496; main.py
  relaunched on new code; heartbeat advancing (22:32:33); **live proof: exactly
  1 empty-universe error since boot vs one every 5s before.**
- Staged (git): engine/watchdog.py, engine/equity/scan.py (M);
  scripts/test_guardian_and_deploy.py, scripts/test_universe_health_ratelimit.py (A).
  AGENT_CHECKPOINT.md left untracked by design.

### 2026-09-03 ~05:40 — COMMITTED & PUSHED to github.com/itisbg/BitStrider
- Branch `fix/ti-scraper-devtools-and-gtc-order-bug` pushed; session commit
  "Morning-readiness pipeline, watchdog hardening, and test battery"
  (local 4cc9faa / remote faca8f3 = WIP snapshot of the working tree on top).
- Push initially REJECTED: historical commit e5fee9c contained
  autobot_scheduler.log.broken_20260806 (110.77 MB > GitHub 100 MB limit).
  Purged via filter-branch in a temp clone (OneDrive .git hung the in-place
  rewrite), force-pushed. Remote tip f38cbd9 was verified an ancestor of local
  work first -> nothing lost. Local repo realigned to the purged history
  (trees verified identical before ref move). Old 110MB blob still exists in
  local .git objects (harmless; GC eventually). NEVER re-add
  autobot_scheduler.log.broken_20260806 (and *.log files generally) to git.
- `git stash@{0}` ("pre-purge-backup") kept as a full working-tree snapshot;
  can be dropped once comfortable: git stash drop.
- Uncommitted user refactor (options/etrade/ti-capture module removals etc.)
  intentionally NOT committed — only session work was pushed.

### Still open (user decisions / future work)
- **FORCE_SCAN is active in .env** (log: "[SYSTEM] FORCE_SCAN active -- bypassing
  market-hours gate" on every boot incl. 22:28). If unintentional, remove it
  from .env — NOTE: with the hash-gated watchdog, editing .env CONTENT now
  correctly triggers ONE restart (desired behavior, no storm).
- venv is OneDrive-fragile (pyvenv.cfg clobber) — watchdog already self-heals
  via `_ensure_virtualenv()` each cycle.
- Commit the staged work when convenient (OneDrive has reverted files before).

---

## Snapshot — 2026-09-02 ~17:00 ET (session complete, nothing pending)

### Goal
Make all polling loops ready by 09:25 ET (user: "check all the polling loops start
at 9.25AM ET to avoid delays") — fix the 09:35:43 first-order delay caused by the
morning restart storm + ActiveListRefresher spacing + clock-grid blind spots.

### State — DONE, DEPLOYED, VERIFIED (no remaining work on this goal)
- `engine/config.py`: added `MORNING_READINESS_ET = "09:25"` + import-time assert
  `PREP(09:05) < ENTRY(09:14) < READINESS(09:25) < MARKET_OPEN(09:30)`.
- `engine/orchestrator.py`:
  - module-level `_readiness_kick = threading.Event()` (+ `import threading`);
  - ActiveListRefresher waits on `_readiness_kick` (with timeout) instead of a bare
    sleep — kick forces immediate ti_capture + Alpaca movers + `prewarm_entry_ema`;
  - main-loop once-per-day 09:25 ET trigger (`readiness_due`, state var
    `readiness_scan_date`): forces fresh scan + sets the kick; fires immediately on
    a late boot (covers the 09:29:46-restart case); scoped to the morning segment
    only (afternoon has its own 14:45 reopen);
  - `_schedule_on_clock_grid` fires each job once immediately at registration
    (pre-grid warm-up) so drift/concentration checks are never blind across the open.
- `scripts/test_morning_readiness.py`: NEW regression net (10 checks).
- **Tests all green:** `py_compile` both files; `test_morning_readiness.py` 10/10;
  `test_scan_smoke.py`; `test_entry_window.py`; `test_lunch_flat.py`;
  `python -m engine.orchestrator` self-test (its 16:39:49 ERROR lines in
  apextrader.log are SIMULATED demo failures — not real).
- **Deployed live:** `deploy_requested.flag` consumed by watchdog 16:40:31 ET,
  main.py relaunched 16:40:36 ET on new code; boot log shows the new
  "[SCHEDULE] ... first tick fired at registration" lines; heartbeats flowing.

### Verified non-issues (do not re-investigate)
- `BodyTimeoutError (UND_ERR_BODY_TIMEOUT)`: Node/undici timeout inside the **Cline
  VS Code extension's** API request to the LLM provider (confirmed in VS Code logs:
  `%APPDATA%\Code\logs\<session>\window...` `1-Cline.log` — "send() completed:
  terminated: BodyTimeoutError ... inputTokens=330489" at 21:17:10 UTC / 17:17 ET,
  2026-09-02). The streamed response stalled longer than undici's body gap timeout,
  so the request was aborted and Cline paused. Network/tooling event — NOT this
  repo's code; bot unaffected (0 hits in ApexTrader logs). Mitigation: keep
  per-task context small (330K-token request = high stall exposure), start fresh
  tasks when context balloons, resume from AGENT_CHECKPOINT.md after any kill.
- "SoftwareStopPoller has not ticked in 200s" / "network down" / "_TickExecutor has
  no attribute" log lines at 16:39:49: orchestrator `_demo()` self-test output
  sharing the same log file. Harmless.
- `[UNIVERSE HEALTH] ti_primary.json is empty!` spam after ~17:53 ET: PRE-EXISTING
  designed fail-open, not from the 09:25 changes. `data/ti_primary.json` is written
  by the Yahoo-universe producer; `TI_PRIMARY_TTL_MINUTES = 125`
  (engine/equity/universe.py:44) — 125 min after the last capture (15:48 ET)
  `_get_ti_primary()` returns [] and every scan cycle logs this + falls back to the
  static universe. Yesterday's log had 10,477 of these (today: 216). Bot is flat
  after hours so nothing trades on the fallback. Optional cleanup: demote to
  DEBUG/warning when outside the discovery window, and/or extend the overnight
  capture cadence so the TTL doesn't expire every evening.

### Rigorous re-verification — 2026-09-02 ~18:00 ET (all green)
- `python -m compileall engine`: OK (whole package).
- ALL 45 `scripts/test_*.py` suites: PASS (44 pass + `test_clock_grid_schedule.py`
  was updated 2026-09-02 to pin the new contract: registration fires the job
  exactly ONCE immediately (pre-grid warm-up) then registers the :00/:10/... grid;
  args forwarded on both the immediate and grid fires; grid shape + loud interval
  validation unchanged). Re-run of it and test_morning_readiness.py: green.
- Code review of every edited region (imports, _schedule_on_clock_grid,
  _readiness_kick + refresher wait, main-loop readiness block, config asserts):
  intact and correct.
- Live process: main.py PID 6612 started 16:40:36 ET (matches watchdog relaunch);
  disk mtimes of engine/orchestrator.py + config.py predate start => running code
  == disk code. Watchdog PID 28440 up since 15:02. Deploy flag consumed (none
  pending). Heartbeats current, zero real ERRORs from the live process post-boot.

### Red-team pass — 2026-09-02 ~18:20 ET (2 hardenings applied, deployed #2)
- NEW `scripts/test_readiness_redteam.py` (39 checks, all green): trigger
  boundary matrix (09:24:59/09:25:00/10:59:59/11:00:00/afternoon, late boot,
  once-per-day latch, next-day re-arm), weekend boots, kick Event races
  (set-before-wait / set-during-work / past-deadline clamp / concurrent set),
  grid job raising at registration (grid survives), malformed config times,
  ordering-guard effectiveness, discovery-window at the kick moment.
- HARDENING 1 (orchestrator readiness_due): weekday gate `now_et.weekday() < 5`
  — a Saturday/Sunday 09:25 boot no longer forces scans/kicks (red-team found
  NO weekday gating anywhere in discovery/orchestrator).
- HARDENING 2 (config.py): `_require_hhmm()` import-time validation of all 13
  "HH:MM" constants. Red-team caught that strptime ALONE accepts "9:5", which
  breaks `_within_discovery_window`'s RAW-STRING comparisons ("9:5" > "10:00"
  lexicographically) — so the validator enforces strict zero-padded `\d{2}:\d{2}`
  + range validity. A malformed constant now fails loudly at import instead of
  error-spamming the live loop into a watchdog stall-restart storm.
- Full regression after hardening: 47/47 suites PASS (incl. the two new ones).
- DEPLOY #2: flag consumed 17:23:51 ET, main.py PID 17296 relaunched 17:23:56 ET
  on the hardened code; heartbeat at boot; ti_primary TTL message still present
  (documented pre-existing non-issue above).

### Deep dive — 2026-09-02 ~18:50 ET (timeline simulation, 3rd hardening, deployed #3)
- NEW `scripts/test_morning_timeline_sim.py`: deterministic 1s-step discrete-event
  simulation of the whole morning (main loop 5s tick + blocking ~100s scans,
  ActiveListRefresher cadence + kick semantics incl. kick-while-busy, clock-grid
  registration fires, watchdog restarts resetting everything). 8 scenarios, 20
  checks, all green: normal day, TODAY'S restart storm (9 kills 08:52-09:34),
  late boot 09:29:46, boot 09:21 (grid-blind window), Saturday boot, mid-morning
  boot, after-hours boot, exact-09:25:00 boundary boot.
- FINDING 1 (design confirmed): readiness re-fires once PER BOOT inside
  [09:25,11:00) — correct self-healing, since every restart wipes the in-memory
  state; each continuous run fires at most once. In the storm sim, EVERY run
  overlapping the window re-armed scan+prewarm immediately and a prewarm still
  started in [09:25, 09:30) despite 9 restarts.
- FINDING 2 (real bug fixed): the main loop's scan-trigger block had NO weekday
  gate and the discovery-window check is time-only — a Saturday 09:14/adaptive
  trigger would run a full scan against closed markets. Fixed: the whole trigger
  block is now weekday-gated (protective + poller schedule jobs unaffected —
  they self-gate). Deployed: flag consumed 17:48:15 ET, main.py PID 26672
  relaunched 17:48:20 ET on the final code.
- Full regression after deep-dive: 47/47 suites PASS, compileall OK.
- DST analysis: trigger/window comparisons use America/New_York local time via
  now() (pytz); the 09:25 band never intersects the 02:00 DST transitions
  (2026: Mar 8 started EDT, Nov 1 ends), so no ambiguity possible.
- Watchdog interplay: heartbeat.txt was written after every scan cycle; scans
  run ~100s vs STALL_RESTART_SECONDS=900 (9x margin); stall restarts are also
  flat-window-gated like deploys.
  **2026-09-02 ~22:00 CORRECTION (this analysis was WRONG):** off-hours the
  adaptive interval stretches to 20 min (SCAN_INTERVAL_CALM_VOL) > the 900s
  stall threshold, so a healthy sleeping bot was killed as "hung" every ~15
  min -- live-observed 19:23-21:30 ET as 7+ consecutive stall restarts. FIX
  (deploy #5, 21:54:37 ET): heartbeat is now MAIN-LOOP LIVENESS -- the loop
  touches it every 5s tick via _touch_heartbeat() (60s rate limit; force=True
  after each cycle). Regression test: scripts/test_heartbeat_liveness.py
  (10 checks). Verified live: heartbeat advances with zero scan cycles.

### Targeted hardening pass #2 — 2026-09-02 (evening): DONE, deployed

1. **Guardian halt dedupe now date-scoped** (orchestrator.py): `guardian_halt_acted` bool
   → `guardian_halt_acted_date: Optional[date]`. New testable helper `_maybe_guardian_halt(ctx)`
   (called first on every `_tick`); dedupes on the flag payload's own date, unparsable date
   falls back to today ET. Fixes: a process-lifetime bool blocked the NEXT day's guardian
   flatten (watchdog keeps main.py alive across midnight).
2. **Watchdog `.env` kill switch is real** (watchdog.py): module const `DEPLOY_RESTART_ENABLED=True`
   removed; new `_deploy_restart_enabled()` reads os.environ + `.env` live (`.env` wins).
   Semantics: missing/blank/invalid → enabled (one-time warning on invalid); only explicit
   0/false/no/off disables. Gates `_deploy_restart_requested`.
3. **Staged tranches no longer blocked by first-entry state** (enhanced.py): `_submit_entry_order`
   gained `scale_in=True` — bypasses ONLY the 60s `_recent_entry_submits` debounce + `order_cache`
   slot; `_entry_pending`/`_pending_entry_signals`/broker active-order checks stay enforced.
   `maybe_add_staged_tranches` now submits with `scale_in=True`. Decision: Option B (scale in
   promptly after first fill) — the staged path already proves open position + gain + fresh EMA;
   a resting unfilled first tranche still blocks via the broker check.
Tests: test_guardian_and_deploy.py 55/55 (+22 new: 4b halt dedupe, 5b kill switch incl. full
gate flow), test_staged_allocation.py all pass (scale-in add + pending/broker/fresh-entry
guards), test_morning_readiness.py pass, compileall clean, git diff --check clean (pre-existing
CRLF warnings only). Deploy #4 via flag → verify "[DEPLOY] deploy flag consumed" in autobot.log.

### Open recommendations (next candidates, not started)
1. Watchdog `.env`-mtime restart is still armed with `.env` in the OneDrive repo —
   the root cause of the 2026-09-02 morning restart storm (10 kills 08:48–09:35 ET).
   Recommend: restart on content-hash change only, or move `.env` machine-local
   (STATE_DIR), or defer `.env` restarts during 09:05–09:35 ET.
2. Prep-scan state (`_prep_scan_date`) is in-memory only — a restart re-runs prep.
   Consider persisting to STATE_DIR (like `.quarterly_state.json`).
3. urllib3 pool size (20) < concurrent demand — consider raising above worker count.
4. Old 375MB `autobot_scheduler.log` still in the OneDrive repo — archive/delete;
   live logs now go to `%LOCALAPPDATA%\ApexTrader\logs\` (machine timestamps = ET−1).

### Environment quick facts
- Venv: `$env:LOCALAPPDATA\ApexTrader\venv\Scripts\python.exe`
- Live logs: `%LOCALAPPDATA%\ApexTrader\logs\apextrader.log` (bot) +
  `autobot.log` (watchdog); repo-root `*.log` files are LEGACY.
- Deploy: write reason text into `%LOCALAPPDATA%\ApexTrader\state\deploy_requested.flag`
  — watchdog consumes it and restarts main.py during flat windows (11:00–14:45,
  after 15:50 ET). Never edit `.env` to deploy.
- Tests: `scripts\test_*.py`, run with the venv python from repo root.
