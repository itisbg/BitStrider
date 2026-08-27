"""
ApexTrader orchestrator — Stage 3 refactor.

scan_and_trade() decomposed into focused private functions:
  _run_options_cycle()     — options monitor + new entries
  _run_discovery()         — all universe refresh sources
  _resolve_market_regime() — regime detection with safe fallback
  _build_scan_targets()    — universe assembly + position filtering
  _filter_eligible()       — confidence gate + long-only enforcement
  _log_skipped()           — skip diagnostics for top-10 non-qualifiers
  _execute_bear_plan()     — bear regime: 1 swap-long + N shorts with cooldown
  _execute_bull_plan()     — bull regime: top-N by confidence
  _build_short_queue()     — pre-screen shorts (tradability + cooldown)

AppContext dataclass holds all runtime singletons so they are never
instantiated at import time — importing this module no longer opens a
broker connection.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import schedule
import pytz
REPO_ROOT = Path(__file__).resolve().parent.parent

from . import config as cfg
from .utils import (
    setup_logging,
    MarketState,
    get_finnhub_trending_tickers,
    get_market_hours_interval,
    get_position_tuning_interval,
    get_vix_interval,
    get_live_holdings,
)
from .equity.strategies import Signal
from .equity.scan import get_scan_targets, scan_universe
from .equity.universe import filter_universe_by_positions
from .equity import discovery as _discovery
from .notifications import notify_scan_results, notify_eod
from .predictions import save_day_picks

# 2026-08-18, user request (daily P&L stuck at $0.00 all session despite real
# trades/losses): `from . import session as _session` bound _session to the
# PACKAGE (engine/session/__init__.py), which does `from .session import
# daily_pnl, daily_start_equity, ...` -- a one-time VALUE COPY at first
# import, frozen at whatever the submodule's globals held at that instant
# (0.0/0.0/None, before load_daily_state()/reset_daily() ever ran). Every
# later refresh_daily_pnl()/reset_daily() call still only mutates the
# SUBMODULE's own globals (that's where `global daily_pnl` resolves, since
# that's where the function is defined) -- invisible through the package's
# already-frozen copy. Reproduced: mutating engine.session.session.daily_pnl
# left engine.session.daily_pnl at 0.0. Importing the submodule object
# itself instead of the package makes every _session.X read/write live.
#
# Same bug silently disabled the daily loss-limit halt: daily_loss_limit at
# line ~558 is `-(_session.daily_start_equity * loss_pct/100) if
# _session.daily_start_equity > 0 else -999_999` -- daily_start_equity was
# always 0.0 through this alias, so the guard always fell to -999_999 and
# could never trip regardless of real drawdown. Also silently suppressed
# log_status()'s "Quarterly:" line (same `_session.quarterly_start_equity >
# 0` pattern) even though session.py's OWN check_quarterly() printed a
# correct "Quarterly P&L:" line right next to it all along, using its local
# global instead of this alias.
from .session import session as _session
from engine.broker.broker_factory import BrokerFactory
from engine.execution.enhanced import EnhancedExecutor
from engine.options.executor import OptionsExecutor
from engine.options.strategies import scan_options_universe
from engine.risk import kill_mode as _kill_mode

log = setup_logging()

import logging as _logging
_logging.getLogger("WDM").setLevel(_logging.ERROR)
_logging.getLogger("webdriver_manager").setLevel(_logging.ERROR)


# ── AppContext ────────────────────────────────────────────────────────────────
# Holds all runtime singletons. Instantiated once inside start()/run() so
# importing this module never opens a broker connection.

@dataclass
class AppContext:
    client:           object
    executor:         EnhancedExecutor
    options_executor: Optional[OptionsExecutor]
    # Per-session state
    last_market_regime:   str  = "bull"
    market_state:         Optional[MarketState] = None
    # Short-fail cooldown: {symbol: monotonic_ts_until_retry}
    # Merged here from the old module-level global so it survives restarts
    # via executor._htb_cache and is accessible to the bear plan.
    short_fail_cooldown: dict = field(default_factory=dict)


def _build_context() -> AppContext:
    """Create and wire all runtime singletons. Called once at startup."""
    client   = BrokerFactory.create_stock_client(cfg.STOCKS_BROKER)
    executor = EnhancedExecutor(client, use_bracket_orders=True)
    opts     = OptionsExecutor(client) if cfg.OPTIONS_ENABLED else None
    if opts:
        log.info(
            f"Options trading ENABLED ({int(cfg.OPTIONS_ALLOCATION_PCT)}% allocation, "
            f"{cfg.OPTIONS_DTE_MIN}-{cfg.OPTIONS_DTE_MAX} DTE)"
        )
    log.info(f"Trade mode: {cfg.TRADE_MODE} (PAPER={cfg.PAPER}, LIVE={cfg.LIVE})")
    if not cfg.LONG_ONLY_MODE:
        log.info("Shorting enabled (LONG_ONLY_MODE=False)")
    return AppContext(client=client, executor=executor, options_executor=opts)


# ── Discovery wrappers ────────────────────────────────────────────────────────
# Thin wrappers that forward config into discovery — keeps scan_and_trade lean.

def _timed(label: str, fn, *args, **kwargs) -> None:
    """Call fn(*args, **kwargs) and log its wall time under [TIMING] <label>."""
    t0 = time.monotonic()
    try:
        fn(*args, **kwargs)
    finally:
        log.info(f"[TIMING]   {label}: {time.monotonic() - t0:.1f}s")


def _run_discovery(ctx: AppContext, market_state: MarketState) -> None:
    """Fire all configured universe refresh sources (each throttled internally)."""
    _timed("trending_stocks", _discovery.scan_trending_stocks,
        use_live_trending=cfg.USE_LIVE_TRENDING,
        use_finnhub=cfg.USE_FINNHUB_DISCOVERY,
        use_sentiment_gate=cfg.USE_SENTIMENT_GATE,
        trending_max=cfg.TRENDING_MAX_RESULTS,
        trending_interval_min=cfg.TRENDING_SCAN_INTERVAL,
        trending_min_momentum=cfg.TRENDING_MIN_MOMENTUM,
        priority_1=cfg.PRIORITY_1_MOMENTUM,
    )
    _timed("tradeideas_universe", _discovery.scan_tradeideas_universe,
        enabled=cfg.USE_TRADEIDEAS_DISCOVERY,
        scan_interval_min=(
            cfg.TRADEIDEAS_SCAN_INTERVAL_MIN if market_state.is_regular_hours
            else cfg.TRADEIDEAS_SCAN_INTERVAL_MIN_AFTER_HOURS
        ),
        headless=cfg.TRADEIDEAS_HEADLESS,
        chrome_profile=cfg.TRADEIDEAS_CHROME_PROFILE,
        update_config=cfg.TRADEIDEAS_UPDATE_CONFIG_FILE,
        priority_1=cfg.PRIORITY_1_MOMENTUM,
        priority_2=cfg.PRIORITY_2_ESTABLISHED,
        browser=cfg.TRADEIDEAS_BROWSER,
        remote_debug_port=9222,
    )
    _timed("tradeideas_unusual_options", _discovery.scan_tradeideas_unusual_options,
        enabled=cfg.USE_TRADEIDEAS_UNUSUAL_OPTIONS_DISCOVERY,
        scan_interval_min=cfg.TRADEIDEAS_UNUSUAL_OPTIONS_SCAN_INTERVAL_MIN,
        headless=cfg.TRADEIDEAS_HEADLESS,
        chrome_profile=cfg.TRADEIDEAS_CHROME_PROFILE,
        update_config=cfg.TRADEIDEAS_UPDATE_CONFIG_FILE,
        priority_1=cfg.PRIORITY_1_MOMENTUM,
        priority_2=cfg.PRIORITY_2_ESTABLISHED,
        browser=cfg.TRADEIDEAS_BROWSER,
        remote_debug_port=9222,
    )
    _timed("tradeideas_toplists", _discovery.scan_tradeideas_toplists,
        enabled=cfg.USE_TRADEIDEAS_TOPLISTS_DISCOVERY,
        scan_interval_min=cfg.TRADEIDEAS_TOPLISTS_SCAN_INTERVAL_MIN,
        headless=cfg.TRADEIDEAS_HEADLESS,
        chrome_profile=cfg.TRADEIDEAS_CHROME_PROFILE,
        update_config=cfg.TRADEIDEAS_UPDATE_CONFIG_FILE,
        priority_1=cfg.PRIORITY_1_MOMENTUM,
        priority_2=cfg.PRIORITY_2_ESTABLISHED,
        browser=cfg.TRADEIDEAS_BROWSER,
        remote_debug_port=9222,
    )
    _timed("sympathy_and_edgar", _discovery.scan_sympathy_and_edgar,
        sympathy_enabled=cfg.USE_SECTOR_SYMPATHY,
        edgar_enabled=cfg.USE_EDGAR_SCANNER,
        sympathy_interval_min=cfg.SECTOR_SYMPATHY_INTERVAL_MIN,
        edgar_interval_min=cfg.EDGAR_SCANNER_INTERVAL_MIN,
        priority_1=cfg.PRIORITY_1_MOMENTUM,
        priority_2=cfg.PRIORITY_2_ESTABLISHED,
    )
    _timed("alpaca_movers", _discovery.scan_alpaca_movers,
        interval_min=cfg.ALPACA_MOVER_SCAN_INTERVAL_MIN,
        market_state=market_state,
    )
    _timed("preopen_intelligence", _discovery.scan_preopen_intelligence,
        enabled=cfg.USE_PREOPEN_INTELLIGENCE,
        interval_min=cfg.PREOPEN_INTELLIGENCE_SCAN_INTERVAL_MIN,
        market_state=market_state,
        priority_1=cfg.PRIORITY_1_MOMENTUM,
        priority_2=cfg.PRIORITY_2_ESTABLISHED,
        max_watchlist=cfg.PREOPEN_INTELLIGENCE_MAX_TICKERS,
        use_regime_gating=cfg.PREOPEN_USE_REGIME_GATING,
        use_sentiment_gating=cfg.PREOPEN_USE_SENTIMENT_GATING,
    )


# ── Options cycle ─────────────────────────────────────────────────────────────

def _run_options_cycle(ctx: AppContext, market_state: MarketState) -> None:
    """Monitor existing options positions and attempt one new entry per cycle."""
    if ctx.options_executor is None:
        return

    if not market_state.is_regular_hours:
        return

    try:
        ctx.options_executor.monitor_positions()
        if market_state.is_options_lull_hours:
            log.info("[OPTIONS] Lull period — monitoring only, no new entries")
            return
        # 2026-08-24, user request: "don't trade or do anything after 3:50pm
        # ET" -- this function's own is_regular_hours gate only stops it at
        # 16:00 ET, a 10-min window past the equities side's entry cutoff
        # (ENTRY_WINDOW_END_ET) where options could still open a brand new
        # position. Monitoring/closing stays active past the cutoff (same
        # as equities); only new entries stop.
        if not _within_entry_window(market_state.now):
            log.info(f"[OPTIONS] Outside entry window (ends {cfg.ENTRY_WINDOW_END_ET} ET) — monitoring only, no new entries")
            return
        all_positions = ctx.client.get_all_positions()
        held_map      = {p.symbol: int(float(p.qty)) for p in all_positions if float(p.qty) > 0}
        existing_syms = {pos.occ_symbol for pos in ctx.options_executor._positions.values()}
        opt_signals   = scan_options_universe(held_map, existing_syms, ctx.market_state)
        if opt_signals:
            top = opt_signals[:10]
            log.info(
                f"[OPTIONS] {len(opt_signals)} candidates | open={len(ctx.options_executor._positions)} "
                f"| top: {top[0].symbol} {top[0].option_type} conf={top[0].confidence:.0%}"
            )
            executed = False
            for sig in top:
                if ctx.options_executor.place_option_order(sig, market_state):
                    executed = True
                    break
            if not executed:
                log.info(f"[OPTIONS] No option order executed this cycle | open={len(ctx.options_executor._positions)}")
        else:
            log.info(
                f"[OPTIONS] No qualifying signals this cycle | open={len(ctx.options_executor._positions)}"
            )
        log.info(f"[OPTIONS] {ctx.options_executor.status_summary()}")
    except Exception as e:
        log.error(f"[OPTIONS] Cycle error: {e}", exc_info=True)


# ── Market regime ─────────────────────────────────────────────────────────────

def _resolve_market_regime(ctx: AppContext, market_state: MarketState) -> Tuple[str, int]:
    """Return (regime, signals_cap). Falls back to last known regime on failure."""
    if not cfg.USE_MARKET_REGIME_FILTER:
        return ctx.last_market_regime, cfg.MAX_SIGNALS_PER_CYCLE
    try:
        is_bull        = market_state.resolve_regime()
        regime         = "bull" if is_bull else "bear"
        ctx.last_market_regime = regime
        signals_cap    = cfg.MAX_SIGNALS_PER_CYCLE if is_bull else cfg.MARKET_REGIME_SIGNALS_CAP
        if is_bull:
            log.info(f"[SCAN] BULL REGIME — cap {signals_cap}/cycle")
        else:
            short_cap = 0 if (cfg.LONG_ONLY_MODE or ctx.executor.shorting_blocked) else cfg.BEAR_SHORT_SIGNALS_CAP
            log.info(f"[SCAN] BEAR REGIME — long cap {cfg.MARKET_REGIME_SIGNALS_CAP}, short cap {short_cap}/cycle")
        return regime, signals_cap
    except Exception as e:
        log.error(f"[SYSTEM] Regime check failed — retaining '{ctx.last_market_regime}': {e}", exc_info=True)
        return ctx.last_market_regime, cfg.MAX_SIGNALS_PER_CYCLE


# ── Universe assembly ─────────────────────────────────────────────────────────

def _build_scan_targets(ctx: AppContext) -> Tuple[List[str], set]:
    """Return (scan_targets, excluded) after universe assembly and position filtering."""
    _, _, excluded = get_live_holdings(ctx.client)
    # 2026-08-18, user request: a post-loss cooldown symbol is no longer kept
    # out of the scan entirely -- _create_bracket_order now routes any signal
    # for it through a trailing buy (see is_reentry) instead of the normal
    # marketable chase, so it's safe to let it be re-scanned/re-signaled.
    # _validate_trade's cooldown check was removed the same way -- see there
    # for the SOXS precedent (22 rapid re-entries, -$605) this replaces.
    targets = filter_universe_by_positions(get_scan_targets(), excluded)
    log.info(
        f"[SCAN] {len(targets)} symbols (filtered, {cfg.SCAN_WORKERS} workers): "
        f"{', '.join(targets)}"
    )
    return targets, excluded


# ── Signal filtering ──────────────────────────────────────────────────────────

def _filter_eligible(
    ctx: AppContext,
    signals: list,
    fresh_held: set,
    regime: str,
) -> list:
    """Apply confidence gate, position cross-ref, and long-only enforcement.

    Returns the eligible signal list ready for execution.
    """
    short_min_conf = cfg.MIN_SHORT_CONFIDENCE_BEAR if regime == "bear" else cfg.MIN_SIGNAL_CONFIDENCE
    long_only      = cfg.LONG_ONLY_MODE or ctx.executor.shorting_blocked

    if ctx.executor.shorting_blocked and not cfg.LONG_ONLY_MODE:
        log.warning("Shorting blocked by broker (40310000) — effective long-only this session")

    # Same-underlying guard: don't buy two leveraged siblings of the same
    # commodity/index/stock in one cycle (e.g. BOIL+KOLD, or AAPU alongside
    # held AAPL) — see leveraged_underlying() in config.py.
    picked_underlyings = {cfg.leveraged_underlying(sym) for sym in fresh_held}

    eligible = []
    for s in signals:
        if s.symbol in fresh_held:
            continue
        underlying = cfg.leveraged_underlying(s.symbol)
        if underlying in picked_underlyings:
            continue
        conf = round(float(s.confidence), 2)
        if s.action == "buy" and conf >= cfg.MIN_SIGNAL_CONFIDENCE:
            eligible.append(s)
            picked_underlyings.add(underlying)
        elif (
            s.action in ("sell", "short")
            and not long_only
            and conf >= short_min_conf
        ):
            eligible.append(s)
            picked_underlyings.add(underlying)

    # Strip shorts when effectively long-only
    if long_only:
        eligible = [s for s in eligible if s.action == "buy"]

    # Long-only fallback: if nothing qualifies, pick the best buy above min conf
    if long_only and not eligible:
        fallback = next(
            (s for s in signals
             if s.action == "buy"
             and s.symbol not in fresh_held
             and cfg.leveraged_underlying(s.symbol) not in picked_underlyings
             and round(float(s.confidence), 2) >= cfg.MIN_SIGNAL_CONFIDENCE),
            None,
        )
        if fallback:
            log.warning(
                f"Long-only fallback: {fallback.symbol} buy @ ${fallback.price:.2f} "
                f"conf={fallback.confidence:.0%}"
            )
            eligible = [fallback]

    log.info(
        f"Confidence gate (long>={cfg.MIN_SIGNAL_CONFIDENCE:.0%}, "
        f"short>={short_min_conf:.0%}) + cross-ref: {len(eligible)} eligible"
    )
    return eligible


def _log_skipped(signals: list, eligible: list, fresh_held: set, regime: str, executor: EnhancedExecutor) -> None:
    """Log skip reason for each top-10 raw signal that did not make it to eligible."""
    short_min_conf = cfg.MIN_SHORT_CONFIDENCE_BEAR if regime == "bear" else cfg.MIN_SIGNAL_CONFIDENCE
    eligible_syms  = {s.symbol for s in eligible}
    eligible_underlyings = {cfg.leveraged_underlying(sym) for sym in fresh_held} | \
                            {cfg.leveraged_underlying(s.symbol) for s in eligible}
    top10          = sorted(signals, key=lambda s: s.confidence, reverse=True)[:10]
    for s in top10:
        if s.symbol in eligible_syms:
            continue
        conf = round(float(s.confidence), 2)
        if s.symbol in fresh_held:
            reason = "already held/ordered"
        elif cfg.leveraged_underlying(s.symbol) in eligible_underlyings:
            reason = f"same underlying ({cfg.leveraged_underlying(s.symbol)}) already held/picked"
        elif s.action == "buy" and conf < cfg.MIN_SIGNAL_CONFIDENCE:
            reason = f"conf {conf:.0%} < long min {cfg.MIN_SIGNAL_CONFIDENCE:.0%}"
        elif s.action in ("sell", "short") and conf < short_min_conf:
            reason = f"conf {conf:.0%} < short min {short_min_conf:.0%}"
        elif executor.shorting_blocked and s.action in ("sell", "short"):
            reason = "shorting blocked by broker"
        elif cfg.LONG_ONLY_MODE and s.action != "buy":
            reason = "long-only mode"
        else:
            reason = "filtered"
        log.info(f"[SCAN] SKIP {s.symbol} {s.action.upper()} ${s.price:.2f} conf={s.confidence:.0%} [{s.strategy}] — {reason}")


# ── Short pre-screening ───────────────────────────────────────────────────────

def _build_short_queue(ctx: AppContext, short_candidates: list) -> list:
    """Pre-screen short candidates: remove cooldown hits and non-shortable assets.

    Returns the filtered short_queue ready for bear execution.
    """
    now_ts = time.monotonic()
    # Prune expired cooldowns
    expired = [sym for sym, ts in ctx.short_fail_cooldown.items() if ts <= now_ts]
    for sym in expired:
        ctx.short_fail_cooldown.pop(sym, None)

    queue = []
    for s in short_candidates:
        cool_until = ctx.short_fail_cooldown.get(s.symbol, 0.0)
        if cool_until > now_ts:
            log.info(f"Pre-skip {s.symbol} SHORT: cooldown {(cool_until - now_ts) / 60:.1f}m remaining")
            continue
        try:
            asset     = ctx.client.get_asset(s.symbol)
            status    = str(getattr(getattr(asset, "status", "active"), "value", getattr(asset, "status", "active"))).lower()
            tradable  = bool(getattr(asset, "tradable",  True))
            shortable = bool(getattr(asset, "shortable", True))
            if status != "active" or not tradable or not shortable:
                log.info(f"Pre-skip {s.symbol} SHORT: status={status} tradable={tradable} shortable={shortable}")
                ctx.short_fail_cooldown[s.symbol] = now_ts + cfg.SHORT_FAIL_COOLDOWN_MIN * 60
                continue
        except Exception as e:
            log.warning(f"Pre-check asset failed {s.symbol}: {e} — keeping candidate")
        queue.append(s)
    return queue


# ── Execution plans ───────────────────────────────────────────────────────────

def _execute_bear_plan(
    ctx: AppContext,
    eligible: list,
    daily_loss_limit: float,
    loss_pct: float,
) -> None:
    """Bear regime: attempt 1 swap-long then up to BEAR_SHORT_SIGNALS_CAP shorts."""
    long_sigs         = [s for s in eligible if s.action == "buy"][:cfg.MARKET_REGIME_SIGNALS_CAP]
    short_candidates  = [] if (cfg.LONG_ONLY_MODE or ctx.executor.shorting_blocked) else \
                        [s for s in eligible if s.action in ("sell", "short")]
    if cfg.LONG_ONLY_MODE and any(s.action in ("sell", "short") for s in eligible):
        log.warning(f"LONG_ONLY_MODE — dropping {len([s for s in eligible if s.action in ('sell','short')])} short(s)")
    if ctx.executor.shorting_blocked and short_candidates:
        log.warning(f"Shorting blocked — dropping {len(short_candidates)} short(s)")

    short_queue  = _build_short_queue(ctx, short_candidates)
    short_target = 0 if (cfg.LONG_ONLY_MODE or ctx.executor.shorting_blocked) else cfg.BEAR_SHORT_SIGNALS_CAP
    log.info(f"[TRADE] BEAR plan: {len(long_sigs)} long(s) swap-only, target {short_target} short(s) from {len(short_queue)} queued")

    # One swap-long per bear cycle
    for sig in long_sigs:
        _session.refresh_daily_pnl(ctx.client)
        if _session.daily_pnl <= daily_loss_limit:
            log.warning(f"Daily loss limit mid-cycle ({loss_pct:.0f}%): ${_session.daily_pnl:.2f} — halting")
            return
        log.info(f"[TRADE] EXECUTE: {sig.action.upper()} {sig.symbol} @ ${sig.price:.2f} | {sig.strategy} | {sig.reason}")
        if ctx.executor.execute(sig, swap_only=True):
            _session.trades += 1
            break
        time.sleep(1)

    # Short queue
    short_success = 0
    for sig in short_queue:
        if short_target <= 0 or short_success >= short_target:
            break
        _session.refresh_daily_pnl(ctx.client)
        if _session.daily_pnl <= daily_loss_limit:
            log.warning(f"Daily loss limit mid-cycle ({loss_pct:.0f}%): ${_session.daily_pnl:.2f} — halting")
            break
        log.info(f"[TRADE] EXECUTE: {sig.action.upper()} {sig.symbol} @ ${sig.price:.2f} | {sig.strategy} | {sig.reason}")
        if ctx.executor.execute(sig, swap_only=False):
            _session.trades += 1
            short_success += 1
            ctx.short_fail_cooldown.pop(sig.symbol, None)
        else:
            ctx.short_fail_cooldown[sig.symbol] = time.monotonic() + cfg.SHORT_FAIL_COOLDOWN_MIN * 60
            log.info(f"SHORT failed {sig.symbol} — cooldown {cfg.SHORT_FAIL_COOLDOWN_MIN}m")
        time.sleep(1)


def _execute_bull_plan(
    ctx: AppContext,
    eligible: list,
    signals_cap: int,
    regime: str,
    daily_loss_limit: float,
    loss_pct: float,
) -> None:
    """Bull (or neutral) regime: try eligible signals ranked by confidence,
    highest first, until signals_cap of them actually SUCCEED (or the list
    runs out) -- not just attempt the top signals_cap once each.

    2026-08-14, user request ("we should have seen multiple stock picks"):
    the old version sliced to the top signals_cap candidates BEFORE
    attempting anything, so if the top-ranked ones all failed for any reason
    (momentum freshness, hard-to-borrow, insufficient buying power...) the
    cycle wasted its whole budget on failures and never even looked at the
    next-ranked candidates. Confirmed live: 5 signals at 96-97% confidence,
    cap=3, the top 3 all failed and the other 2 (BRUN, LFS) were never
    tried. Same risk cap as before (still at most signals_cap new
    positions) -- this only stops giving up early on failures that were
    never going to fill anyway. Mirrors the pattern _execute_bear_plan's
    short queue already used correctly (short_success counter, not a
    pre-slice)."""
    ranked = sorted(eligible, key=lambda s: s.confidence, reverse=True)
    log.info(f"Executing up to {signals_cap} signal(s) from {len(ranked)} eligible (cap={signals_cap})")
    executed = 0
    for sig in ranked:
        if executed >= signals_cap:
            break
        swap_only = (regime == "bear") and sig.action not in ("sell", "short")
        _session.refresh_daily_pnl(ctx.client)
        if _session.daily_pnl <= daily_loss_limit:
            log.warning(f"Daily loss limit mid-cycle ({loss_pct:.0f}%): ${_session.daily_pnl:.2f} — halting")
            break
        log.info(f"EXECUTE: {sig.action.upper()} {sig.symbol} @ ${sig.price:.2f} | {sig.strategy} | {sig.reason}")
        if ctx.executor.execute(sig, swap_only=swap_only):
            _session.trades += 1
            executed += 1
        time.sleep(1)


# ── Core scan cycle ───────────────────────────────────────────────────────────

def _check_kill_mode(ctx: AppContext) -> bool:
    return _kill_mode.check(
        ctx.client, ctx.executor, ctx.options_executor,
        vix_level=cfg.KILL_MODE_VIX_LEVEL,
        spy_drop_pct=cfg.KILL_MODE_SPY_DROP_PCT,
        vix_roc_pct=cfg.KILL_MODE_VIX_ROC_PCT,
    )


def _within_entry_window(now_et: datetime.datetime) -> bool:
    """True if now_et (ET, tz-aware) falls within [ENTRY_WINDOW_START_ET,
    ENTRY_WINDOW_END_ET]. Pure string-time comparison, same pattern
    MarketState.from_now() already uses for is_market_open/is_regular_hours."""
    t = now_et.strftime("%H:%M")
    return cfg.ENTRY_WINDOW_START_ET <= t <= cfg.ENTRY_WINDOW_END_ET


def _margin_cushion_ok(equity: float, maintenance_margin: float, min_ratio: float) -> bool:
    """True if equity is still >= min_ratio x maintenance_margin (safe cushion
    against an Alpaca maintenance margin call). No margin exposure at all
    (maintenance_margin <= 0) is always safe -- nothing to protect against."""
    if maintenance_margin <= 0:
        return True
    return equity >= min_ratio * maintenance_margin


def scan_and_trade(ctx: AppContext) -> None:
    """One complete scan-and-trade cycle.

    Sequence:
      1. Session reset / daily guards
      2. Options cycle
      3. Market-hours + kill-mode gates
      4. Session P&L guards
      5. Discovery refresh
      6. Universe assembly + scan
      7. Signal filtering
      8. Execution (bear or bull plan)
    """
    _cycle_start = time.monotonic()
    _session.reset_daily(ctx.client)

    ctx.market_state = MarketState.from_now()
    ctx.market_state.resolve_regime()
    ctx.executor.update_market_state(ctx.market_state)
    # 2026-08-24, user request: _run_options_cycle no longer runs here --
    # moved to its own thread (_start_options_scan_thread). It used to be
    # step 2 of this function, BEFORE equity discovery/scan/execute below,
    # so every equity cycle wasn't free to run until a full options scan
    # (160 tickers, sequential per-symbol fetches, minutes long) finished
    # first -- confirmed live, that alone blew past a minute most cycles,
    # so equity re-entries (the actual swing-capture mechanism) never got
    # close to REGULAR_HOURS_SCAN_INTERVAL. See _start_options_scan_thread.

    market_state = ctx.market_state
    if not market_state.is_market_open:
        if not cfg.FORCE_SCAN:
            log.info("[SYSTEM] Market closed — skipping scan")
            return
        log.warning("[SYSTEM] FORCE_SCAN active — bypassing market-hours gate")

    # Kill mode has real protective side effects (emergency close on an
    # extreme-bear trigger) beyond just gating entries, so it must run
    # unconditionally here -- the entry-window check below only ever blocks
    # new entries and must not stand in front of it.
    if _check_kill_mode(ctx):
        log.info("[SYSTEM] Kill mode active — aborting cycle")
        return

    if not _within_entry_window(market_state.now):
        log.info(
            f"[SYSTEM] Outside entry window ({cfg.ENTRY_WINDOW_START_ET}-{cfg.ENTRY_WINDOW_END_ET} ET) "
            f"— skipping discovery/scan this cycle (concentration/correlation checks run on their own "
            f"schedule now, unaffected by this gate — see _concentration_check_job)"
        )
        return

    _session.refresh_daily_pnl(ctx.client)
    loss_pct          = cfg.DAILY_LOSS_LIMIT_BEAR_PCT if ctx.last_market_regime == "bear" else cfg.DAILY_LOSS_LIMIT_BULL_PCT
    daily_loss_limit  = -(_session.daily_start_equity * loss_pct / 100) if _session.daily_start_equity > 0 else -999_999

    if _session.daily_pnl <= daily_loss_limit:
        log.warning(f"[SYSTEM] Daily loss limit ({loss_pct:.0f}% {ctx.last_market_regime}): ${_session.daily_pnl:.2f} — halting")
        return
    if _session.daily_pnl >= cfg.DAILY_PROFIT_TARGET:
        log.info(f"[SYSTEM] Daily profit target reached: ${_session.daily_pnl:.2f}")
        return

    _session.check_quarterly(ctx.client, cfg.USE_QUARTERLY_TARGET, cfg.QUARTERLY_PROFIT_TARGET_PCT)

    sentiment = market_state.resolve_sentiment()
    log.info(f"[SCAN] Market sentiment: {sentiment}")

    ctx.executor.update_stale_orders()
    ctx.executor.check_tp_targets()

    acct = ctx.executor._get_account()
    min_needed = (
        cfg.SMALL_ACCOUNT_MIN_POSITION_DOLLARS if acct.equity < cfg.SMALL_ACCOUNT_EQUITY_THRESHOLD
        else cfg.MIN_POSITION_DOLLARS
    )
    if acct.buying_power < min_needed:
        log.info(
            f"[SYSTEM] Buying power ${acct.buying_power:,.0f} < minimum position ${min_needed:,.0f} "
            f"— skipping discovery/scan this cycle (existing stops/TP/concentration checks still ran above)"
        )
        return

    if cfg.MARGIN_SAFEGUARD_ENABLED and not _margin_cushion_ok(acct.equity, acct.maintenance_margin, cfg.MARGIN_CUSHION_MIN_RATIO):
        cushion_ratio = (acct.equity / acct.maintenance_margin) if acct.maintenance_margin > 0 else float("inf")
        log.warning(
            f"[SYSTEM] Margin cushion {cushion_ratio:.2f}x < {cfg.MARGIN_CUSHION_MIN_RATIO}x minimum "
            f"(equity ${acct.equity:,.0f} vs maintenance ${acct.maintenance_margin:,.0f}) "
            f"— skipping discovery/scan this cycle (existing stops/TP/concentration checks still ran above)"
        )
        return

    _t_discovery = time.monotonic()
    _run_discovery(ctx, market_state)
    log.info(f"[TIMING] discovery: {time.monotonic() - _t_discovery:.1f}s")

    scan_targets, excluded = _build_scan_targets(ctx)
    if not scan_targets:
        log.info("[SCAN] No targets after filtering — skipping scan")
        return

    ctx.executor._swap_cycle_closed.clear()
    regime, signals_cap = _resolve_market_regime(ctx, market_state)

    _t_scan = time.monotonic()
    signals, hit_counts, scan_errors = scan_universe(scan_targets, sentiment, market_state)
    _scan_elapsed = time.monotonic() - _t_scan
    log.info(f"[TIMING] scan_universe: {_scan_elapsed:.1f}s for {len(scan_targets)} symbols ({_scan_elapsed/max(len(scan_targets),1):.2f}s/symbol)")

    if cfg.LONG_ONLY_MODE:
        pre = len(signals)
        signals = [s for s in signals if s.action == "buy"]
        log.warning(f"LONG_ONLY_MODE: filtered {pre} → {len(signals)} (buy-only)")

    breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(hit_counts.items()))
    log.info(f"[SCAN] Breakdown — {breakdown or 'none'} | Errors: {scan_errors} | Total: {len(signals)}")
    if not hit_counts:
        if not market_state.is_market_open:
            log.info("[SCAN] No signals — after hours (stale daily bars, intraday gates not met)")
        else:
            log.info("[SCAN] No signals — market likely in downtrend or momentum gates not met")

    for idx, s in enumerate(sorted(signals, key=lambda s: s.confidence, reverse=True)[:cfg.TOP_N_SIGNALS], 1):
        log.info(f"[SCAN] TOP{cfg.TOP_N_SIGNALS}_RAW #{idx}: {s.symbol} {s.action.upper()} ${s.price:.2f} conf={s.confidence:.0%} [{s.strategy}] — {s.reason}")

    if not signals:
        log.info("[SCAN] No signals this cycle")
        return

    _, _, fresh_held = get_live_holdings(ctx.client)
    fresh_held = fresh_held or excluded
    log.info(f"Live holdings: {len(fresh_held)} excluded")

    eligible = _filter_eligible(ctx, signals, fresh_held, regime)
    _log_skipped(signals, eligible, fresh_held, regime, ctx.executor)

    for idx, s in enumerate(eligible[:cfg.TOP_N_SIGNALS], 1):
        log.info(f"[TRADE] TOP{cfg.TOP_N_SIGNALS}_ELIGIBLE #{idx}: {s.symbol} {s.action.upper()} ${s.price:.2f} conf={s.confidence:.0%} [{s.strategy}] — {s.reason}")

    save_day_picks(eligible[:cfg.TOP_N_SIGNALS], regime)
    notify_scan_results(eligible[:cfg.TOP_N_SIGNALS], datetime.date.today(), sentiment, regime)

    if not eligible:
        log.info("[SCAN] No eligible signals after filtering")
        return

    _t_exec = time.monotonic()
    if regime == "bear":
        _execute_bear_plan(ctx, eligible, daily_loss_limit, loss_pct)
    else:
        _execute_bull_plan(ctx, eligible, signals_cap, regime, daily_loss_limit, loss_pct)
    log.info(
        f"[TIMING] signal→order: {time.monotonic() - _t_exec:.1f}s | "
        f"total cycle: {time.monotonic() - _cycle_start:.1f}s"
    )


# ── Status + interval helpers ─────────────────────────────────────────────────

def _fetch_account_and_positions(ctx: AppContext, timeout_seconds: int = 30):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: (ctx.client.get_account(), ctx.client.get_all_positions()))
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"Account status call timed out after {timeout_seconds}s")


def log_status(ctx: AppContext) -> None:
    try:
        account, positions = _fetch_account_and_positions(ctx, timeout_seconds=20)
        log.info("=" * 70)
        log.info("STATUS")
        log.info(f"Equity:     ${float(account.equity):,.2f}")
        log.info(f"Daily P&L:  ${_session.daily_pnl:.2f}  |  Trades: {_session.trades}")
        if cfg.USE_QUARTERLY_TARGET and _session.quarterly_start_equity > 0:
            q_gain = ((float(account.equity) - _session.quarterly_start_equity) / _session.quarterly_start_equity) * 100
            log.info(f"Quarterly:  {q_gain:+.1f}% (target >= {cfg.QUARTERLY_PROFIT_TARGET_PCT:.0f}%)")
        log.info(f"Positions:  {len(positions)}")
        if positions:
            total_pnl = sum(float(p.unrealized_pl) for p in positions)
            log.info(f"Unrealized: ${total_pnl:.2f}")
            for p in positions:
                pct = float(p.unrealized_plpc) * 100
                log.info(
                    f"  {p.symbol}: {p.qty} @ ${float(p.avg_entry_price):.2f} "
                    f"| ${float(p.unrealized_pl):.2f} ({pct:+.2f}%)"
                )
        log.info("=" * 70)
    except Exception as e:
        log.error(f"Status error: {e}")


def get_adaptive_interval(ctx: AppContext) -> int:
    """Return next scan interval in minutes based on VIX, market phase, and position count."""
    if not cfg.ADAPTIVE_INTERVALS:
        return cfg.SCAN_INTERVAL_MIN

    market_state = ctx.market_state or MarketState.from_now()
    vix, vix_interval, vol = market_state.resolve_vix()
    interval     = vix_interval
    market_phase = "ALL DAY"

    if cfg.USE_MARKET_HOURS_TUNING:
        mkt_interval, market_phase = get_market_hours_interval(market_state.hour, {
            "PREMARKET_SCAN_INTERVAL":     cfg.PREMARKET_SCAN_INTERVAL,
            "REGULAR_HOURS_SCAN_INTERVAL": cfg.REGULAR_HOURS_SCAN_INTERVAL,
            "AFTERHOURS_SCAN_INTERVAL":    cfg.AFTERHOURS_SCAN_INTERVAL,
        })
        if mkt_interval is not None:
            interval = mkt_interval

    pos_status = "DISABLED"
    if cfg.USE_POSITION_TUNING:
        try:
            pos_count  = len(ctx.client.get_all_positions())
            pos_interval, pos_status = get_position_tuning_interval(pos_count, {
                "HIGH_POSITION_INTERVAL":   cfg.HIGH_POSITION_INTERVAL,
                "NORMAL_POSITION_INTERVAL": cfg.NORMAL_POSITION_INTERVAL,
                "LOW_POSITION_INTERVAL":    cfg.LOW_POSITION_INTERVAL,
            })
            if pos_interval is not None:
                interval = max(interval, pos_interval)
        except Exception as e:
            log.debug(f"Position tuning check failed: {e}")
            pos_status = "POS CHECK ERROR"

    log.info(f"VIX: {vix:.2f} ({vol}) | {market_phase} | {pos_status} | Scan: {interval} min")
    return interval


def _eod_close_job(ctx: AppContext) -> None:
    """schedule-driven wrapper for close_eod_positions, same reasoning as
    _guardrail_close_job below: was scan-cadence-gated with a narrow window
    (EOD_CLOSE_TIME through the hard 16:00 ET cutoff) that could fall
    between two cycles. 2026-08-12: retimed 15:50->15:45 (15 min before
    close, was 10) and decoupled the same way."""
    eod_summary = None
    try:
        eod_summary = ctx.executor.close_eod_positions()
    except Exception as e:
        log.error(f"close_eod_positions error: {e}", exc_info=True)

    if eod_summary:
        try:
            account   = ctx.client.get_account()
            positions = ctx.client.get_all_positions()
            notify_eod(eod_summary, account, positions, _session.daily_pnl, _session.trades, _discovery.trending_stocks)
        except Exception as e:
            log.error(f"EOD notify error: {e}", exc_info=True)


def _guardrail_close_job(ctx: AppContext) -> None:
    """schedule-driven wrapper for close_guardrail_fail_positions.

    2026-08-12: this used to run only inside the main scan-cadence block
    below, gated on the same variable interval (5-60 min depending on
    VIX/position count) as everything else there. That let the function's
    own internal 5-min close window (GUARDRAIL_EOD_CLOSE_TIME through
    16:00 ET) fall entirely between two cycles -- confirmed same-day: one
    cycle started 15:54:16 ET (too early), the next didn't start until
    ~16:01 ET (already past the hard 16:00 cutoff), so the window never
    got checked at all. Running it as its own schedule.every(1).minutes
    job decouples it from scan cadence -- schedule.run_pending() ticks
    every 5s in the main loop regardless, so a 1-min job is guaranteed to
    land inside any 5-min window. The function's own internal gating
    (time-of-day check + once-per-day done flag) still does the real work;
    this just guarantees it's actually asked every minute."""
    try:
        ctx.executor.close_guardrail_fail_positions()
    except Exception as e:
        log.error(f"close_guardrail_fail_positions error: {e}", exc_info=True)


def _price_drift_stop_job(ctx: AppContext) -> None:
    """schedule-driven wrapper for check_price_drift_stop -- runs on its own
    PRICE_DRIFT_CHECK_INTERVAL_MIN cadence (10 min, matching the TI-scrape
    cadence) rather than the variable scan-cadence block, same decoupling
    reasoning as _guardrail_close_job."""
    try:
        ctx.executor.check_price_drift_stop()
    except Exception as e:
        log.error(f"check_price_drift_stop error: {e}", exc_info=True)


def _swing_drift_stop_job(ctx: AppContext) -> None:
    """schedule-driven wrapper for check_swing_drift_stop -- wider-threshold
    sibling of _price_drift_stop_job for multi-day positions, its own
    SWING_DRIFT_STOP_CHECK_INTERVAL_MIN cadence (30 min)."""
    try:
        ctx.executor.check_swing_drift_stop()
    except Exception as e:
        log.error(f"check_swing_drift_stop error: {e}", exc_info=True)


def _concentration_check_job(ctx: AppContext) -> None:
    """schedule-driven wrapper for enforce_position_concentration/
    enforce_correlation_concentration -- 2026-08-15, user request: idea #6
    of six suggested improvements. These used to run inline inside
    scan_and_trade(), which meant they were gated behind FOUR separate
    early-returns above them (market-closed, kill-mode, entry-window,
    daily-loss-limit/profit-target) despite being risk-REDUCTION actions on
    existing positions, not new entries -- on a day the daily-loss-limit
    trips, concentration trimming stopped right when it mattered most. Own
    fixed clock-grid schedule now (CONCENTRATION_CHECK_INTERVAL_MIN), same
    decoupling reasoning as _guardrail_close_job/_price_drift_stop_job,
    runs regardless of any of those four gates.

    2026-08-17: also runs enforce_portfolio_leverage() (caps TOTAL exposure
    at MAX_PORTFOLIO_LEVERAGE x equity, independent of per-symbol/per-group
    caps) on the same schedule."""
    try:
        ctx.executor.enforce_position_concentration()
        ctx.executor.enforce_correlation_concentration()
        ctx.executor.enforce_portfolio_leverage()
    except Exception as e:
        log.error(f"concentration check error: {e}", exc_info=True)


def _schedule_on_clock_grid(interval_min: int, job, *args) -> None:
    """Register `job` to run at fixed wall-clock marks (:00, :10, :20, ... for
    interval_min=10) instead of schedule.every(N).minutes, which counts N
    minutes from whenever this line executes -- i.e. from process start.

    2026-08-14, found while investigating why FFAI's drift-stop check missed
    a brief dip-and-recover: on a day with this many restarts, each restart
    re-registered schedule.every(10).minutes fresh, so the first fire landed
    10 min after THAT restart, not on any fixed grid -- confirmed live,
    checks landed 11:06, 11:25, 11:41, 11:54 (13-19 min gaps, not a clean
    10), widening the blind spot between checks. A drift stop is a
    point-in-time poll, not a continuous high/low tracker, so wider gaps
    mean more brief moves slip through entirely. This doesn't fix that
    inherent polling gap, but it does stop restarts from making it worse --
    every restart now re-aligns to the same clock marks instead of resetting
    its own independent countdown.

    interval_min must evenly divide 60 (10, 12, 15, 20, 30 ... — 10 is what
    every caller here actually uses)."""
    assert 60 % interval_min == 0, f"{interval_min} must evenly divide 60 to land on a fixed grid"
    for minute in range(0, 60, interval_min):
        schedule.every().hour.at(f":{minute:02d}").do(job, *args)


def _prune_universe_job() -> None:
    try:
        from .equity.universe import prune as _prune
        removed = _prune()
        if removed:
            log.info(f"Universe pruned: {len(removed)} expired ticker(s): {removed[:10]}{'…' if len(removed) > 10 else ''}")
        else:
            log.info("Universe pruned: no expired tickers")
    except Exception as e:
        log.warning(f"Universe prune failed: {e}")


# ── Top3-only (dry-run) mode ──────────────────────────────────────────────────

def scan_top3_only(ctx: AppContext) -> None:
    market_state = ctx.market_state or MarketState.from_now()
    ctx.market_state = market_state
    sentiment = market_state.resolve_sentiment()
    log.info(f"Market sentiment: {sentiment}")
    _run_discovery(ctx, market_state)
    _, _, excluded = get_live_holdings(ctx.client)
    scan_targets   = get_scan_targets(excluded)
    log.info(f"Top3 mode: scanning {len(scan_targets)} symbols ({len(excluded)} pre-excluded)")
    signals, _, scan_errors = scan_universe(scan_targets, sentiment, market_state)
    log.info(f"Scan errors: {scan_errors} | Signals: {len(signals)}")
    if not signals:
        log.info("No signals found in Top3 mode")
        return
    _, _, fresh_held = get_live_holdings(ctx.client)
    fresh_held = fresh_held or excluded
    top5 = [s for s in signals if s.symbol not in fresh_held][:5]
    if not top5:
        log.info("No signals (all candidates already held)")
        return
    log.info("TOP 5 SCAN PICKS:")
    for idx, s in enumerate(top5, 1):
        log.info(f"#{idx}: {s.symbol} {s.action.upper()} ${s.price:.2f} conf={s.confidence:.0%} [{s.strategy}] — {s.reason}")
    notify_scan_results(top5, datetime.date.today(), sentiment, ctx.last_market_regime)


# ── Main loop ─────────────────────────────────────────────────────────────────

# ── Software-stop fast-poll thread ───────────────────────────────────────────
# PDT-blocked stops need frequent polling regardless of the adaptive scan
# interval (which can stretch to 20 min in calm markets).
# This thread runs independently at a fixed 10-second cadence and only
# makes a broker call when _pdt_stop_blocked is non-empty.

def _start_software_stop_thread(ctx: AppContext) -> None:
    """Spawn a daemon thread that polls _cover_naked_positions(),
    check_software_stops(), check_afterhours_stops(), _sweep_force_closes(),
    _sweep_pending_entries(), and detect_stopped_out_positions() every 10
    seconds, plus check_ema9_exit(), check_pending_entries_ema(), and
    check_blocked_entries_ema() every STAGNANT_STOP_CHECK_INTERVAL_MIN.

    2026-08-24, user request ("why do you say the cycle time increase" --
    it wasn't supposed to touch the EMA check at all): the per-minute EMA
    exit check used to run via schedule.every() in the main loop, with
    comments claiming that decouples it from scan cadence because
    "schedule.run_pending() ticks every 5s in the main loop regardless."
    That's false once scan_and_trade() itself runs long -- the main loop is
    single-threaded, so schedule.run_pending() (and every job registered on
    it) simply doesn't get called until scan_and_trade() returns. Confirmed
    live: cycles were landing 4-10 min apart despite a 1-min config, which
    silently starved the EMA exit check the same way. This thread is
    genuine concurrency (a real Thread, checked every 10s independent of
    what the main loop is doing) -- moving the EMA check here is what
    schedule.every() was supposed to give it and didn't. Still the only
    trigger for the per-minute exit check, now check_ema9_exit
    (2026-08-25, user request: the original EMA15-based check_ema15_exit
    this reasoning was built for is removed -- see check_ema9_exit's
    docstring for the current logic)."""
    import threading

    def _loop() -> None:
        last_ema15 = 0.0
        while True:
            try:
                ctx.executor._cover_naked_positions()
            except Exception as e:
                log.error(f"[STOP-THREAD] _cover_naked_positions error: {e}", exc_info=True)
            try:
                if ctx.executor._pdt_stop_blocked:
                    ctx.executor.check_software_stops()
            except Exception as e:
                log.error(f"[STOP-THREAD] check_software_stops error: {e}", exc_info=True)
            try:
                ctx.executor.check_afterhours_stops()
            except Exception as e:
                log.error(f"[STOP-THREAD] check_afterhours_stops error: {e}", exc_info=True)
            try:
                ctx.executor._sweep_force_closes()
            except Exception as e:
                log.error(f"[STOP-THREAD] _sweep_force_closes error: {e}", exc_info=True)
            try:
                ctx.executor._sweep_pending_entries()
            except Exception as e:
                log.error(f"[STOP-THREAD] _sweep_pending_entries error: {e}", exc_info=True)
            try:
                ctx.executor.detect_stopped_out_positions()
            except Exception as e:
                log.error(f"[STOP-THREAD] detect_stopped_out_positions error: {e}", exc_info=True)
            if time.time() - last_ema15 >= cfg.STAGNANT_STOP_CHECK_INTERVAL_MIN * 60:
                try:
                    # 2026-08-25, user request: "remove the ema15 delta
                    # check, only keep the ema3 and ema7 positive slope" --
                    # the EMA15-based exit check (method, helpers, config
                    # constants, self-tests) was deleted outright, not just
                    # unwired. check_ema9_exit (EMA9 delta now, was EMA7) is
                    # the only per-minute exit check now.
                    ctx.executor.check_ema9_exit()
                except Exception as e:
                    log.error(f"[STOP-THREAD] check_ema9_exit error: {e}", exc_info=True)
                try:
                    ctx.executor.check_pending_entries_ema()
                except Exception as e:
                    log.error(f"[STOP-THREAD] check_pending_entries_ema error: {e}", exc_info=True)
                try:
                    # 2026-08-25, user request: "each blocked trade should
                    # wait for next minute recheck not to completely
                    # discard the order" -- see check_blocked_entries_ema.
                    ctx.executor.check_blocked_entries_ema()
                except Exception as e:
                    log.error(f"[STOP-THREAD] check_blocked_entries_ema error: {e}", exc_info=True)
                last_ema15 = time.time()
            time.sleep(10)

    t = threading.Thread(target=_loop, name="SoftwareStopPoller", daemon=True)
    t.start()
    log.info("[STOP-THREAD] Software-stop fast-poll thread started (10s interval, EMA9 exit + pending-entry + blocked-entry EMA re-check every 1 min)")


def _start_options_scan_thread(ctx: AppContext) -> None:
    """Spawn a daemon thread that runs the options monitor + new-entry cycle
    (_run_options_cycle) on its own OPTIONS_SCAN_INTERVAL_MIN timer,
    independent of the equity scan_and_trade() loop.

    2026-08-24, user request ("every one minute there should be new order
    attempts if the conditions met"): this used to run INSIDE
    scan_and_trade(), as step 2 of 8 -- BEFORE the equity discovery/scan/
    execute steps that follow it. Every equity cycle waited on a full
    options scan (160 tickers, sequential per-symbol bar fetches) to finish
    first. Confirmed live: that alone routinely ran past a minute by itself,
    so equity re-entries never got close to REGULAR_HOURS_SCAN_INTERVAL no
    matter how low that config was set. Same fix as the per-minute EMA exit
    check (_start_software_stop_thread) and the same reason it has to be a
    real thread, not schedule.every() -- that's just as blocked whenever
    scan_and_trade() itself is running, see that function's docstring.

    Computes its own local MarketState each cycle rather than reading
    ctx.market_state (written concurrently by the main loop) -- only
    assigns it to ctx.market_state right before calling _run_options_cycle,
    since that function's scan_options_universe() call still reads it from
    there. Same level of shared-ctx looseness the SoftwareStopPoller thread
    already runs with elsewhere in this file."""
    import threading

    def _loop() -> None:
        while True:
            try:
                market_state = MarketState.from_now()
                market_state.resolve_regime()
                ctx.market_state = market_state
                _run_options_cycle(ctx, market_state)
            except Exception as e:
                log.error(f"[OPTIONS-THREAD] cycle error: {e}", exc_info=True)
            time.sleep(cfg.OPTIONS_SCAN_INTERVAL_MIN * 60)

    t = threading.Thread(target=_loop, name="OptionsScanner", daemon=True)
    t.start()
    log.info(f"[OPTIONS-THREAD] Options scan thread started ({cfg.OPTIONS_SCAN_INTERVAL_MIN} min interval)")


def start() -> None:
    ctx = _build_context()

    _session.load_quarterly_state()
    _session.load_daily_state()
    log.info("=" * 70)
    log.info("APEXTRADER - Priority-Based Momentum Trading")
    log.info("=" * 70)
    log.info(f"Priority 1 (Momentum): {len(cfg.PRIORITY_1_MOMENTUM)} stocks")
    log.info(f"Priority 2 (Established): {len(cfg.PRIORITY_2_ESTABLISHED)} stocks")
    log.info(f"Total Universe: {sum(len(v) for v in cfg.STOCKS.values())} stocks")
    log.info(f"Scan: {'ADAPTIVE (VIX-based)' if cfg.ADAPTIVE_INTERVALS else f'{cfg.SCAN_INTERVAL_MIN} min fixed'}")
    log.info("=" * 70)

    try:
        account = ctx.client.get_account()
        log.info(f"Equity:          ${float(account.equity):,.2f}")
        log.info(f"Buying Power:    ${float(account.buying_power):,.2f}")
        log.info(f"PDT Status:      {'Yes' if account.pattern_day_trader else 'No'}")
        log.info(f"Day Trade Count: {account.daytrade_count}")
    except Exception as e:
        log.error(f"Account info error: {e}")

    log.info("=" * 70)
    log.info("Starting… Press Ctrl+C to stop")
    log.info("=" * 70)

    try:
        ctx.executor.protect_positions()
    except Exception as e:
        log.error(f"protect_positions startup error: {e}", exc_info=True)

    # Start the dedicated software-stop monitor thread
    _start_software_stop_thread(ctx)
    _start_options_scan_thread(ctx)

    # Block until startup TI capture completes (up to 90s)
    if cfg.USE_TRADEIDEAS_DISCOVERY:
        try:
            log.info("Startup TI capture — refreshing universe before first scan…")
            _discovery.scan_tradeideas_universe(
                enabled=cfg.USE_TRADEIDEAS_DISCOVERY,
                scan_interval_min=cfg.TRADEIDEAS_SCAN_INTERVAL_MIN,
                headless=cfg.TRADEIDEAS_HEADLESS,
                chrome_profile=cfg.TRADEIDEAS_CHROME_PROFILE,
                update_config=cfg.TRADEIDEAS_UPDATE_CONFIG_FILE,
                priority_1=cfg.PRIORITY_1_MOMENTUM,
                priority_2=cfg.PRIORITY_2_ESTABLISHED,
                browser=cfg.TRADEIDEAS_BROWSER,
                remote_debug_port=9222,
            )
            fut = getattr(_discovery, "_ti_future", None)
            if fut is not None:
                if cfg.STARTUP_TI_CAPTURE_TIMEOUT_S > 0:
                    log.info(
                        f"Waiting up to {cfg.STARTUP_TI_CAPTURE_TIMEOUT_S}s for startup TI capture…"
                    )
                    try:
                        fut.result(timeout=cfg.STARTUP_TI_CAPTURE_TIMEOUT_S)
                    except concurrent.futures.TimeoutError:
                        log.warning("Startup TI capture timed out — proceeding with current universe")
                    except Exception as e:
                        log.warning(f"Startup TI capture failed: {e}")
                else:
                    log.info(
                        "Startup TI capture is running in background; first scan will use current universe. "
                        "Use this only if fresh TI tickers are not required at startup."
                    )
        except Exception as e:
            log.warning(f"Startup TI capture error: {e}")

    try:
        scan_and_trade(ctx)
    except Exception as e:
        log.error(f"Initial scan error: {e}", exc_info=True)

    last_vix_check    = time.time()
    current_interval  = get_adaptive_interval(ctx)
    last_scan         = time.time()
    _, last_market_phase = get_market_hours_interval(MarketState.from_now().hour, {})

    schedule.every(30).minutes.do(log_status, ctx)
    schedule.every(30).minutes.do(_prune_universe_job)
    schedule.every(1).minutes.do(_guardrail_close_job, ctx)
    schedule.every(1).minutes.do(_eod_close_job, ctx)
    _schedule_on_clock_grid(cfg.PRICE_DRIFT_CHECK_INTERVAL_MIN, _price_drift_stop_job, ctx)
    _schedule_on_clock_grid(cfg.SWING_DRIFT_STOP_CHECK_INTERVAL_MIN, _swing_drift_stop_job, ctx)
    # Per-minute EMA exit check (check_ema9_exit) runs on the
    # SoftwareStopPoller thread (see _start_software_stop_thread), not
    # registered here.
    _schedule_on_clock_grid(cfg.CONCENTRATION_CHECK_INTERVAL_MIN, _concentration_check_job, ctx)

    try:
        while True:
            try:
                # Refresh interval every 15 min, OR immediately on a market-phase
                # transition (PRE-MARKET/REGULAR HOURS/AFTER-HOURS/OFF-HOURS).
                # The 15-min timer alone let a stale premarket interval (10 min)
                # ride up to 15 min past the 9:30 ET open before recomputing --
                # confirmed 2026-08-26: one scan at 9:27:44 ET, next not until
                # 9:38:12 ET, an ~8 min dead zone spanning the open. This phase
                # check is a local hour lookup (get_market_hours_interval), no
                # API call, so it's cheap to run every loop tick.
                _, market_phase_now = get_market_hours_interval(MarketState.from_now().hour, {})
                phase_changed = cfg.USE_MARKET_HOURS_TUNING and market_phase_now != last_market_phase
                if cfg.ADAPTIVE_INTERVALS and (phase_changed or (time.time() - last_vix_check) >= 900):
                    new_interval = get_adaptive_interval(ctx)
                    if new_interval != current_interval:
                        log.info(f"Scan interval: {current_interval} → {new_interval} min"
                                 + (f" (phase: {last_market_phase} → {market_phase_now})" if phase_changed else ""))
                        current_interval = new_interval
                    last_vix_check = time.time()
                last_market_phase = market_phase_now

                if (time.time() - last_scan) >= (current_interval * 60):
                    try:
                        ctx.executor.protect_positions()
                    except Exception as e:
                        log.error(f"protect_positions error: {e}", exc_info=True)
                    # check_software_stops() runs in its dedicated 10s thread — not here

                    try:
                        ctx.executor.ratchet_confident_winners()
                    except Exception as e:
                        log.error(f"ratchet_confident_winners error: {e}", exc_info=True)

                    try:
                        ctx.executor.close_stale_swing_positions()
                    except Exception as e:
                        log.error(f"close_stale_swing_positions error: {e}", exc_info=True)

                    try:
                        ctx.executor.close_no_gain_positions()
                    except Exception as e:
                        log.error(f"close_no_gain_positions error: {e}", exc_info=True)

                    try:
                        scan_and_trade(ctx)
                    except Exception as e:
                        log.error(f"Scan cycle error: {e}", exc_info=True)

                    last_scan = time.time()
                    log.info(f"Heartbeat: {datetime.datetime.now().isoformat()}")
                    try:
                        # Plain-text, UTC ISO timestamp — read by engine/watchdog.py's
                        # stall monitor, which runs on the supervising process (no
                        # heavy deps available there, so it can't just tail this log).
                        (REPO_ROOT / "heartbeat.txt").write_text(
                            datetime.datetime.now(datetime.timezone.utc).isoformat(), encoding="utf-8"
                        )
                    except Exception as e:
                        log.warning(f"heartbeat.txt write failed: {e}")

                schedule.run_pending()
                time.sleep(5)

            except KeyboardInterrupt:
                log.info("Stopped by user")
                log_status(ctx)
                break
            except Exception as e:
                log.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(10)

    except KeyboardInterrupt:
        log.info("Stopped by user")
        log_status(ctx)


# ── Public entry point ────────────────────────────────────────────────────────

def run(*, force: bool = False, once: bool = False, top3_only: bool = False) -> None:
    if force:
        cfg.FORCE_SCAN = True

    if top3_only:
        ctx = _build_context()
        log.info("APEXTRADER — Top3 scan mode")
        scan_top3_only(ctx)
        log_status(ctx)
        return

    if once:
        ctx = _build_context()
        log.info("=" * 70)
        log.info("APEXTRADER — Single Scan Cycle")
        log.info("=" * 70)
        scan_and_trade(ctx)
        log_status(ctx)
        return

    start()
