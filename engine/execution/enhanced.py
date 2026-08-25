"""
ApexTrader - Enhanced Executor
Optimized trade executor with consolidated logic:
  - Reduced API calls through caching
  - Unified buy/short entry paths
  - Bracket orders with tiered SL/TP
  - PDT compliance
"""

import logging
import datetime
import re
import time
from collections import deque
from types import SimpleNamespace
from typing import Optional, Dict, Tuple, Deque
from dataclasses import dataclass, field
from enum import Enum

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
    ReplaceOrderRequest,
    TrailingStopOrderRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.trading.enums import OrderType as AlpacaOrderType

from engine.config import (
    PDT_ACCOUNT_MIN, PDT_MAX_TRADES, MIN_EQUITY_FOR_SHORT,
    MAX_POSITIONS,
    SWAP_ON_FULL,
    SWAP_MIN_CONFIDENCE,
    EXTENDED_HOURS,
    USE_DYNAMIC_TIERS,
    USE_RISK_EQUALIZED_SIZING,
    USE_VIX_ROC_FILTER,
    MIN_BUYING_POWER_PCT, MIN_POSITION_DOLLARS, PDT_WARN_AT_REMAINING,
    TAKE_PROFIT_NORMAL, TAKE_PROFIT_HIGH, STOP_LOSS_PCT,
    ATR_TP_RATIO, MAX_SHORT_FLOAT_PCT, HIGH_SHORT_FLOAT_STOCKS, is_high_short_float,
    EOD_CLOSE_ENABLED, EOD_CLOSE_TIME, EOD_CLOSE_STRATEGIES,
    GUARDRAIL_EOD_CLOSE_ENABLED, GUARDRAIL_EOD_CLOSE_TIME,
    PRICE_DRIFT_STOP_ENABLED, PRICE_DRIFT_STOP_PCT,
    PRICE_DRIFT_CHECK_INTERVAL_MIN, PRICE_DRIFT_LOOKBACK_MIN,
    TRAIL_STOP_PCT, PROFIT_TRAIL_GIVEBACK_PCT,
    STAGNANT_STOP_ENABLED, STAGNANT_STOP_CHECK_INTERVAL_MIN, EMA15_EXIT_MIN_BARS, EMA15_EXIT_DELTA_PCT, EMA15_TREND_DROP_PCT, EMA15_BREAKDOWN_PCT, EMA15_RECLAIM_PCT,
    EMA_TREND_FILTER_ENABLED, EMA_TREND_MIN_BARS,
    SWING_DRIFT_STOP_ENABLED, SWING_DRIFT_STOP_PCT,
    MIN_AVG_DAILY_VOLUME_REGULAR_HOURS, MIN_FLOAT_SHARES, MIN_MARKET_CAP,
    SWING_STALE_EXIT_ENABLED, SWING_STALE_DAYS, SWING_STALE_MIN_GAIN_PCT,
    NO_GAIN_EXIT_ENABLED, NO_GAIN_EXIT_HOURS, NO_GAIN_EXIT_MIN_PCT, NO_GAIN_EXIT_MAX_LOSS_PCT,
    AFTERHOURS_STOP_CHECK_ENABLED, AFTERHOURS_CHASE_STALE_SECONDS,
    MAX_POSITION_CONCENTRATION_PCT, CORRELATION_GROUPS, MAX_PORTFOLIO_LEVERAGE,
    POSITION_CAP_GROWTH_FACTOR, POSITION_CAP_ABSOLUTE_MAX_PCT,
    LONG_ONLY_MODE,
    STALE_ORDER_MINUTES, STALE_ORDER_MINUTES_INTRADAY,
    KILL_MODE_TRAIL_PCT,
    SMALL_ACCOUNT_EQUITY_THRESHOLD, SMALL_ACCOUNT_MAX_POSITIONS,
    SMALL_ACCOUNT_MIN_POSITION_DOLLARS,
    POSITION_SIZE_PCT, SMALL_ACCOUNT_POSITION_SIZE_PCT,
    CONF_SCALE_MIN_MULT, CONF_SCALE_FULL_CONF,
    MAX_POSITION_SIZE_PCT,
    STRATEGY_KELLY_MULT, STRATEGY_KELLY_MULT_DEFAULT,
    THIN_LIQUIDITY_EXCLUDED_STRATEGIES,
    CONF_RATCHET_ENABLED, CONF_RATCHET_TRIGGER_GAIN_PCT, CONF_RATCHET_MAX_TIGHTEN,
    MOMENTUM_FRESHNESS_ENABLED, MOMENTUM_FRESHNESS_STRATEGIES,
    TRADE_STALE_MOMENTUM_REJECTS,
    MOMENTUM_FRESHNESS_LOOKBACK_MIN, MOMENTUM_FRESHNESS_MAX_PULLBACK_PCT,
    THIN_LIQUIDITY_POSITION_SIZE_PCT,
    THIN_LIQUIDITY_TRAILING_STOP_MULT,
    MARKETABLE_LIMIT_BUFFER_PCT,
    FADED_ENTRY_PASSIVE_WINDOW_SECONDS, FADED_ENTRY_CEILING_TIMEOUT_SECONDS,
    REENTRY_TRAIL_PCT,
    LIVE,
)
from engine.equity.strategies import Signal, _get_float_shares, _get_market_cap
from engine.equity.universe import get_ti_primary
from engine.utils import MarketState, calculate_risk_adjusted_size, check_vix_roc_filter, get_dynamic_tier
from engine.utils.bars import get_bars, get_daily_volume_bars
from engine.never_trade import is_never_trade
from engine.notifications.notifications import send_email

log = logging.getLogger("ApexTrader")


# ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
# Helpers
# ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
def ratchet_scale(confidence: float) -> float:
    """Pure math for the confidence-ratchet trailing-stop multiplier.
    confidence <= SWAP_MIN_CONFIDENCE (0.75) -> 1.0 (no tightening).
    confidence == 1.0                        -> CONF_RATCHET_MAX_TIGHTEN (max tightening).
    Linear in between. See ratchet_confident_winners() for where this is used
    and CONFIG.md / config.py for the constants' rationale."""
    if confidence <= SWAP_MIN_CONFIDENCE:
        return 1.0
    span = max(1e-6, 1.0 - SWAP_MIN_CONFIDENCE)
    frac = min(1.0, (confidence - SWAP_MIN_CONFIDENCE) / span)
    return 1.0 - frac * (1.0 - CONF_RATCHET_MAX_TIGHTEN)


def _check_momentum_freshness(signal: Signal) -> Tuple[bool, Optional[str]]:
    """Reject a gap/momentum signal (MOMENTUM_FRESHNESS_STRATEGIES) if price
    has already faded MOMENTUM_FRESHNESS_MAX_PULLBACK_PCT+ off its high over
    the last MOMENTUM_FRESHNESS_LOOKBACK_MIN minutes — the move may already
    be rolling over by the time the order is about to submit, seconds to
    minutes after the strategy detected it. See engine/config.py for the
    full reasoning and known limitations (sharp reversals only, not gradual
    multi-hour fades).

    Returns (fresh, reject_reason). fresh=True with no reason for any
    strategy not in MOMENTUM_FRESHNESS_STRATEGIES, or when there isn't
    enough recent bar data to judge — never blocks on missing data.
    """
    if not MOMENTUM_FRESHNESS_ENABLED or signal.strategy not in MOMENTUM_FRESHNESS_STRATEGIES:
        return True, None
    bars = get_bars(signal.symbol, period="1d", interval="1m")
    if bars.empty or "high" not in bars.columns or "close" not in bars.columns:
        return True, None
    recent = bars.tail(MOMENTUM_FRESHNESS_LOOKBACK_MIN)
    recent_high = float(recent["high"].max())
    current_price = float(bars["close"].iloc[-1])
    if recent_high <= 0:
        return True, None
    pullback_pct = (recent_high - current_price) / recent_high * 100
    if pullback_pct > MOMENTUM_FRESHNESS_MAX_PULLBACK_PCT:
        return False, (
            f"{signal.symbol}: faded {pullback_pct:.1f}% off its {MOMENTUM_FRESHNESS_LOOKBACK_MIN}-min "
            f"high (${recent_high:.2f} -> ${current_price:.2f}) — {signal.strategy} entry not fresh"
        )
    return True, None


def _check_ema_trend_alignment(signal: Signal, is_long: bool) -> Tuple[bool, Optional[str]]:
    """2026-08-22, user request: simplified from an EMA9-vs-EMA20 crossover
    to an EMA's own slope -- current-minute EMA minus the previous minute's
    EMA must be positive for a long entry, negative for a short (checked
    alongside the trail-buy entry). Applies to both directions, unlike
    _check_momentum_freshness (long-only) -- a short entry needs the same
    short-term-trend check.

    2026-08-24, user request: EMA9 -> EMA7 -- faster/more responsive to
    recent price action, allows an earlier read on a turning trend.
    Briefly reverted back to EMA9 the same day on a flawed read of the
    backtest (EMA7 blocked 4 of today's real entries that EMA9 let
    through -- but all 4 turned out to be losers under both exit versions,
    so EMA7 blocking them was correct, not a problem). Verified: with the
    current exit logic, EMA7 beats EMA9 on today's 13 real entries
    (-$3.94 vs -$9.79) -- switched back to EMA7. Paired with the
    entry-anchored EMA15 delta exit (see check_ema15_exit) so an entry
    that's still below its EMA15 isn't rejected outright here -- it's
    instead watched post-entry for whether the gap keeps widening.

    Fail-open on missing/insufficient bar data (fewer than
    EMA_TREND_MIN_BARS of 1-min history) -- same philosophy as
    _check_momentum_freshness: never block a trade on data the bot doesn't
    have, only on data that actively contradicts it.
    """
    if not EMA_TREND_FILTER_ENABLED:
        return True, None
    bars = get_bars(signal.symbol, period="1d", interval="1m")
    if bars.empty or "close" not in bars.columns or len(bars) < EMA_TREND_MIN_BARS:
        return True, None
    ema7  = bars["close"].ewm(span=7, adjust=False).mean()
    slope = float(ema7.iloc[-1] - ema7.iloc[-2])  # this minute's EMA7 vs last minute's
    aligned = (slope > 0) if is_long else (slope < 0)
    if not aligned:
        return False, (
            f"{signal.symbol}: 1-min EMA7 slope {slope:+.4f} "
            f"({'not rising' if is_long else 'not falling'}) — trend not aligned with the "
            f"{'long' if is_long else 'short'} entry"
        )
    return True, None


def _resolve_freshness_reject(signal: Signal, fresh: bool, fade_reason: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Decide what _validate_trade does with a _check_momentum_freshness
    result: (valid, block_reason). fresh=True -> always valid, signal
    untouched. fresh=False -> hard-blocked (valid=False, block_reason=
    fade_reason) unless TRADE_STALE_MOMENTUM_REJECTS, in which case the
    signal is flagged thin_liquidity=True (same reduced sizing as a
    guardrail admit, see _apply_thin_liquidity_override) and treated as
    valid so it still trades -- UNLESS signal.strategy is in
    THIN_LIQUIDITY_EXCLUDED_STRATEGIES (2026-08-15: ORB/GapBreakout,
    measured net-negative specifically on their bypass trades), in which
    case it's hard-blocked regardless of the toggle. Split out for
    unit-testability without a broker/bars connection — mutates signal in
    place same as the inline version would, callers pass their own Signal
    instance."""
    if fresh:
        return True, None
    if not TRADE_STALE_MOMENTUM_REJECTS or signal.strategy in THIN_LIQUIDITY_EXCLUDED_STRATEGIES:
        return False, fade_reason
    signal.thin_liquidity = True
    signal.stale_entry = True  # narrower flag -- see Signal.stale_entry docstring in
                                # strategies.py. Only THIS path sets it; the guardrail-
                                # floor admit in scan.py sets thin_liquidity alone.
    return True, None


def _entry_rechase_slip_pct(chase_count: int) -> float:
    """Next slip% for an entry re-chase attempt (_sweep_pending_entries) --
    starts beyond the original MARKETABLE_LIMIT_BUFFER_PCT bound and widens
    each retry, capped at 3% same as every other re-chase path in this file
    (_sweep_force_closes, check_afterhours_stops, close_no_gain_positions)."""
    return min(MARKETABLE_LIMIT_BUFFER_PCT * (chase_count + 2), 3.0)


def _marketable_limit_price(price: float, is_long: bool, buffer_pct: float = MARKETABLE_LIMIT_BUFFER_PCT) -> float:
    """A limit price just past the reference price -- fills like a market
    order under normal conditions, but caps the worst case at buffer_pct
    instead of a plain market order absorbing an unbounded bid-ask spread.
    is_long=True (buying, or covering a short) rounds UP by buffer_pct;
    False (selling, or opening a short) rounds DOWN."""
    adj = 1 + buffer_pct / 100 if is_long else 1 - buffer_pct / 100
    return round(price * adj, 2)


def _live_quote_mid(client, symbol: str, fallback: float) -> float:
    """Live bid/ask midpoint -- the reference _marketable_limit_price should
    bound against, instead of the scan-time signal.price or a possibly-stale
    pos.current_price. By the time an order reaches the broker, the scan
    that produced the reference price can be seconds to minutes old (scan
    cadence, MAX_SIGNALS_PER_CYCLE throttling); bounding "within 1%" of a
    stale number defeats the point. Falls back to `fallback` if the quote
    call fails or either side is missing/non-positive -- same defensive
    pattern as the stale-order requote path in detect_stopped_out_positions."""
    try:
        q = client.get_latest_quote(symbol)
        bid = float(getattr(q, "bid_price", 0) or 0)
        ask = float(getattr(q, "ask_price", 0) or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
    except Exception:
        pass
    return fallback


def _apply_thin_liquidity_override(risk_info: Dict, signal: Signal, equity: float) -> Dict:
    """If signal.thin_liquidity is set, replace dollar_amount with a flat
    THIN_LIQUIDITY_POSITION_SIZE_PCT of equity, overriding confidence-scaling
    entirely rather than stacking on top of it — a predictable cap on the
    downside regardless of how confident the firing strategy was. Two
    independent reasons set this flag, same sizing either way: a rejected-
    list symbol admitted anyway (TRADE_THIN_LIQUIDITY_REJECTS, engine/
    equity/scan.py _scan_one) or a momentum-freshness reject traded anyway
    (TRADE_STALE_MOMENTUM_REJECTS, _validate_trade below, 2026-08-14).
    Returns risk_info unchanged if the signal isn't flagged.
    """
    if not getattr(signal, "thin_liquidity", False):
        return risk_info
    thin_dollars = round(equity * THIN_LIQUIDITY_POSITION_SIZE_PCT / 100, 2)
    out = dict(risk_info, dollar_amount=thin_dollars, allocation_pct=THIN_LIQUIDITY_POSITION_SIZE_PCT)
    log_extra = ""
    # stop_loss_pct only exists on the non-LIVE bracket path (_create_bracket_order's
    # inline trailing stop) -- the live path's protect_positions()/etc. don't read
    # risk_info at all, they get the same halving from _trail_pct_for() instead.
    if "stop_loss_pct" in risk_info:
        halved = round(risk_info["stop_loss_pct"] * THIN_LIQUIDITY_TRAILING_STOP_MULT, 2)
        out["stop_loss_pct"] = halved
        log_extra = f" | stop {risk_info['stop_loss_pct']:.1f}% -> {halved:.1f}%"
    log.info(
        f"[SIZE] {signal.symbol}: thin-liquidity admit — "
        f"${risk_info['dollar_amount']:,.0f} -> ${thin_dollars:,.0f} "
        f"({THIN_LIQUIDITY_POSITION_SIZE_PCT:.0f}% flat){log_extra}"
    )
    return out


def _apply_confidence_size_ramp(risk_info: Dict, confidence: float, equity: float) -> Dict:
    """2026-08-13, user request: confidence-scaling (_execute_entry's
    CONF_SCALE_MIN_MULT..CONF_SCALE_FULL_CONF ramp) plateaus at 1.0x for any
    confidence >= 85% -- 85% and 99% get sized identically. Originally
    patched with a flat step above 92% confidence.

    2026-08-15, user request: "increase the percentage progressively
    maximum to 15% maximum per ticker" -- replaced the flat step with a
    continuous linear ramp: allocation_pct rises from the base %
    (risk_info['allocation_pct'], i.e. POSITION_SIZE_PCT/SMALL_ACCOUNT_
    POSITION_SIZE_PCT) at CONF_SCALE_FULL_CONF (85%) up to
    MAX_POSITION_SIZE_PCT (15%) at 100% confidence -- every confidence
    level above 85% now gets its own size instead of just two tiers.
    Returns risk_info unchanged at or below CONF_SCALE_FULL_CONF. Applied
    before _apply_thin_liquidity_override in the caller, which fully
    overrides -- not stacks with -- either scaling step."""
    if confidence <= CONF_SCALE_FULL_CONF:
        return risk_info
    base_pct = risk_info["allocation_pct"]
    span     = max(1e-6, 1.0 - CONF_SCALE_FULL_CONF)
    frac     = min(1.0, (confidence - CONF_SCALE_FULL_CONF) / span)
    ramp_pct = base_pct + (MAX_POSITION_SIZE_PCT - base_pct) * frac
    return dict(risk_info, allocation_pct=ramp_pct, dollar_amount=round(equity * ramp_pct / 100.0, 2))


def _apply_strategy_kelly_mult(risk_info: Dict, strategy: str, equity: float) -> Dict:
    """2026-08-15, user request: per-strategy sizing informed by each
    strategy's own Kelly % (STRATEGY_KELLY_MULT in config.py -- GapBreakout
    2.0x, TrendBreaker 0.25x, everything else unchanged at 1.0x). Straight
    multiplier on whatever allocation_pct the confidence ramp already
    produced, clamped to MAX_POSITION_CONCENTRATION_PCT (the hard
    per-symbol cap, also enforced independently and more precisely at
    order-sizing time via signal.price/buying power in
    _size_with_buying_power -- this clamp is defense-in-depth so risk_info
    itself never CLAIMS more than the real ceiling allows).

    2026-08-15: found by running the full sizing pipeline against real
    symbols/confidences -- GapBreakout at 95% confidence ramps to 12.5%
    BEFORE this multiplier runs, so the unclamped 2.0x pushed
    allocation_pct/dollar_amount to 25%, past the 20% cap, even though
    the final executed share count was already correctly capped
    downstream. Harmless to the actual trade, but risk_info and the debug
    log line built from it were overstating what would really execute --
    clamped here so they can't diverge from reality at any pipeline stage.
    Returns risk_info unchanged for a 1.0x (default) strategy."""
    mult = STRATEGY_KELLY_MULT.get(strategy, STRATEGY_KELLY_MULT_DEFAULT)
    if mult == 1.0:
        return risk_info
    new_pct = min(risk_info["allocation_pct"] * mult, MAX_POSITION_CONCENTRATION_PCT)
    return dict(risk_info, allocation_pct=new_pct, dollar_amount=round(equity * new_pct / 100.0, 2))


def _trail_pct_for(symbol: str, price: float, entry_log: Dict, gain_pct: float = None) -> Tuple[float, str]:
    """Trailing-stop % for `symbol`. 2026-08-22, user request: replaced the
    tiered/thin-liquidity system (get_dynamic_tier + THIN_LIQUIDITY_
    TRAILING_STOP_MULT) with one flat floor, TRAIL_STOP_PCT, for every
    position -- no more per-tier or per-liquidity variability.

    If `gain_pct` (current unrealized %) is given and positive, widen past
    the floor to PROFIT_TRAIL_GIVEBACK_PCT of that gain once it computes
    wider than the floor -- a winning trade earns more room instead of
    riding the same fixed leash as a fresh entry. Losing/flat positions
    (gain_pct <= 0 or omitted) just get the flat floor.

    Single source of truth for every trailing-stop placement/re-place/
    tighten in this file (protect_positions, ratchet, after-hours
    virtual-stop, all re-arm fallbacks) instead of separate call sites
    drifting out of sync with each other."""
    trail_pct = TRAIL_STOP_PCT
    label = "FLAT"
    if gain_pct is not None and gain_pct > 0:
        profit_trail = round(gain_pct * (PROFIT_TRAIL_GIVEBACK_PCT / 100.0), 2)
        if profit_trail > trail_pct:
            trail_pct, label = profit_trail, "PROFIT"
    return trail_pct, label


def _demo() -> None:
    """python -m engine.execution.enhanced — asserts the ratchet math holds
    at its key points before it's trusted against a live account."""
    assert ratchet_scale(0.0) == 1.0, "below floor -> no tightening"
    assert ratchet_scale(0.75) == 1.0, "at floor -> no tightening"
    assert abs(ratchet_scale(1.0) - CONF_RATCHET_MAX_TIGHTEN) < 1e-9, "at 1.0 -> max tightening"
    mid = ratchet_scale(0.875)  # halfway between 0.75 and 1.0
    expected_mid = 1.0 - 0.5 * (1.0 - CONF_RATCHET_MAX_TIGHTEN)
    assert abs(mid - expected_mid) < 1e-9, f"halfway point off: {mid} != {expected_mid}"
    assert ratchet_scale(0.90) < ratchet_scale(0.80), "higher confidence must tighten more"
    print("ratchet_scale: all checks passed")

    # _trail_pct_for: flat floor, widens to PROFIT_TRAIL_GIVEBACK_PCT of gain
    # only once that's wider than the floor. 2026-08-22, user request.
    assert _trail_pct_for("X", 10.0, {}) == (TRAIL_STOP_PCT, "FLAT")
    assert _trail_pct_for("X", 10.0, {}, gain_pct=-2.0) == (TRAIL_STOP_PCT, "FLAT"), "losing position must use the floor"
    assert _trail_pct_for("X", 10.0, {}, gain_pct=0.0) == (TRAIL_STOP_PCT, "FLAT")
    crossover = TRAIL_STOP_PCT / (PROFIT_TRAIL_GIVEBACK_PCT / 100.0)  # gain% where widening starts beating the floor
    below = crossover - 1.0  # just under the crossover
    assert _trail_pct_for("X", 10.0, {}, gain_pct=below)[1] == "FLAT", "still under the floor -> no widening yet"
    above = crossover + 10.0  # comfortably past the crossover
    r = _trail_pct_for("X", 10.0, {}, gain_pct=above)
    assert r == (round(above * PROFIT_TRAIL_GIVEBACK_PCT / 100.0, 2), "PROFIT"), r
    print("_trail_pct_for: all checks passed")

    # _ema15_exit_reason: entry-anchored (price-EMA15) delta check, exits
    # once it's worsened by EMA15_EXIT_DELTA_PCT% of ema15 vs. its
    # entry-time value -- not on any single cross of EMA15 itself.
    # 2026-08-24, user request (supersedes the old zero-buffer cross check).
    reason = EnhancedExecutor._ema15_exit_reason
    # entry delta -5 (entered at 10, ema15 15, user's own worked example);
    # threshold at EMA15_EXIT_DELTA_PCT% of 15.
    threshold = EMA15_EXIT_DELTA_PCT / 100.0 * 15.0
    assert reason(10.00, 15.00, -5.0, True) is None, "delta unchanged from entry -> no exit"
    assert reason(10.00 + threshold / 2, 15.00, -5.0, True) is None, "delta improved -> no exit"
    assert reason(10.00 - threshold - 0.01, 15.00, -5.0, True) is not None, "delta worsened past threshold -> exit"
    assert reason(10.00 - threshold + 0.01, 15.00, -5.0, True) is None, "delta worsened but still under threshold -> no exit"
    # short: entry delta +5 (entered at 20, ema15 15) -- adverse direction is delta growing MORE positive
    assert reason(20.00, 15.00, 5.0, False) is None, "delta unchanged from entry (short) -> no exit"
    assert reason(20.00 + threshold + 0.01, 15.00, 5.0, False) is not None, "delta worsened past threshold (short) -> exit"
    print("_ema15_exit_reason: all checks passed")

    # _ema15_trend_drop_reason: second, independent check -- EMA15 itself
    # vs. its own entry-time value, not price vs. EMA15. 2026-08-24, user
    # request (closes the slow-bleed blind spot _ema15_exit_reason has).
    trend_reason = EnhancedExecutor._ema15_trend_drop_reason
    entry_ema15 = 15.00
    trend_threshold = EMA15_TREND_DROP_PCT / 100.0 * entry_ema15
    assert trend_reason(entry_ema15, entry_ema15, True) is None, "EMA15 unchanged from entry -> no exit"
    assert trend_reason(entry_ema15 - trend_threshold / 2, entry_ema15, True) is None, "EMA15 dipped but under threshold -> no exit"
    assert trend_reason(entry_ema15 - trend_threshold - 0.01, entry_ema15, True) is not None, "EMA15 fell past threshold -> exit"
    assert trend_reason(entry_ema15 + 1.0, entry_ema15, True) is None, "EMA15 rose (favorable for a long) -> no exit"
    # short: adverse direction is EMA15 rising
    assert trend_reason(entry_ema15 + trend_threshold + 0.01, entry_ema15, False) is not None, "EMA15 rose past threshold (short) -> exit"
    assert trend_reason(entry_ema15 - 1.0, entry_ema15, False) is None, "EMA15 fell (favorable for a short) -> no exit"
    print("_ema15_trend_drop_reason: all checks passed")

    # _ema15_breakdown_reason: single rule used for a favorable-side entry
    # (long: entered at/above EMA15; short: at/below) -- exit once price
    # breaks the CURRENT EMA15 by more than EMA15_BREAKDOWN_PCT%, not
    # anchored to entry at all. 2026-08-24, user request.
    breakdown_reason = EnhancedExecutor._ema15_breakdown_reason
    ema15 = 15.00
    breakdown_threshold = EMA15_BREAKDOWN_PCT / 100.0 * ema15
    assert breakdown_reason(ema15, ema15, True) is None, "close at EMA15 -> no exit (strict below only)"
    assert breakdown_reason(ema15 - breakdown_threshold + 0.001, ema15, True) is None, "close under EMA15 but within buffer -> no exit"
    assert breakdown_reason(ema15 - breakdown_threshold - 0.01, ema15, True) is not None, "close broke past the buffer -> exit"
    # short: adverse direction is a close ABOVE EMA15 + buffer
    assert breakdown_reason(ema15 + breakdown_threshold - 0.001, ema15, False) is None, "close over EMA15 but within buffer (short) -> no exit"
    assert breakdown_reason(ema15 + breakdown_threshold + 0.01, ema15, False) is not None, "close broke past the buffer (short) -> exit"
    print("_ema15_breakdown_reason: all checks passed")

    # _check_ema_trend_alignment: EMA7's own slope (this minute vs last)
    # must confirm the trade direction, fail-open on missing/insufficient
    # data. 2026-08-22, user request; EMA9 -> EMA7 2026-08-24 (see the
    # function's own docstring for the revert-then-revert-back).
    import pandas as _pd
    _orig_get_bars = get_bars
    _sig_stub = Signal("TEST", "buy", 10.0, 0.9, "test", "TestStrat")
    globals()["get_bars"] = lambda symbol, period, interval: _pd.DataFrame({"close": list(range(1, 40))})  # rising -> EMA7 slope positive
    ok, reason = _check_ema_trend_alignment(_sig_stub, is_long=True)
    assert ok is True and reason is None, "rising EMA7 must align with a long"
    ok, reason = _check_ema_trend_alignment(_sig_stub, is_long=False)
    assert ok is False and reason is not None, "rising EMA7 must reject a short"
    globals()["get_bars"] = lambda symbol, period, interval: _pd.DataFrame({"close": list(range(40, 1, -1))})  # falling -> EMA7 slope negative
    ok, reason = _check_ema_trend_alignment(_sig_stub, is_long=False)
    assert ok is True and reason is None, "falling EMA7 must align with a short"
    ok, reason = _check_ema_trend_alignment(_sig_stub, is_long=True)
    assert ok is False and reason is not None, "falling EMA7 must reject a long"
    globals()["get_bars"] = lambda symbol, period, interval: _pd.DataFrame()
    ok, reason = _check_ema_trend_alignment(_sig_stub, is_long=True)
    assert ok is True and reason is None, "empty bars must fail open, never block"
    globals()["get_bars"] = _orig_get_bars
    print("_check_ema_trend_alignment: all checks passed")

    # _entries_today_count: needs only the two bare attrs, no live client --
    # build a stub rather than a full EnhancedExecutor().
    class _Stub:
        _entries_today: Dict[str, int] = {}
        _entries_today_date = None
    stub = _Stub()
    count = EnhancedExecutor._entries_today_count
    assert count(stub, "PFSA") == 0, "first entry today must not look like a re-entry"
    stub._entries_today["PFSA"] = 1  # what _create_bracket_order does after that first entry fills
    assert count(stub, "PFSA") == 1, "second same-day entry must be flagged a re-entry"
    assert count(stub, "OTHER") == 0, "a different symbol is unaffected"
    stub._entries_today_date = datetime.date(2000, 1, 1)  # force a date rollover
    assert count(stub, "PFSA") == 0, "a new day must reset the count"
    print("_entries_today_count: all checks passed")




class OrderType(Enum):
    LONG  = "long"
    SHORT = "short"


@dataclass
class PDTTracker:
    """Pattern Day Trader tracking — syncs with live Alpaca daytrade_count."""
    trades: list = field(default_factory=list)

    def add(self, date: datetime.date) -> None:
        self.trades.append(date)
        cutoff = date - datetime.timedelta(days=7)
        self.trades = [d for d in self.trades if d > cutoff]

    def remaining(self, equity: float, live_count: int, pdt_flagged: bool = False) -> int:
        """Returns day trades remaining. 999 = exempt if account is PDT-exempt or equity >= $25k."""
        if equity >= PDT_ACCOUNT_MIN or not pdt_flagged:
            return 999
        used = max(live_count, len(self.trades))
        return max(0, PDT_MAX_TRADES - used)

    def can_trade(self, equity: float, live_count: int = 0, pdt_flagged: bool = False) -> bool:
        return self.remaining(equity, live_count, pdt_flagged) > 0


@dataclass
class PositionInfo:
    """Cached snapshot of open positions."""
    positions_dict: Dict[str, any]
    total_count:    int

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions_dict

    def is_long(self, symbol: str) -> bool:
        return self.has_position(symbol) and float(self.positions_dict[symbol].qty) > 0

    def is_short(self, symbol: str) -> bool:
        return self.has_position(symbol) and float(self.positions_dict[symbol].qty) < 0

    def total_market_value(self) -> float:
        """Sum of abs(market_value) across every open equity position
        (options legs excluded — they're sized/margined separately, same
        exclusion used throughout this file for concentration checks)."""
        total = 0.0
        for sym, pos in self.positions_dict.items():
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue
            try:
                total += abs(float(pos.market_value))
            except (TypeError, ValueError, AttributeError):
                continue
        return total


@dataclass
class AccountSnapshot:
    """Cached Alpaca account state — equity, buying power, live PDT count."""
    equity:              float
    buying_power:        float
    daytrade_count:      int
    pattern_day_trader:  bool = False
    maintenance_margin:  float = 0.0
    timestamp:           float = field(default=0.0)


# ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
# Executor
# ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
class EnhancedExecutor:
    """Optimized trade executor with consolidated long/short logic."""

    def __init__(self, client: TradingClient, use_bracket_orders: bool = True):
        self.client              = client
        self.use_bracket_orders  = use_bracket_orders
        self.pdt                 = PDTTracker()
        self.order_cache:  Dict[str, str] = {}
        self._position_cache: Optional[PositionInfo]    = None
        self._cache_timestamp: float = 0
        self._cache_ttl:       float = 5.0
        self._account_cache:  Optional[AccountSnapshot] = None
        self._account_ttl:    float = 2.0   # tight TTL — buying power must be fresh between orders
        self._htb_cache:      set   = set()   # hard-to-borrow symbols — skip shorts this session
        self._entry_log:   Dict[str, dict] = {}  # {symbol: {"strategy": str, "date": date}}
        self._swap_cycle_closed: set = set()     # positions already swapped this scan cycle
        self._ratchet_done: set = set()          # symbols whose stop was already confidence-tightened
        self._tp_targets: Dict[str, float] = {} # {symbol: take-profit price} for ATR-based TP tracking
        # {symbol: (entry_price - ema15) captured at entry submission} — the
        # reference check_ema15_exit() compares against every 1-min poll.
        # 2026-08-24, user request. Cleared on close in detect_stopped_out_positions.
        self._entry_ema15_delta: Dict[str, float] = {}
        # {symbol: raw ema15 value captured at entry submission} — separate
        # reference for the EMA15-trend-itself check (_ema15_trend_drop_reason),
        # independent of the delta above. 2026-08-24, user request. Cleared
        # alongside _entry_ema15_delta in detect_stopped_out_positions.
        self._entry_ema15: Dict[str, float] = {}
        # {symbols that entered BELOW their own EMA15 and have since
        # reclaimed it by EMA15_RECLAIM_PCT% -- permanently switched to the
        # breakdown rule (_ema15_breakdown_reason) from then on, same as a
        # favorable-side entry. 2026-08-24, user request. One-way: never
        # removed except on close. Cleared in detect_stopped_out_positions.
        self._reclaimed_ema15: set = set()
        self._pdt_stop_blocked: Dict[str, float] = {}  # {symbol: stop_price} — broker-rejected stops; monitored in software
        self._last_known_positions: Dict[str, dict] = {}  # {symbol: {entry_price, last_price, is_long}} — snapshot used to notice a position disappearing between polls
        self._afterhours_chase_count: Dict[str, int] = {}  # {symbol: consecutive re-chase attempts} — widens slip each retry so a fast-falling after-hours book actually fills
        self._no_gain_chase_count: Dict[str, int] = {}  # same, for close_no_gain_positions's re-chase
        self._pdt_overnight_forced: set = set()  # symbols where PDT also blocks close — forced overnight, no retries
        self._pdt_violation_alerted: bool = False  # tracks whether the PDT violation email has been sent this session
        self._force_close_pending: Dict[str, dict] = {}  # {symbol: {"reason": str, "chase_count": int}} — EOD/guardrail closes not yet confirmed flat; swept by _sweep_force_closes until filled
        self._guardrail_eod_closed: Dict[object, set] = {}  # {date: {symbol, ...}} — symbols already force-closed today by close_guardrail_fail_positions, so its per-minute reruns don't re-cancel/resubmit an order already in flight
        # {symbol: deque of the last N check_price_drift_stop prices, maxlen = PRICE_DRIFT_LOOKBACK_MIN / PRICE_DRIFT_CHECK_INTERVAL_MIN}
        # deque[0] is the oldest sample kept — the ~PRICE_DRIFT_LOOKBACK_MIN-minutes-ago reference once full.
        self._price_drift_history: Dict[str, Deque[float]] = {}
        # {symbol: {"order_id": str, "qty": int, "is_long": bool, "chase_count": int}}
        # — resting entry orders not yet confirmed filled; swept by _sweep_pending_entries
        self._entry_pending: Dict[str, dict] = {}
        self._stale_exit_done: object = None  # date of last completed swing stale-exit check
        # {symbol: count} — how many times a symbol has been entered today, reset
        # on a date rollover (_entries_today_date). 2026-08-18, user request: a
        # 2nd+ same-day entry uses a trailing BUY (see REENTRY_TRAIL_PCT) instead
        # of chasing a marketable limit -- PFSA that day, 2nd EarlySqueeze entry
        # chased in at $13.52 while fading 15% off its high, filled $12.50,
        # stopped $11.72 eight minutes later. Deliberately independent of
        # win/loss -- PFSA's first exit was a ratcheted win, not a loss, and
        # still deserved the trailing-buy treatment on the 2nd entry.
        self._entries_today: Dict[str, int] = {}
        self._entries_today_date: Optional[datetime.date] = None
        # symbols confirmed to have zero prior broker fill history -- lets
        # _is_reentry_signal's broker fallback (_get_entry_datetime) skip the
        # round-trip on every future entry attempt for a genuinely new name.
        self._no_history_cache: set = set()
        self.market_state: Optional[MarketState] = None
        self._rebuild_entry_log_from_orders()

    def update_market_state(self, market_state: MarketState) -> None:
        """Store the active market snapshot for per-cycle execution decisions."""
        self.market_state = market_state

    # -- Entry Log Rebuild (survive restarts) ----------------------------
    def _rebuild_entry_log_from_orders(self) -> None:
        """On startup, reconstruct today's entry log from Alpaca filled orders.
        Prevents swap-closes of same-day positions after a bot restart, which would
        trigger Alpaca PDT protection (error 40310100).

        2026-08-14: was BUY-only, so a SHORT position (opened via a SELL) never
        got an entry_log record after a restart. Confirmed live: SPAI entered
        10:54:41, a routine restart landed 16s later at 10:54:57, and the fresh
        process's entry_log had no 'SPAI' key at all -- which silently broke TWO
        things at once, both scoped by entry_log lookups: _trail_pct_for()
        couldn't see thin_liquidity=True anymore so protect_positions() armed a
        full 8.0% trailing stop instead of the intended 4.0% thin-liquidity half,
        and check_price_drift_stop()'s same-day scope (entry_log[sym]['date'] ==
        today) skipped SPAI entirely, leaving it with zero drift-stop coverage
        too. Now derives the correct entry side per symbol from the live
        position (BUY opened a long, SELL opened a short) instead of assuming
        BUY. thin_liquidity itself still can't be recovered this way (not
        derivable from broker order data) -- same known gap as the 0.0
        confidence / 'restored' strategy placeholder below."""
        try:
            today = datetime.date.today()
            import pytz
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            et       = pytz.timezone("America/New_York")
            try:
                positions = self.client.get_all_positions()
            except Exception:
                positions = []
            is_long_by_sym = {p.symbol: float(p.qty) > 0 for p in positions}
            # Filter to today only — avoids fetching the full account order history
            # on accounts with months of activity (can be thousands of orders).
            today_start = datetime.datetime.combine(today, datetime.time.min).replace(tzinfo=pytz.UTC)
            req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=today_start)
            filled_orders = self.client.get_orders(filter=req)
            for order in filled_orders:
                filled_at = getattr(order, "filled_at", None)
                if filled_at is None:
                    continue
                if hasattr(filled_at, "astimezone"):
                    order_date = filled_at.astimezone(et).date()
                else:
                    order_date = today  # conservative fallback
                if order_date != today:
                    continue
                sym = order.symbol
                if sym not in is_long_by_sym:
                    continue  # no open position left for this symbol — nothing to protect
                # order.side is an OrderSide enum; str(enum) is "OrderSide.SELL", not
                # "sell" -- comparing that against a bare "buy"/"sell" literal never
                # matches. .value gives the plain string; getattr falls back to the
                # raw attribute so a plain string (e.g. from a test double) still
                # works. 2026-08-14: this was the actual root cause of the rebuild
                # being a total no-op — the "Entry log rebuilt from today's orders"
                # log line had never once fired in the whole log history, for ANY
                # symbol, long or short.
                raw_side = getattr(order, "side", "")
                side = str(getattr(raw_side, "value", raw_side)).lower()
                entry_side = "buy" if is_long_by_sym[sym] else "sell"
                if side != entry_side:
                    continue  # this order closed/trimmed the position, not opened it
                if sym not in self._entry_log:
                    self._entry_log[sym] = {
                        "strategy": "restored",
                        "date": today,
                        "confidence": 0.0,
                    }
            if self._entry_log:
                log.info(
                    f"Entry log rebuilt from today's orders: "
                    f"{', '.join(self._entry_log.keys())}"
                )
        except Exception as e:
            log.warning(f"_rebuild_entry_log_from_orders failed (non-fatal): {e}")

    def _current_market_state(self) -> MarketState:
        if self.market_state is not None:
            return self.market_state
        raise RuntimeError("EnhancedExecutor requires market_state to be set before execution")

    # -- Position Cache ----------------------------------------------------
    def _has_pending_close(self, symbol: str) -> bool:
        """True if *symbol* already has a resting non-GTC order (i.e. something
        other than its routine protective trailing stop) — meaning a swap-close
        was already submitted for it on an earlier cycle and just hasn't filled
        yet (routine in pre/after-hours illiquidity). Candidate-finders use this
        to avoid re-selecting the same position for a second close order before
        the first one clears — confirmed in production 2026-08-05: without this,
        RRC and GCT each got a duplicate close submitted 10 minutes apart, and
        both swaps were for nothing since the intended new entry (PLTR/ONDS)
        still got skipped on insufficient buying power either time (freed cash
        from an unfilled close doesn't settle same-cycle)."""
        try:
            for o in (self.client.get_orders() or []):
                if o.symbol != symbol:
                    continue
                if getattr(o, "time_in_force", None) != TimeInForce.GTC:
                    return True
            return False
        except Exception:
            return False

    def _find_weakest_position(self) -> Optional[str]:
        """Return the symbol of the open long position with the worst unrealized P&L %.
        Skips positions entered today (protected for full day), those already
        closed this cycle, and those already mid-close from a prior cycle.
        Returns None if no closable position found.

        Does NOT require qty_available > 0: every position here normally carries
        a full-size GTC trailing stop (qty_available is always 0 as a result),
        and _attempt_swap already cancels that resting order before closing —
        requiring qty_available > 0 here meant this never found a candidate in
        practice, silently defeating the whole swap-on-high-confidence feature.
        """
        try:
            today = datetime.date.today()
            entered_today = {
                sym for sym, info in self._entry_log.items()
                if info.get("date") == today
            }
            positions = self.client.get_all_positions()
            longs = [
                p for p in positions
                if float(p.qty) > 0
                and p.symbol not in self._swap_cycle_closed
                and p.symbol not in entered_today
                and not re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', p.symbol)  # skip OCC option symbols
                and not self._has_pending_close(p.symbol)
            ]
            if not longs:
                return None
            worst = min(longs, key=lambda p: float(p.unrealized_plpc))
            return worst.symbol
        except Exception as e:
            log.warning(f"_find_weakest_position error: {e}")
            return None

    def _find_stalest_position(self, min_hours: float = NO_GAIN_EXIT_HOURS) -> Optional[str]:
        """Return the symbol of the oldest closable long position held >= min_hours
        (default: same 24h bar as NO_GAIN_EXIT_HOURS), for swap-out when a new
        high-confidence signal arrives and the book is full. Age takes priority
        over P&L here — a day-old idea makes room for a stronger new one whether
        it's currently green or red. This is on top of (not instead of)
        close_no_gain_positions, which separately force-exits anything stale
        AND non-positive every cycle regardless of new signals."""
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            positions = self.client.get_all_positions()
            candidates = []
            for p in positions:
                sym = p.symbol
                if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                    continue  # options legs — managed separately
                if float(p.qty) <= 0:
                    continue
                if sym in self._swap_cycle_closed:
                    continue
                if self._has_pending_close(sym):
                    continue
                entry_dt = self._get_entry_datetime(sym)
                if entry_dt is None:
                    continue
                held_hours = (now_utc - entry_dt).total_seconds() / 3600
                if held_hours < min_hours:
                    continue
                candidates.append((held_hours, sym))
            if not candidates:
                return None
            candidates.sort(reverse=True)  # oldest first
            return candidates[0][1]
        except Exception as e:
            log.warning(f"_find_stalest_position error: {e}")
            return None

    def _find_least_confident_position(self, min_new_conf: float = 0.0) -> tuple:
        """Return (symbol, entry_confidence) of the held long position with the lowest
        entry confidence that is strictly below min_new_conf.
        Skips positions entered today (give them a full day) and those already swapped.
        Returns (None, 1.0) if no suitable candidate found."""
        try:
            today = datetime.date.today()
            entered_today = {
                sym for sym, info in self._entry_log.items()
                if info.get("date") == today
            }
            positions = self.client.get_all_positions()
            candidates = [
                p for p in positions
                if float(p.qty) > 0
                and p.symbol not in self._swap_cycle_closed
                and p.symbol not in entered_today
                and not re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', p.symbol)  # skip OCC option symbols
                and not self._has_pending_close(p.symbol)
            ]
            if not candidates:
                return None, 1.0

            def _entry_conf(p):
                return self._entry_log.get(p.symbol, {}).get("confidence", 0.0)

            worst = min(candidates, key=_entry_conf)
            worst_conf = _entry_conf(worst)
            # Only swap if new signal is meaningfully more confident (>5% gap)
            if worst_conf >= min_new_conf - 0.05:
                return None, worst_conf
            return worst.symbol, worst_conf
        except Exception as e:
            log.warning(f"_find_least_confident_position error: {e}")
            return None, 1.0

    def _get_positions(self, force_refresh: bool = False) -> PositionInfo:
        now = time.time()
        if force_refresh or self._position_cache is None or (now - self._cache_timestamp) > self._cache_ttl:
            raw = self.client.get_all_positions()
            self._position_cache = PositionInfo(
                positions_dict={p.symbol: p for p in raw},
                total_count=len(raw),
            )
            self._cache_timestamp = now
        return self._position_cache

    # -- Account Cache -----------------------------------------------------
    def _get_account(self, force_refresh: bool = False) -> AccountSnapshot:
        now = time.time()
        if force_refresh or self._account_cache is None or (now - self._account_cache.timestamp) > self._account_ttl:
            raw = self.client.get_account()
            self._account_cache = AccountSnapshot(
                equity=float(raw.equity),
                buying_power=float(raw.buying_power),
                daytrade_count=int(raw.daytrade_count or 0),
                pattern_day_trader=str(getattr(raw, "pattern_day_trader", False)).lower() in ("1", "true", "yes"),
                maintenance_margin=float(getattr(raw, "maintenance_margin", None) or 0.0),
                timestamp=now,
            )
        return self._account_cache

    @property
    def shorting_blocked(self) -> bool:
        """Live account-wide short-selling gate — Alpaca's own Reg T equity
        minimum (MIN_EQUITY_FOR_SHORT), read fresh off the 2s-TTL account
        cache every time so it self-corrects the moment equity crosses back
        above the floor. Replaces an old sticky `self.shorting_blocked = True`
        flag that a single misclassified broker rejection could leave stuck
        for the rest of the session with no way back — confirmed 2026-08-07:
        one INDI no-borrow rejection, misread as account-wide, disabled every
        short for hours despite the account's Shorting Enabled setting being
        on the whole time."""
        return self._get_account().equity < MIN_EQUITY_FOR_SHORT

    # -- Swap -----------------------------------------------------------
    def _attempt_swap(self, signal: Signal, swap_only: bool) -> Tuple[bool, Optional[str]]:
        """Try to close the stalest (24h+, falling back to weakest P&L) position
        to make room / free cash for *signal*. Shared by the buying-power gate
        (cash-starved even below max positions) and the max-positions gate.

        Returns (closed, block_reason):
          closed=True        a position was closed — caller should refresh
                              account/position state before re-checking gates.
          block_reason=str   the close attempt itself failed and entry should
                              be denied (position may be left unprotected).
          Otherwise (False, None): no candidate to swap — caller proceeds
          without a swap (matches the pre-existing "allow entry anyway" path).
        """
        label = "SWAP (bear)" if swap_only else "SWAP"
        stale_candidate = self._find_stalest_position()
        if stale_candidate:
            weakest, swap_reason = stale_candidate, "stale 24h+"
        else:
            weakest, swap_reason = self._find_weakest_position(), "weakest"
        if not weakest:
            log.debug(f"No swappable position found for {signal.symbol}")
            return False, None

        log.info(
            f"{label}: closing {weakest} ({swap_reason}) to make room for "
            f"{signal.symbol} (conf={signal.confidence:.0%})"
        )
        # Any resting order for this symbol — the GTC trailing stop, or a
        # leftover DAY close from a prior NO-GAIN/stale-exit attempt — reserves
        # qty and makes Alpaca reject close_position() as a wash trade (confirmed
        # in production: 40310000, "opposite side market/stop order exists").
        # Cancel ALL of them first, not just the GTC, so the swap-close actually
        # goes through (GTC-only cancel here previously had a 0% success rate).
        weakest_gtc_id = None
        try:
            for o in (self.client.get_orders() or []):
                if o.symbol != weakest:
                    continue
                if getattr(o, "time_in_force", None) == TimeInForce.GTC:
                    weakest_gtc_id = o.id
                self.client.cancel_order_by_id(str(o.id))
                time.sleep(0.4)
        except Exception as cancel_err:
            log.warning(f"SWAP {weakest}: order cancel failed, close may reject: {cancel_err}")

        try:
            self.client.close_position(weakest)
            self._swap_cycle_closed.add(weakest)
            # Closing a prior-day position is NOT a day trade — do not count against PDT
            return True, None
        except Exception as e:
            err_str = str(e)
            if "40310100" in err_str:
                # Alpaca PDT protection: position was entered today — can't close same day.
                # Mark as today's entry so it's never selected as swap candidate again.
                self._entry_log[weakest] = {
                    "strategy": "restored",
                    "date": datetime.date.today(),
                    "confidence": 0.0,
                }
                log.warning(
                    f"SWAP skip {weakest}: PDT same-day protection (40310100) — "
                    f"marked as today entry, will not retry this session"
                )
                # Don't block the new signal — allow entry without the swap
                return False, None
            log.warning(f"SWAP close failed for {weakest}: {e}")
            if weakest_gtc_id:
                # We cancelled its GTC stop to attempt the close, and the
                # close itself failed — re-arm protection immediately
                # rather than leave the position naked.
                try:
                    weakest_pos = next(
                        (p for p in self.client.get_all_positions() if p.symbol == weakest), None
                    )
                    if weakest_pos is not None:
                        w_qty     = int(float(weakest_pos.qty))
                        w_current = float(weakest_pos.current_price)
                        w_trail   = _trail_pct_for(weakest, w_current, self._entry_log)[0]
                        self.client.submit_order(TrailingStopOrderRequest(
                            symbol        = weakest,
                            qty           = abs(w_qty),
                            side          = OrderSide.SELL if w_qty > 0 else OrderSide.BUY,
                            type          = AlpacaOrderType.TRAILING_STOP,
                            time_in_force = TimeInForce.GTC,
                            trail_percent = w_trail,
                        ))
                        log.warning(f"SWAP {weakest}: re-armed GTC trailing stop after failed close")
                except Exception as rearm_err:
                    log.error(f"SWAP {weakest}: close failed AND GTC re-arm failed — position may be UNPROTECTED: {rearm_err}")
            return False, f"Swap close failed: {e}"

    # -- Validation --------------------------------------------------------
    def _validate_trade(self, signal: Signal, acct: AccountSnapshot, order_type: OrderType, swap_only: bool = False) -> Tuple[bool, Optional[str]]:
        if USE_VIX_ROC_FILTER:
            allow, roc = check_vix_roc_filter()
            if not allow:
                return False, f"VIX spike filter: {roc:.1f}% increase"

        # PDT — use live broker count (survives restarts).
        # Block only when the count EXCEEDS the limit (4+) — an actual PDT violation.
        # At exactly 3/3: new buys are allowed because they are held overnight (not same-day
        # round-trips) and therefore do NOT count as additional day trades.
        if acct.pattern_day_trader and acct.equity < PDT_ACCOUNT_MIN and acct.daytrade_count > PDT_MAX_TRADES:
            msg = (
                f"PDT VIOLATION: {acct.daytrade_count} day trades used "
                f"(limit {PDT_MAX_TRADES}, equity ${acct.equity:,.0f}) — "
                f"account may be flagged as Pattern Day Trader. Review immediately!"
            )
            log.error(msg)
            if not getattr(self, "_pdt_violation_alerted", False):
                send_email("[APEXTRADER] PDT VIOLATION ALERT", msg)
                self._pdt_violation_alerted = True
            return False, f"PDT violation: {acct.daytrade_count}/{PDT_MAX_TRADES} day trades exceeded"
        dt_left = self.pdt.remaining(acct.equity, acct.daytrade_count, acct.pattern_day_trader)
        if acct.pattern_day_trader and dt_left <= PDT_WARN_AT_REMAINING and acct.equity < PDT_ACCOUNT_MIN:
            log.warning(f"PDT WARNING: only {dt_left} day trade(s) remaining (equity ${acct.equity:,.0f})")

        # Alpaca's own Reg T minimum to short at all — checked live every time
        # (not cached/session-flag) so shorting resumes automatically the
        # moment equity crosses back above the floor, no restart needed.
        if order_type == OrderType.SHORT and acct.equity < MIN_EQUITY_FOR_SHORT:
            return False, f"equity ${acct.equity:,.0f} < ${MIN_EQUITY_FOR_SHORT:,.0f} minimum required to short"

        # Skip hard-to-borrow shorts cached from previous failures this session
        if order_type == OrderType.SHORT and signal.symbol in self._htb_cache:
            return False, f"{signal.symbol} hard-to-borrow (cached)"

        # 2026-08-24, user request: no post-loss re-entry cooldown at all —
        # every entry (cooldown or not) already goes through the trailing-buy
        # path (_create_bracket_order), which can't fill mid-fall the way a
        # marketable chase could. Protection against a re-firing signal is
        # the exit stack alone now: the trailing stop, check_ema15_exit
        # (per-minute), and the standalone software stop-loss. See SOXS
        # (2026-08-05, 22 trades/-$605 net re-firing the same losing signal)
        # for why that stack matters if this gets revisited.

        # Momentum entry freshness (long only — a short entry isn't chasing a
        # gap up) — reject a gap/momentum signal that's already faded off its
        # recent high by the time we're about to submit. See engine/config.py
        # MOMENTUM_FRESHNESS_* for the reasoning and known limitations.
        if order_type == OrderType.LONG:
            fresh, fade_reason = _check_momentum_freshness(signal)
            valid, block_reason = _resolve_freshness_reject(signal, fresh, fade_reason)
            if not valid:
                return False, block_reason
            if fade_reason:
                log.info(f"[SIZE] {fade_reason} — trading anyway at reduced size")

        # EMA7 slope trend alignment (both directions) — see
        # _check_ema_trend_alignment for the reasoning.
        trend_ok, trend_reason = _check_ema_trend_alignment(signal, order_type == OrderType.LONG)
        if not trend_ok:
            return False, trend_reason

        # Asset tradability check: skip halted or suspended symbols
        try:
            asset = self.client.get_asset(signal.symbol)
            raw_status = getattr(asset, "status", "active")
            status = str(getattr(raw_status, "value", raw_status)).lower()
            if status != "active":
                return False, f"{signal.symbol} not tradable: asset status={raw_status}"
            if not getattr(asset, "tradable", True):
                return False, f"{signal.symbol} not tradable: asset.tradable=False"
        except Exception as e:
            log.warning(f"{signal.symbol}: asset status check failed ({e}) — proceeding cautiously")

        # Pending order guard: don't submit a second order if one is already live/filling
        if signal.symbol in self.order_cache:
            cached_id = self.order_cache[signal.symbol]
            try:
                cached_order = self.client.get_order_by_id(cached_id)
                active_statuses = {"new", "partially_filled", "pending_new", "accepted", "held"}
                if str(getattr(cached_order, "status", "")).lower() in active_statuses:
                    return False, f"Pending order already active for {signal.symbol} (id={cached_id})"
                else:
                    # Order is filled/cancelled — remove stale cache entry
                    del self.order_cache[signal.symbol]
            except Exception:
                # Can't verify — keep cache entry intact to avoid double-submit risk
                return False, f"Could not verify order status for {signal.symbol} (id={cached_id}) — skipping to be safe"

        positions = self._get_positions()

        # Dynamic max positions: use equity-based strategic capacity (not raw buying_power).
        # buying_power can be artificially depressed by leveraged/inverse ETF margin requirements,
        # causing the bot to permanently block new entries even when capital is available.
        # We compute effective_max from equity × position_size_pct, then separately gate each
        # execution on whether buying_power is sufficient for one position.
        #
        # 2026-08-13, user request ("max position increase to 24"): SMALL_ACCOUNT_MAX_POSITIONS
        # (24) had been defined in config.py since before this file existed but was dead --
        # imported here and never referenced, so every account, small or not, was silently
        # capped at plain MAX_POSITIONS (12). Wired it in as the ceiling for accounts under
        # SMALL_ACCOUNT_EQUITY_THRESHOLD. Also switched the equity_capacity estimate to use
        # THIN_LIQUIDITY_POSITION_SIZE_PCT (3%) when the signal itself is a thin-liquidity
        # admit, not the flat 7.5% small-account rate -- otherwise the affordability math
        # still silently caps out around 12 (7.5% x 12 ≈ 90% of equity) regardless of the new
        # 24 ceiling, since today's guardrail widening means most newly-eligible signals will
        # actually execute at the smaller 3% size, not 7.5%.
        _pos_size_pct = (
            THIN_LIQUIDITY_POSITION_SIZE_PCT if getattr(signal, "thin_liquidity", False)
            else SMALL_ACCOUNT_POSITION_SIZE_PCT if acct.equity < SMALL_ACCOUNT_EQUITY_THRESHOLD
            else POSITION_SIZE_PCT
        )
        _pos_size_dollars = max(MIN_POSITION_DOLLARS, acct.equity * _pos_size_pct / 100.0)
        # Strategic max: how many positions our equity allocation strategy supports
        equity_capacity = max(1, int(acct.equity * 0.95 / _pos_size_dollars))
        _max_positions_cap = (
            SMALL_ACCOUNT_MAX_POSITIONS
            if acct.equity < SMALL_ACCOUNT_EQUITY_THRESHOLD
            else MAX_POSITIONS
        )
        effective_max = min(_max_positions_cap, equity_capacity)
        log.debug(
            f"[DBG] effective_max={effective_max} equity={acct.equity:.0f} bp={acct.buying_power:.0f} "
            f"pos_size=${_pos_size_dollars:.0f} ({_pos_size_pct:.0f}%) equity_cap={equity_capacity} "
            f"max_cap={_max_positions_cap}"
        )

        # ── Buying power gate (must come first) ───────────────────────────
        # Check if sufficient buying power for this trade (primary constraint).
        # This allows entry even when at max positions if capital is available.
        margin = 2.0 if order_type == OrderType.SHORT else 1.0
        min_usable = (SMALL_ACCOUNT_MIN_POSITION_DOLLARS
                      if acct.equity < SMALL_ACCOUNT_EQUITY_THRESHOLD
                      else MIN_POSITION_DOLLARS)
        min_bp_needed = min_usable * margin

        if acct.buying_power < min_bp_needed:
            # Cash-starved even below max positions (e.g. margin tied up by
            # leveraged/inverse ETFs) — a high-confidence signal should still
            # be able to bump a stale/weak position for the cash rather than
            # just being skipped every cycle until something exits on its own.
            if SWAP_ON_FULL and signal.confidence >= SWAP_MIN_CONFIDENCE and positions.total_count > 0:
                closed, block_reason = self._attempt_swap(signal, swap_only)
                if block_reason:
                    return False, block_reason
                if closed:
                    acct = self._get_account(force_refresh=True)
                    positions = self._get_positions(force_refresh=True)
            if acct.buying_power < min_bp_needed:
                return False, (
                    f"Insufficient buying power: ${acct.buying_power:,.0f} "
                    f"(need ${min_bp_needed:,.0f} for minimum position)"
                )

        # ── Max positions gate (secondary; optional swap if at limit) ─────
        if positions.total_count >= effective_max:
            if not (SWAP_ON_FULL and signal.confidence >= SWAP_MIN_CONFIDENCE):
                # At max but BP available — allow entry (no swap needed)
                log.debug(
                    f"At max positions {positions.total_count}/{effective_max} but allowing entry "
                    f"due to available BP ${acct.buying_power:,.0f}"
                )
            else:
                # Strong confidence signal + at max: prefer swap to maintain position count.
                closed, block_reason = self._attempt_swap(signal, swap_only)
                if block_reason:
                    return False, block_reason
                if closed:
                    positions = self._get_positions(force_refresh=True)

        if positions.has_position(signal.symbol):
            if order_type == OrderType.LONG  and positions.is_long(signal.symbol):
                return False, f"Already long {signal.symbol}"
            if order_type == OrderType.SHORT and positions.is_short(signal.symbol):
                return False, f"Already short {signal.symbol}"

        return True, None

    # -- Buying Power Sizing -----------------------------------------------
    def _size_with_buying_power(
        self, buying_power: float, signal: Signal,
        risk_info: Dict, order_type: OrderType
    ) -> Tuple[int, Optional[str]]:
        """Returns (shares, skip_reason). Downsizes if BP constrained, skips if below min.

        2026-08-18, user request: "prioritize the full number than dollar
        value... 10% limit puts 1.8 stock then round to 2 stocks if there is
        cash available" -- `desired` rounds to the NEAREST share instead of
        always truncating down, so a 1.8-share target becomes 2 rather than
        1 (silently using only 56% of the intended allocation). The caps
        below (max_bp, max_concentration, max_leverage) stay floored with
        int() -- those are hard capacity ceilings, not targets, so "if
        there is cash available" is enforced by the min() below: rounding
        desired up only sticks when a cap doesn't clamp it back down."""
        margin  = 2.0 if order_type == OrderType.SHORT else 1.0
        usable  = buying_power * (1.0 - MIN_BUYING_POWER_PCT / 100.0)
        desired = round(risk_info["dollar_amount"] / signal.price)
        max_bp  = int(usable / (signal.price * margin))

        account_snapshot = self._account_cache or self._get_account()  # use cached if available
        max_concentration = int(account_snapshot.equity * MAX_POSITION_CONCENTRATION_PCT / 100.0 / signal.price)

        # 2026-08-17, user request: cap TOTAL exposure across every open
        # position at MAX_PORTFOLIO_LEVERAGE x equity, independent of
        # whatever margin the broker's own buying_power would otherwise
        # allow (max_bp above already reflects margin -- this is a
        # separate, usually-stricter ceiling on the whole book at once,
        # not per-symbol). See enforce_portfolio_leverage() for the
        # periodic backstop covering a position that drifts over the cap
        # through price appreciation alone.
        current_exposure   = self._get_positions().total_market_value()
        leverage_cap_value = account_snapshot.equity * MAX_PORTFOLIO_LEVERAGE
        leverage_headroom  = max(0.0, leverage_cap_value - current_exposure)
        max_leverage        = int(leverage_headroom / (signal.price * margin))

        shares  = min(desired, max_bp, max_concentration, max_leverage)

        min_position = SMALL_ACCOUNT_MIN_POSITION_DOLLARS if account_snapshot.equity < SMALL_ACCOUNT_EQUITY_THRESHOLD else MIN_POSITION_DOLLARS

        if shares < 1:
            if max_leverage < 1:
                return 0, (
                    f"Portfolio leverage cap: ${current_exposure:,.0f} exposure already at/above "
                    f"{MAX_PORTFOLIO_LEVERAGE:.1f}x equity (${leverage_cap_value:,.0f} cap) for {signal.symbol}"
                )
            return 0, (
                f"Insufficient BP: ${buying_power:,.0f} usable ${usable:,.0f} "
                f"for {signal.symbol} @ ${signal.price:.2f} (x{margin:.0f} margin)"
            )

        cost = shares * signal.price

        # Debug trace for min position handling.
        log.debug(
            f"size check {signal.symbol}: equity={account_snapshot.equity:.2f}, "
            f"min_position=${min_position:.2f}, shares={shares}, cost=${cost:.2f}, desired={desired}, max_bp={max_bp}, usable=${usable:.2f}"
        )

        if cost < min_position:
            return 0, f"{signal.symbol} too small after downsize: ${cost:.0f} < min ${min_position:.0f}"

        if shares < desired:
            log.info(
                f"  BP downsize {signal.symbol}: {desired} -> {shares} shares "
                f"(BP ${buying_power:,.0f}, usable ${usable:,.0f}, cost ${cost:,.0f})"
            )
        return shares, None

    # ── Bracket Prices ──────────────────────────────────────────────────────────
    def _calculate_bracket_prices(self, signal: Signal, risk_info: Dict, order_type: OrderType) -> tuple:
        if signal.atr_stop and signal.atr_stop > 0:
            # ATR-based 2:1 R:R — stop at 1.5×ATR, target at 2× the risk
            risk_dist = signal.atr_stop
            if order_type == OrderType.LONG:
                sl = round(signal.price - risk_dist, 2)
                tp = round(signal.price + ATR_TP_RATIO * risk_dist, 2)
            else:
                sl = round(signal.price + risk_dist, 2)
                tp = round(signal.price - ATR_TP_RATIO * risk_dist, 2)
        else:
            # Percentage-based fallback
            if order_type == OrderType.LONG:
                sl = round(signal.price * (1 - risk_info["stop_loss_pct"] / 100), 2)
                tp = round(signal.price * (1 + risk_info["tp"]            / 100), 2)
            else:
                sl = round(signal.price * (1 + risk_info["stop_loss_pct"] / 100), 2)
                tp = round(signal.price * (1 - risk_info["tp"]            / 100), 2)
        return sl, tp

    # ── Entry + Trailing Stop Order ──────────────────────────────────────────
    def _handle_short_rejection(self, signal: Signal, e: Exception) -> None:
        """Broker rejected a short with "cannot be sold short" / 40310000 /
        "account is not allowed to short". Alpaca reuses that same wording for
        two different causes that need different handling: a genuine
        per-symbol no-borrow-available condition (should stick for the
        session) versus the account-wide Reg T equity minimum,
        MIN_EQUITY_FOR_SHORT (transient — must NOT poison one ticker's cache).
        Confirmed 2026-08-10: FIG and RIG both got cached as hard-to-borrow
        from rejections that fired while equity was under $2,000, then stayed
        stuck "not shortable" for the rest of the session even after equity
        recovered — checking equity here first is what `shorting_blocked`
        already does live, so re-check it rather than caching the symbol."""
        if self.shorting_blocked:
            log.warning(
                f"Short blocked {signal.symbol}: account equity below "
                f"${MIN_EQUITY_FOR_SHORT:,.0f} minimum — not caching as HTB, "
                "will retry once equity recovers"
            )
            return
        self._htb_cache.add(signal.symbol)
        log.warning(f"Short blocked {signal.symbol} (not shortable/insufficient BP): {e}")

    def _entries_today_count(self, symbol: str) -> int:
        """How many times `symbol` has already been entered today -- resets
        on a date rollover. Read BEFORE submitting a new entry (0 = first
        entry today, so a return value > 0 means the one about to be
        submitted is a re-entry). See _entries_today in __init__."""
        today = datetime.date.today()
        if self._entries_today_date != today:
            self._entries_today.clear()
            self._entries_today_date = today
        return self._entries_today.get(symbol, 0)

    def _is_reentry_signal(self, symbol: str, is_long: bool = True) -> bool:
        """True if `symbol` should use the trailing-buy entry path instead of
        the normal marketable chase: a 2nd+ same-day entry, OR any symbol with
        SOME prior fill history at all -- broker-confirmed via
        _get_entry_datetime, since _entry_log alone doesn't survive this
        bot's frequent restarts.

        2026-08-18, user request: "the re entry to trail buy doesn't have to
        come from cool down list only... even if the non cool down reentry to
        a prior traded stock is entering put in a trail buy order" -- SNDQ
        that day: stopped 09:02 (no cooldown block issue, still same-day
        entry), but the general case is a symbol that WON its last trade or
        was traded days ago -- same-day count alone catches neither, and it
        deserves the same falling-knife protection on re-entry.

        2026-08-24, user request: dropped the post-loss cooldown branch that
        used to live here -- there's no cooldown window left to check (see
        _validate_trade), everything else about this function is unchanged.

        Broker lookup only runs once per symbol per process lifetime -- a
        confirmed "never traded" result is cached in _no_history_cache so a
        genuinely new symbol doesn't pay a broker round-trip on every single
        entry attempt forever."""
        if self._entries_today_count(symbol) > 0:
            return True
        if symbol in self._no_history_cache:
            return False
        has_history = self._get_entry_datetime(symbol, is_long) is not None
        if not has_history:
            self._no_history_cache.add(symbol)
        return has_history

    def _create_bracket_order(self, signal: Signal, shares: int, risk_info: Dict, order_type: OrderType) -> bool:
        """Submit a trailing-buy entry, then a GTC trailing stop at
        TRAIL_STOP_PCT%. TP bracket leg is intentionally dropped -- the
        trailing stop locks in gains automatically; swap logic and EOD
        close handle opportunity exits.

        2026-08-22, user request: EVERY entry (not just 2nd+ same-day /
        post-loss-cooldown re-entries) now submits a TrailingStopOrderRequest
        -- it trails the adverse move (down for a long, up for a short) and
        only fires once price reverses REENTRY_TRAIL_PCT% off the extreme it
        reached, so an entry can't fill while the name is still actively
        falling (or squeezing, for a short). Replaces the old marketable-
        limit chase / passive-limit-faded-entry path entirely (see
        _marketable_limit_price, now unused here). It's a resting order the
        broker adjusts itself, so it bypasses _entry_pending/
        _sweep_pending_entries entirely -- nothing for that re-chase loop to
        do; _entry_pending now stays permanently empty.

        2026-08-18, user request (superseded by the above but kept for the
        PFSA incident this originally covered): REENTRY_TRAIL_PCT in
        config.py. See _validate_trade's docstring -- as of 2026-08-24 there's
        no post-loss cooldown left at all, just this trailing-buy entry plus
        the trailing stop / check_ema15_exit / standalone stop-loss exit stack."""
        side          = OrderSide.BUY  if order_type == OrderType.LONG else OrderSide.SELL
        stop_side     = OrderSide.SELL if order_type == OrderType.LONG else OrderSide.BUY
        trail_pct     = TRAIL_STOP_PCT  # flat -- see _trail_pct_for()
        is_long_entry = order_type == OrderType.LONG
        is_reentry    = self._is_reentry_signal(signal.symbol, is_long_entry)  # kept for logging only

        # ── Step 1: Entry order (failure aborts the whole bracket) ──────────
        try:
            entry_req = TrailingStopOrderRequest(
                symbol          = signal.symbol,
                qty             = shares,
                side            = side,
                type            = AlpacaOrderType.TRAILING_STOP,
                time_in_force   = TimeInForce.DAY,
                trail_percent   = REENTRY_TRAIL_PCT,
                client_order_id = f"apex-entry-{signal.strategy}-{signal.symbol}-{int(time.time())}",
            )
            order = self.client.submit_order(entry_req)
            self.order_cache[signal.symbol] = order.id
            # 2026-08-24, user request: capture (entry_price - ema15) now, as
            # the reference check_ema15_exit() compares every 1-min poll
            # against -- best-effort off signal.price (the resting trailing-
            # buy above hasn't necessarily filled at this exact price yet,
            # same approximation the rest of this bracket already uses for
            # risk_info). Never blocks the entry itself on a bars fetch failure.
            try:
                _bars = get_bars(signal.symbol, period="1d", interval="1m")
                if not _bars.empty and "close" in _bars.columns and len(_bars) >= EMA15_EXIT_MIN_BARS:
                    _ema15 = float(_bars["close"].ewm(span=15, adjust=False).mean().iloc[-1])
                    self._entry_ema15_delta[signal.symbol] = signal.price - _ema15
                    self._entry_ema15[signal.symbol] = _ema15
            except Exception as e:
                log.debug(f"{signal.symbol}: entry EMA15 delta capture failed (non-fatal): {e}")
            log.info(
                f"{signal.symbol}: {'re-entry' if is_reentry else 'entry'} -- trailing "
                f"{'BUY' if is_long_entry else 'SELL'} {REENTRY_TRAIL_PCT:.1f}% instead of chasing in"
            )
            self._entries_today[signal.symbol] = self._entries_today.get(signal.symbol, 0) + 1

            # Store ATR-based TP target — checked each scan cycle by check_tp_targets()
            if signal.atr_stop and signal.atr_stop > 0:
                _sl, _tp = self._calculate_bracket_prices(signal, risk_info, order_type)
                self._tp_targets[signal.symbol] = _tp
                log.info(f"TP target set {signal.symbol}: ${_tp:.2f} (ATR R:R {ATR_TP_RATIO}:1)")

        except Exception as e:
            err = str(e).lower()
            if order_type == OrderType.SHORT and ("cannot be sold short" in err or "40310000" in err or "account is not allowed to short" in err):
                self._handle_short_rejection(signal, e)
            elif order_type != OrderType.SHORT and ("cannot be sold short" in err or "40310000" in err):
                # Inverse ETF or other buy rejected by broker — do not poison short flag
                log.warning(f"Buy rejected for {signal.symbol} (broker): {e}")
            elif "insufficient buying power" in err:
                log.warning(f"Bracket skip {signal.symbol}: insufficient buying power")
            else:
                log.error(f"Bracket order failed {signal.symbol}: {e}")
            return False

        # ── Step 2: Trailing stop — best-effort; entry already filled ────────
        # On live accounts, skip the same-day trailing stop — PDT rules block GTC
        # SELL legs on shares entered today.  protect_positions() re-places it next
        # session when the position is no longer same-day restricted.
        # Inverse ETFs (SOXS, DUST, UVXY …) may also reject a GTC trailing stop
        # with 40310000.  This must NOT cancel the entry or disable shorting.
        if LIVE:
            log.info(
                f"Trailing stop deferred {signal.symbol} (live same-day entry) — "
                "protect_positions() will place it next session"
            )
        else:
            try:
                ts_req = TrailingStopOrderRequest(
                    symbol        = signal.symbol,
                    qty           = shares,
                    side          = stop_side,
                    type          = AlpacaOrderType.TRAILING_STOP,
                    time_in_force = TimeInForce.GTC,
                    trail_percent = trail_pct,
                )
                self.client.submit_order(ts_req)
            except Exception as e:
                log.warning(
                    f"Trailing stop skipped {signal.symbol} (entry filled): {e} — "
                    "protect_positions() will re-place next cycle"
                )

        self._log_bracket(signal, shares, risk_info, trail_pct, None, order_type)
        return True

    def _log_bracket(self, signal, shares, risk_info, trail_pct, _tp_unused, order_type):
        action    = "BUY"  if order_type == OrderType.LONG else "SHORT"
        tier      = risk_info["tier"]
        atr_pct   = risk_info.get("atr_pct", 0)
        alloc_pct = risk_info["allocation_pct"]

        if USE_DYNAMIC_TIERS and atr_pct > 0 and USE_RISK_EQUALIZED_SIZING:
            log.info(f"{action} {signal.symbol}: {shares} @ ${signal.price:.2f} submitted "
                     f"({alloc_pct:.1f}% pos) | TRAILING SL {trail_pct:.1f}% "
                     f"| Tier: {tier} (ATR {atr_pct:.1f}%) | {signal.strategy}")
        else:
            log.info(f"{action} {signal.symbol}: {shares} @ ${signal.price:.2f} submitted "
                     f"| TRAILING SL {trail_pct:.1f}% | Tier: {tier} | {signal.strategy}")

    # ΓöÇΓöÇ Simple Order ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    def _create_simple_order(self, signal: Signal, shares: int, order_type: OrderType) -> bool:
        """Non-bracket entry path. Always a bounded limit off the live bid/ask
        mid (see _marketable_limit_price/_live_quote_mid) -- regular hours
        used to be a plain MarketOrderRequest with no price bound, same
        NBIL-class risk _create_bracket_order had (see
        MARKETABLE_LIMIT_BUFFER_PCT in config.py).

        2026-08-18, user request: entries never use extended hours -- EXTENDED_HOURS
        now only governs exit paths (a stop-loss must be able to fire outside
        regular hours; a new position never needs to open outside them). In
        practice this is already unreachable -- ENTRY_WINDOW_START/END_ET now
        match regular hours -- but hardcoded here too rather than relying
        solely on that window (FORCE_SCAN bypasses it)."""
        side   = OrderSide.BUY if order_type == OrderType.LONG else OrderSide.SELL
        action = "BUY"         if order_type == OrderType.LONG else "SHORT"

        try:
            coid     = f"apex-{signal.strategy}-{signal.symbol}-{int(time.time())}"
            extended = False
            mid      = _live_quote_mid(self.client, signal.symbol, signal.price)
            limit    = _marketable_limit_price(mid, is_long=(order_type == OrderType.LONG))
            req = LimitOrderRequest(
                symbol          = signal.symbol,
                qty             = shares,
                side            = side,
                time_in_force   = TimeInForce.DAY,
                limit_price     = limit,
                extended_hours  = extended,
                client_order_id = coid,
            )
            order = self.client.submit_order(req)
            self.order_cache[signal.symbol] = order.id
            self._entry_pending[signal.symbol] = {
                "order_id": str(order.id), "qty": shares,
                "is_long": order_type == OrderType.LONG, "chase_count": 0,
            }
            log.info(
                f"{action} {signal.symbol}: {shares} @ ${limit:.2f} submitted"
                f"{' (ext-hours)' if extended else ''} | {signal.strategy}"
            )
            return True

        except Exception as e:
            err = str(e).lower()
            if order_type == OrderType.SHORT and ("cannot be sold short" in err or "40310000" in err or "account is not allowed to short" in err):
                self._handle_short_rejection(signal, e)
            elif order_type != OrderType.SHORT and ("cannot be sold short" in err or "40310000" in err):
                # Inverse ETF or other buy rejected by broker — do not poison short flag
                log.warning(f"Buy rejected for {signal.symbol} (broker): {e}")
            elif "insufficient buying power" in err:
                log.warning(f"Skip {signal.symbol}: insufficient buying power")
            else:
                log.error(f"{action} order error {signal.symbol}: {e}")
            return False

    # -- Entry (unified) ---------------------------------------------------
    def _execute_entry(self, signal: Signal, acct: AccountSnapshot, order_type: OrderType, swap_only: bool = False) -> bool:
        valid, reason = self._validate_trade(signal, acct, order_type, swap_only=swap_only)
        if not valid:
            if reason:
                log.info(f"Skip {signal.symbol}: {reason}")
            return False

        risk_info = calculate_risk_adjusted_size(acct.equity, signal.symbol, signal.price)

        # Scale dollar_amount by confidence: 0.50× at floor (MIN_SIGNAL_CONFIDENCE) → 1.0× at 0.85+
        from engine.config import MIN_SIGNAL_CONFIDENCE
        _conf_floor = MIN_SIGNAL_CONFIDENCE
        _conf_mult = CONF_SCALE_MIN_MULT + (1.0 - CONF_SCALE_MIN_MULT) * min(
            1.0, max(0.0, (signal.confidence - _conf_floor) / (CONF_SCALE_FULL_CONF - _conf_floor))
        )
        risk_info = dict(risk_info, dollar_amount=round(risk_info["dollar_amount"] * _conf_mult, 2))
        log.debug(
            f"[SIZE] {signal.symbol} conf={signal.confidence:.0%} → "
            f"scale={_conf_mult:.2f}× → ${risk_info['dollar_amount']:,.0f}"
        )

        _pre_bonus_pct = risk_info["allocation_pct"]
        risk_info = _apply_confidence_size_ramp(risk_info, signal.confidence, acct.equity)
        if risk_info["allocation_pct"] != _pre_bonus_pct:
            log.debug(
                f"[SIZE] {signal.symbol} conf={signal.confidence:.0%} > {CONF_SCALE_FULL_CONF:.0%} "
                f"— confidence size ramp: allocation {_pre_bonus_pct:.1f}% → {risk_info['allocation_pct']:.1f}% "
                f"(${risk_info['dollar_amount']:,.0f})"
            )

        _pre_kelly_pct = risk_info["allocation_pct"]
        risk_info = _apply_strategy_kelly_mult(risk_info, signal.strategy, acct.equity)
        if risk_info["allocation_pct"] != _pre_kelly_pct:
            log.debug(
                f"[SIZE] {signal.symbol} [{signal.strategy}] Kelly mult "
                f"{STRATEGY_KELLY_MULT.get(signal.strategy, STRATEGY_KELLY_MULT_DEFAULT):.2f}x: "
                f"allocation {_pre_kelly_pct:.1f}% → {risk_info['allocation_pct']:.1f}% "
                f"(${risk_info['dollar_amount']:,.0f})"
            )

        risk_info = _apply_thin_liquidity_override(risk_info, signal, acct.equity)

        shares, skip_reason = self._size_with_buying_power(acct.buying_power, signal, risk_info, order_type)
        if shares < 1:
            # Confidence-swap: if a held position has lower entry confidence, rotate into the new signal.
            # Skip entirely when PDT = 0 — closing a same-day position would itself be a day trade.
            _dt_left_swap = self.pdt.remaining(acct.equity, acct.daytrade_count)
            if order_type == OrderType.LONG and _dt_left_swap > 0:
                victim, victim_conf = self._find_least_confident_position(signal.confidence)
                if victim:
                    log.info(
                        f"CONF-SWAP: closing {victim} (conf={victim_conf:.0%}) "
                        f"to make room for {signal.symbol} (conf={signal.confidence:.0%})"
                    )
                    try:
                        # Same as _attempt_swap: victim's full qty is normally
                        # reserved by its own GTC trailing stop, so close_position()
                        # rejects with "insufficient qty available" unless that
                        # resting order is cancelled first (confirmed failing on
                        # every cycle in production before this — AMLX, 40310000).
                        try:
                            for o in (self.client.get_orders() or []):
                                if o.symbol == victim:
                                    self.client.cancel_order_by_id(str(o.id))
                                    time.sleep(0.4)
                        except Exception as cancel_err:
                            log.warning(f"CONF-SWAP {victim}: order cancel failed, close may reject: {cancel_err}")
                        self.client.close_position(victim)
                        self._swap_cycle_closed.add(victim)
                        # Do not count the close as a day trade (exits are always allowed)
                        acct = self._get_account(force_refresh=True)
                        shares, skip_reason = self._size_with_buying_power(acct.buying_power, signal, risk_info, order_type)
                    except Exception as e:
                        log.warning(f"Conf-swap close failed for {victim}: {e}")
            if shares < 1:
                log.info(f"Skip {signal.symbol}: {skip_reason}")
                return False

        # Short-float position cap: never exceed 20% of equity in a single squeeze ticker
        if is_high_short_float(signal.symbol):
            cap_shares = max(0, int(acct.equity * (MAX_SHORT_FLOAT_PCT / 100) / signal.price))
            if shares > cap_shares:
                log.info(
                    f"Short-float cap {signal.symbol}: {shares}→{cap_shares} shares "
                    f"({MAX_SHORT_FLOAT_PCT:.0f}% equity max, equity ${acct.equity:,.0f})"
                )
                shares = cap_shares
            if shares < 1:
                log.info(f"Skip {signal.symbol}: too small after short-float cap")
                return False

        if order_type == OrderType.SHORT and LONG_ONLY_MODE:
            log.info(f"Skipping {signal.symbol} SHORT because LONG_ONLY_MODE is active")
            return False

        if self.use_bracket_orders and self._current_market_state().is_regular_hours:
            if self._create_bracket_order(signal, shares, risk_info, order_type):
                self.pdt.add(datetime.date.today())
                self._entry_log[signal.symbol] = {"strategy": signal.strategy, "date": datetime.date.today(), "filled_at": datetime.datetime.now(datetime.timezone.utc), "confidence": signal.confidence, "thin_liquidity": signal.thin_liquidity}
                self._swap_cycle_closed.add(signal.symbol)  # protect from same-cycle swap-out
                self._get_positions(force_refresh=True)
                self._get_account(force_refresh=True)
                return True

        if self._create_simple_order(signal, shares, order_type):
            self.pdt.add(datetime.date.today())
            self._entry_log[signal.symbol] = {"strategy": signal.strategy, "date": datetime.date.today(), "confidence": signal.confidence, "thin_liquidity": signal.thin_liquidity}
            self._swap_cycle_closed.add(signal.symbol)  # protect from same-cycle swap-out
            self._get_positions(force_refresh=True)
            self._get_account(force_refresh=True)
            return True

        return False

    # -- Public: Execute ---------------------------------------------------
    def execute(self, signal: Signal, swap_only: bool = False) -> bool:
        if is_never_trade(signal.symbol):
            log.info(f"Skipping {signal.symbol}: listed in data/never_trade.txt")
            return False
        try:
            acct      = self._get_account()
            positions = self._get_positions()

            if signal.action == "buy":
                if positions.has_position(signal.symbol) and positions.is_short(signal.symbol):
                    return self._close_short_position(signal, acct.equity)
                return self._execute_entry(signal, acct, OrderType.LONG, swap_only=swap_only)

            elif signal.action in ("sell", "short"):
                if LONG_ONLY_MODE:
                    log.info(
                        f"Skipping {signal.symbol} {signal.action.upper()} because LONG_ONLY_MODE is enabled"
                    )
                    return False
                if self.shorting_blocked:
                    log.info(
                        f"Skipping {signal.symbol} {signal.action.upper()} because shorting is blocked for this account/session"
                    )
                    return False

                if positions.has_position(signal.symbol) and positions.is_long(signal.symbol):
                    return self._close_long_position(signal, acct.equity)
                return self._execute_entry(signal, acct, OrderType.SHORT, swap_only=swap_only)

        except Exception as e:
            log.error(f"Execute error {signal.symbol}: {e}")
        return False

    # ΓöÇΓöÇ Close Short ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    def _close_short_position(self, signal: Signal, equity: float) -> bool:
        positions = self._get_positions()
        if not positions.has_position(signal.symbol):
            log.info(f"No short position in {signal.symbol}")
            return False
        try:
            qty = abs(int(positions.positions_dict[signal.symbol].qty))
            if EXTENDED_HOURS and not self._current_market_state().is_regular_hours:
                req = LimitOrderRequest(
                    symbol=signal.symbol, qty=qty, side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(signal.price * 1.002, 2), extended_hours=True,
                )
            else:
                req = MarketOrderRequest(
                    symbol=signal.symbol, qty=qty, side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
            self.client.submit_order(req)
            # Closing a short that was opened today is a day trade round-trip
            self.pdt.add(datetime.date.today())
            log.info(f"COVER {signal.symbol}: {qty} @ ${signal.price:.2f} | {signal.strategy}")
            return True
        except Exception as e:
            log.error(f"Cover error {signal.symbol}: {e}")
            return False

    # ΓöÇΓöÇ Close Long ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
    def _close_long_position(self, signal: Signal, equity: float) -> bool:
        positions = self._get_positions()
        if not positions.has_position(signal.symbol):
            log.info(f"No position in {signal.symbol}")
            return False
        # Closes are ALWAYS allowed regardless of PDT — never block an exit

        qty = abs(int(float(positions.positions_dict[signal.symbol].qty)))
        try:
            # A plain MarketOrderRequest gets rejected outside regular hours — this
            # path (a strategy-driven "sell" signal on a held long) is reachable
            # any time scan_and_trade runs, which spans the full 07:00-20:00
            # is_market_open window, not just 09:30-16:00. Its sibling
            # _close_short_position already branches on regular-hours a few lines
            # above; this one didn't. _submit_closing_order handles both cases.
            self._submit_closing_order(signal.symbol, qty, OrderSide.SELL, signal.price)
            # NOTE: closing an existing position is NOT a new day trade.
            # Alpaca counts the round-trip (open+close same day) as one trade;
            # pdt.add() is intentionally omitted here — it was already counted at entry.
            self._get_positions(force_refresh=True)
            log.info(f"SELL {signal.symbol}: {qty} shares | {signal.strategy}")
            return True
        except Exception as e:
            log.error(f"Sell error {signal.symbol}: {e}")
            return False

    # ─── Protect Open Positions ──────────────────────────────────────────────
    def protect_positions(self) -> None:
        """
        For every open position whose shares are fully free (qty_available > 0
        AND no existing sell/buy-to-cover order on that symbol), place a GTC
        trailing stop.  Skips any position already covered by an active order.

        Covers today's entries too — if the bracket-order step-2 trailing stop
        was rejected by the broker (common for inverse ETFs), this re-places it
        so the position is never left naked intraday.  A GTC trailing stop that
        fills same-day will count as a day trade; the PDT violation alert in
        _validate_trade fires if the count exceeds PDT_MAX_TRADES.
        """
        positions = []
        covered = set()

        # Resist transient connection drops by retrying fetch operations.
        for attempt in range(1, 4):
            try:
                positions = self.client.get_all_positions()
                open_orders = self.client.get_orders()
                covered = {o.symbol for o in open_orders}
                break
            except Exception as e:
                log.warning(
                    f"protect_positions: data fetch attempt {attempt}/3 failed: {e}"
                )
                if attempt < 3:
                    time.sleep(2)
                else:
                    log.error("protect_positions: all fetch retries failed; skipping this cycle")
                    return

        for pos in positions:
            sym = pos.symbol

            # Skip options legs — OCC symbols (e.g. AEHR260515C00080000) are managed
            # by OptionsExecutor.monitor_positions(); trailing stops are invalid for options
            # (Alpaca error 42210000).  OCC symbols always match <ticker><YYMMDD><C|P><8digits>.
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue

            # Primary guard: don't add orders if symbol already has any active order
            if sym in covered:
                continue

            # Skip positions confirmed as forced overnight holds (PDT blocks close too)
            if sym in self._pdt_overnight_forced:
                continue

            # Secondary guard: skip if broker reports zero available qty. Alpaca
            # mirrors qty_available's sign to qty for shorts (a fully-free -26
            # share short reports qty_available=-26, not +26) — checking <= 0
            # is only correct for longs. Confirmed live 2026-08-12: every open
            # short (ACHR, CORZ, IREN, MARA, ONON, SE, WULF) has a negative
            # qty_available and was being skipped here every single cycle,
            # leaving the entire short book with zero trailing-stop protection.
            # 0 (not sign) is what actually means "fully reserved by another
            # order" on both sides, so that's the only case to skip.
            try:
                qty_available = int(float(pos.qty_available))
            except (AttributeError, TypeError, ValueError):
                qty_available = 0
            if qty_available == 0:
                continue

            try:
                qty         = int(float(pos.qty))
                avail       = abs(qty_available)
                current     = float(pos.current_price)
                is_long_pos = qty > 0
                try:
                    gain_pct = float(pos.unrealized_plpc) * 100.0
                except (TypeError, ValueError, AttributeError):
                    gain_pct = None

                trail_pct, tier_label = _trail_pct_for(sym, current, self._entry_log, gain_pct)

                stop_side = OrderSide.SELL if is_long_pos else OrderSide.BUY
                self.client.submit_order(TrailingStopOrderRequest(
                    symbol        = sym,
                    qty           = avail,
                    side          = stop_side,
                    type          = AlpacaOrderType.TRAILING_STOP,
                    time_in_force = TimeInForce.GTC,
                    trail_percent = trail_pct,
                ))
                direction = "LONG" if is_long_pos else "SHORT"
                log.info(f"PROTECT {direction} {sym} [{tier_label}]: trailing stop {trail_pct:.1f}% GTC")
                # A real broker-side GTC now covers this symbol -- drop any
                # software-stop fallback (from _cover_naked_positions or an
                # earlier 40310100 rejection) so check_software_stops() stops
                # watching a position that's already covered for real.
                self._pdt_stop_blocked.pop(sym, None)
            except Exception as e:
                err_str = str(e)
                if "40310100" in err_str:
                    # Broker PDT protection rejects the stop for today's entry.
                    # Fall back to software stop monitoring via check_software_stops().
                    if sym not in self._pdt_stop_blocked:
                        try:
                            entry_price = float(pos.avg_entry_price or pos.current_price)
                            stop_pct    = _trail_pct_for(sym, float(pos.current_price), self._entry_log)[0]
                            stop_price  = round(
                                entry_price * (1 - stop_pct / 100) if qty > 0
                                else entry_price * (1 + stop_pct / 100),
                                2,
                            )
                            self._pdt_stop_blocked[sym] = stop_price
                            log.warning(
                                f"protect_positions {sym}: broker PDT stop rejected — "
                                f"software SL set at ${stop_price:.2f} ({stop_pct:.1f}% from ${entry_price:.2f})"
                            )
                        except Exception:
                            log.warning(f"protect_positions {sym}: PDT stop rejected (software SL unavailable)")
                    else:
                        log.debug(f"protect_positions {sym}: PDT stop still rejected (software SL active @ ${self._pdt_stop_blocked[sym]:.2f})")
                else:
                    log.error(f"protect_positions {sym}: {e}")

    def ratchet_confident_winners(self) -> None:
        """Tighten the trailing stop on a position once it's up
        CONF_RATCHET_TRIGGER_GAIN_PCT or more, scaled by how confident the
        original entry signal was — a trade we were more sure about locks in
        its gain sooner instead of riding the full tier-width stop like every
        other trade. Runs once per position for its whole life (tracked via
        _ratchet_done); protect_positions() never revisits a symbol once it
        has a resting order, so this is the only place a stop gets replaced
        after the fact.

        Skips: positions still at/under their entry price, confidence at or
        below SWAP_MIN_CONFIDENCE (includes the 0.0 placeholder that
        _rebuild_entry_log_from_orders uses for positions restored after a
        bot restart — we don't actually know those were high-confidence, so
        never tighten them), and anything already ratcheted.
        """
        if not CONF_RATCHET_ENABLED:
            return
        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"ratchet_confident_winners: fetch failed: {e}")
            return

        for pos in positions:
            sym = pos.symbol
            if sym in self._ratchet_done:
                continue
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately
            confidence = self._entry_log.get(sym, {}).get("confidence", 0.0)
            if confidence <= SWAP_MIN_CONFIDENCE:
                continue
            try:
                qty      = int(float(pos.qty))
                gain_pct = float(pos.unrealized_plpc) * 100.0
            except (TypeError, ValueError):
                continue
            if qty == 0 or gain_pct < CONF_RATCHET_TRIGGER_GAIN_PCT:
                continue

            try:
                current  = float(pos.current_price)
                base_pct = _trail_pct_for(sym, current, self._entry_log)[0]
                tightened_pct = round(base_pct * ratchet_scale(confidence), 2)
                if tightened_pct >= base_pct:
                    self._ratchet_done.add(sym)  # nothing to tighten to; don't recheck every cycle
                    continue

                for o in (self.client.get_orders() or []):
                    if o.symbol == sym:
                        self.client.cancel_order_by_id(str(o.id))
                        time.sleep(0.4)

                stop_side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                self.client.submit_order(TrailingStopOrderRequest(
                    symbol        = sym,
                    qty           = abs(qty),
                    side          = stop_side,
                    type          = AlpacaOrderType.TRAILING_STOP,
                    time_in_force = TimeInForce.GTC,
                    trail_percent = tightened_pct,
                ))
                self._ratchet_done.add(sym)
                log.info(
                    f"RATCHET {sym}: +{gain_pct:.1f}% unrealized, entry conf={confidence:.0%} — "
                    f"trailing stop {base_pct:.1f}% -> {tightened_pct:.1f}%"
                )
            except Exception as e:
                log.warning(f"ratchet_confident_winners {sym}: {e}")

    def _submit_closing_order(
        self, symbol: str, qty: int, side: OrderSide, current_price: float,
        slip_pct: float = 0.5, force_extended_hours: bool = False,
        no_extended_hours: bool = False,
    ) -> None:
        """Submit a position-closing order as a marketable limit crossing the
        spread by slip_pct off the LIVE bid/ask mid (see _live_quote_mid) --
        never a naked MarketOrderRequest during regular hours either
        anymore: same unbounded-spread risk as the entry side (NBIL,
        MARKETABLE_LIMIT_BUFFER_PCT), just on the way out instead of in.
        extended_hours is set whenever we're actually outside regular hours,
        since Alpaca rejects market orders (and non-extended limits) then --
        force_extended_hours=True overrides that for callers submitted DURING
        regular hours that still need to survive past the close if unfilled.
        no_extended_hours=True is the opposite override -- 2026-08-18, user
        request: EOD/guardrail force-closes (_sweep_force_closes' regular-
        hours branch) must NEVER be extended_hours even if this call happens
        to land right at the regular/extended boundary and MarketState.from_now()
        has already flipped to after-hours by the time this fires; those two
        reasons aren't "price moved against the position" and don't get to
        trade in extended hours at all anymore (see _sweep_force_closes).
        Callers that keep missing the fill (fast-moving book) should widen
        slip_pct on retry rather than resubmitting at the same price forever."""
        mid  = _live_quote_mid(self.client, symbol, current_price)
        slip = (1.0 - slip_pct / 100.0) if side == OrderSide.SELL else (1.0 + slip_pct / 100.0)
        extended = False if no_extended_hours else (force_extended_hours or not MarketState.from_now().is_regular_hours)
        req = LimitOrderRequest(
            symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY,
            limit_price=round(mid * slip, 2),
            extended_hours=extended,
        )
        self.client.submit_order(req)

    def _cover_naked_positions(self) -> None:
        """Fast-thread companion to protect_positions(): the moment a
        position exists with no resting order and no software-stop coverage
        yet, register a software stop off the REAL pos.avg_entry_price --
        pure bookkeeping (a dict write), never a broker order attempt. The
        actual broker-side GTC attempt frequency is untouched, still only on
        protect_positions()'s normal (slower) cadence -- this can't turn
        into a submit_order retry storm for a genuinely PDT-blocked symbol.

        2026-08-17, CDTG: the bracket-order trailing stop is deliberately
        deferred for every live same-day entry (PDT), and protect_positions()
        only runs on the adaptive scan cadence -- CDTG sat with literally
        zero stop coverage for 3+ minutes while it fell from $2.97 to $2.73,
        and by the time a stop was finally armed it anchored to the already-
        fallen price instead of the entry. Called from the same 10s thread
        as check_software_stops(), which is what actually watches and closes
        whatever this registers -- see that method for the poll side."""
        try:
            positions   = self.client.get_all_positions()
            open_orders = self.client.get_orders()
            covered     = {o.symbol for o in open_orders}
        except Exception as e:
            log.warning(f"_cover_naked_positions: fetch failed: {e}")
            return
        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately
            if sym in covered or sym in self._pdt_stop_blocked:
                continue
            try:
                qty = int(float(pos.qty))
                if qty == 0:
                    continue
                entry_price = float(pos.avg_entry_price or pos.current_price)
                stop_pct    = _trail_pct_for(sym, float(pos.current_price), self._entry_log)[0]
                stop_price  = round(
                    entry_price * (1 - stop_pct / 100) if qty > 0
                    else entry_price * (1 + stop_pct / 100),
                    2,
                )
                self._pdt_stop_blocked[sym] = stop_price
                log.warning(
                    f"_cover_naked_positions {sym}: no order coverage yet — "
                    f"software SL set at ${stop_price:.2f} ({stop_pct:.1f}% from ${entry_price:.2f})"
                )
            except Exception:
                continue  # best-effort — next 10s tick tries again

    def check_software_stops(self) -> None:
        """Close any position whose broker-rejected PDT stop has been breached.
        Called every scan cycle for positions in _pdt_stop_blocked."""
        if not self._pdt_stop_blocked:
            return
        try:
            positions = {p.symbol: p for p in self.client.get_all_positions()}
        except Exception as e:
            log.warning(f"check_software_stops: fetch failed: {e}")
            return
        for sym, stop_price in list(self._pdt_stop_blocked.items()):
            pos = positions.get(sym)
            if pos is None:
                # Position already closed (stop filled or manual)
                self._pdt_stop_blocked.pop(sym, None)
                continue
            try:
                current = float(pos.current_price)
                qty     = int(float(pos.qty))
                is_long = qty > 0
                hit     = (is_long and current <= stop_price) or (not is_long and current >= stop_price)
                if hit:
                    side = OrderSide.SELL if is_long else OrderSide.BUY
                    try:
                        self._submit_closing_order(sym, abs(qty), side, current)
                        self._pdt_stop_blocked.pop(sym, None)
                        log.warning(
                            f"SOFTWARE SL HIT {sym}: price ${current:.2f} crossed stop ${stop_price:.2f} — "
                            f"{'SELL' if is_long else 'BUY-TO-COVER'} submitted"
                        )
                    except Exception as close_err:
                        if "40310100" in str(close_err):
                            # Broker PDT also blocks same-day close — position is a forced
                            # overnight hold.  Stop retrying; it will carry to next session.
                            self._pdt_stop_blocked.pop(sym, None)
                            self._pdt_overnight_forced.add(sym)
                            log.warning(
                                f"SOFTWARE SL {sym}: stop breached at ${current:.2f} but PDT blocks "
                                f"same-day close — holding overnight (stop was ${stop_price:.2f})"
                            )
                        else:
                            log.error(f"check_software_stops {sym}: {close_err}")
                else:
                    log.debug(f"SOFTWARE SL {sym}: current ${current:.2f} | stop ${stop_price:.2f} | margin ${current - stop_price:+.2f}")
            except Exception as e:
                log.error(f"check_software_stops {sym}: {e}")

    def detect_stopped_out_positions(self) -> None:
        """Catch a position closing via ANY route — most commonly a normal
        broker-side GTC trailing stop filling on its own — and reset its
        ratchet-tightening state so a later re-entry gets fresh confidence-
        ratchet protection instead of none (see the _ratchet_done.discard
        call below).

        2026-08-24, user request: this used to also arm a post-loss re-entry
        cooldown here (and in check_afterhours_stops()'s own close path) —
        removed. No cooldown left anywhere; the exit stack (trailing stop,
        per-minute check_ema15_exit, standalone stop-loss) is the only
        protection now.
        """
        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"detect_stopped_out_positions: fetch failed: {e}")
            return

        current: Dict[str, dict] = {}
        for p in positions:
            sym = p.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately
            try:
                current[sym] = {
                    "entry_price": float(p.avg_entry_price),
                    "last_price": float(p.current_price),
                    "is_long": float(p.qty) > 0,
                }
            except (TypeError, ValueError):
                continue

        for sym in self._last_known_positions:
            if sym in current:
                continue  # still open
            # Closed via any route — eligible for confidence-ratchet protection
            # again next time it's re-entered (was symbol-keyed and add-only,
            # so a re-entry after an earlier ratcheted win got none — 2026-08-10:
            # ABCL ran +6.3% unrealized on a fresh entry after an earlier lot had
            # already ratcheted, and never got tightened).
            self._ratchet_done.discard(sym)
            # 2026-08-24, user request: clear both entry-anchored EMA15
            # references too -- a stale reference from the lot that just
            # closed must never get compared against a later, differently-
            # priced re-entry.
            self._entry_ema15_delta.pop(sym, None)
            self._entry_ema15.pop(sym, None)
            self._reclaimed_ema15.discard(sym)

        self._last_known_positions = current

    def check_afterhours_stops(self) -> None:
        """Actively watch every open position's loss while the market is NOT in
        regular hours — the broker-side GTC trailing stop from protect_positions()
        sits inert outside 09:30-16:00 ET, so a position can free-fall pre-market
        or after-hours with no protection until regular hours resume. Uses a flat
        stop from avg_entry_price at the same trail % as the resting trailing
        stop (not a true trailing high-water-mark — good enough for a software
        backstop). Skips symbols already handled by check_software_stops to
        avoid double-submitting a close. Meant to be polled frequently (the
        10s software-stop thread) since after-hours moves can be sharp.

        The resting GTC trailing stop reserves the position's qty, so Alpaca
        won't accept a replacement close order while it's still open — it's
        cancelled up front, deterministically, rather than waiting to see if
        the close gets rejected. If the close then fails for any reason, a
        fresh GTC trailing stop is immediately re-armed as a fallback so the
        position is never left with zero protection. If a submitted close
        sits unfilled past AFTERHOURS_CHASE_STALE_SECONDS, it's cancelled and
        re-submitted at a fresh marketable price to make sure it actually
        executes."""
        if not AFTERHOURS_STOP_CHECK_ENABLED:
            return
        if MarketState.from_now().is_regular_hours:
            return

        now_utc = datetime.datetime.now(datetime.timezone.utc)

        try:
            positions   = self.client.get_all_positions()
            open_orders = self.client.get_orders()
        except Exception as e:
            log.warning(f"check_afterhours_stops: fetch failed: {e}")
            return

        # Position closed since the last poll — its re-chase count is stale, drop it
        # so a future breach of the same symbol starts back at the base slip.
        _live_syms = {p.symbol for p in positions}
        for _sym in [s for s in self._afterhours_chase_count if s not in _live_syms]:
            self._afterhours_chase_count.pop(_sym, None)

        pending_by_sym: Dict[str, object] = {}  # symbol -> resting non-GTC order (a close already in flight)
        gtc_orders: Dict[str, str] = {}          # symbol -> GTC trailing-stop order id
        for o in open_orders:
            if getattr(o, "time_in_force", None) == TimeInForce.GTC:
                gtc_orders[o.symbol] = o.id
            else:
                pending_by_sym[o.symbol] = o

        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately
            if sym in self._pdt_stop_blocked:
                continue
            try:
                qty = int(float(pos.qty))
                if qty == 0:
                    continue
                is_long = qty > 0
                current = float(pos.current_price)
                entry   = float(pos.avg_entry_price)
                trail_pct  = _trail_pct_for(sym, current, self._entry_log)[0]
                stop_price = entry * (1 - trail_pct / 100) if is_long else entry * (1 + trail_pct / 100)
                hit = (is_long and current <= stop_price) or (not is_long and current >= stop_price)
                if not hit:
                    continue
                side = OrderSide.SELL if is_long else OrderSide.BUY

                existing = pending_by_sym.get(sym)
                if existing is not None:
                    submitted_at = getattr(existing, "submitted_at", None) or getattr(existing, "created_at", None)
                    age_s = (now_utc - submitted_at).total_seconds() if submitted_at else 0.0
                    if age_s < AFTERHOURS_CHASE_STALE_SECONDS:
                        continue  # close already in flight — give it time to fill
                    try:
                        self.client.cancel_order_by_id(str(existing.id))
                        time.sleep(0.4)
                    except Exception as e:
                        log.warning(f"check_afterhours_stops {sym}: stale-close cancel failed, will retry next poll: {e}")
                        continue
                    log.warning(f"AFTER-HOURS SL {sym}: prior close unfilled after {age_s:.0f}s — re-chasing at fresh price")
                else:
                    # First attempt for this breach: the resting GTC trailing
                    # stop reserves the qty, so it must go before Alpaca will
                    # accept a replacement close order after-hours.
                    gtc_id = gtc_orders.get(sym)
                    if gtc_id:
                        try:
                            self.client.cancel_order_by_id(str(gtc_id))
                            time.sleep(0.4)
                        except Exception as cancel_err:
                            log.warning(f"check_afterhours_stops {sym}: GTC cancel failed, will retry next poll: {cancel_err}")
                            continue

                try:
                    chase_n  = self._afterhours_chase_count.get(sym, 0)
                    slip_pct = min(0.5 * (chase_n + 1), 3.0)  # widen 0.5% -> 1.0% -> ... capped at 3% so a fast-falling book still fills
                    self._submit_closing_order(sym, abs(qty), side, current, slip_pct=slip_pct)
                    self._afterhours_chase_count[sym] = chase_n + 1
                    _strategy = self._entry_log.get(sym, {}).get("strategy", "unknown")
                    _pnl = (current - entry) * qty
                    log.warning(
                        f"AFTER-HOURS SL HIT {sym} [{_strategy}]: price ${current:.2f} crossed stop ${stop_price:.2f} "
                        f"({trail_pct:.1f}% from entry ${entry:.2f}) | P&L ${_pnl:+,.2f} — extended-hours "
                        f"{'SELL' if is_long else 'BUY-TO-COVER'} submitted @ {slip_pct:.1f}% slip "
                        f"(attempt {chase_n + 1})"
                    )
                except Exception as close_err:
                    log.error(f"AFTER-HOURS SL {sym}: close order failed after GTC cancel: {close_err}")
                    # GTC is gone and the replacement didn't go through — without
                    # a fallback the position would sit fully unprotected until
                    # the next protect_positions() cycle. Re-arm one now.
                    try:
                        self.client.submit_order(TrailingStopOrderRequest(
                            symbol        = sym,
                            qty           = abs(qty),
                            side          = OrderSide.SELL if is_long else OrderSide.BUY,
                            type          = AlpacaOrderType.TRAILING_STOP,
                            time_in_force = TimeInForce.GTC,
                            trail_percent = trail_pct,
                        ))
                        log.warning(f"AFTER-HOURS SL {sym}: re-armed GTC trailing stop as fallback after failed close")
                    except Exception as rearm_err:
                        log.error(f"AFTER-HOURS SL {sym}: close failed AND GTC re-arm failed — position may be UNPROTECTED: {rearm_err}")
            except Exception as e:
                log.error(f"check_afterhours_stops {sym}: {e}")

    # ── Position Concentration Cap ───────────────────────────────────────────
    @staticmethod
    def _effective_concentration_cap_pct(gain_pct: float) -> float:
        """2026-08-17, user request: "maximum holding as 20% of the
        portfolio value and growing based on the continued positive
        returns". A losing/flat position (gain_pct <= 0) keeps the plain
        MAX_POSITION_CONCENTRATION_PCT (20%) cap. A winning position's cap
        grows with its gain -- POSITION_CAP_GROWTH_FACTOR points of extra
        room per point of unrealized gain -- up to
        POSITION_CAP_ABSOLUTE_MAX_PCT (35%), never below the 20% base."""
        bonus = max(0.0, gain_pct) * POSITION_CAP_GROWTH_FACTOR
        return min(MAX_POSITION_CONCENTRATION_PCT + bonus, POSITION_CAP_ABSOLUTE_MAX_PCT)

    def enforce_position_concentration(self) -> None:
        """Trim any position whose market value exceeds its effective
        concentration cap (see _effective_concentration_cap_pct — the base
        MAX_POSITION_CONCENTRATION_PCT for a losing/flat position, growing
        room for a winner). Entry sizing caps new buys at the plain base
        cap (see _size_with_buying_power -- a brand-new position has no
        gain yet to grow from), but an existing winner can drift past its
        (possibly wider) cap through further price appreciation — this is
        the backstop for that case."""
        try:
            acct = self._get_account()
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"enforce_position_concentration: fetch failed: {e}")
            return
        if acct.equity <= 0:
            return
        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — sized/managed separately
            qty = int(float(pos.qty))
            if qty == 0:
                continue
            try:
                gain_pct = float(pos.unrealized_plpc) * 100.0
            except (TypeError, ValueError, AttributeError):
                gain_pct = 0.0
            cap_pct   = self._effective_concentration_cap_pct(gain_pct)
            cap_value = acct.equity * cap_pct / 100.0
            market_value = abs(float(pos.market_value))
            if market_value <= cap_value:
                continue
            current = float(pos.current_price)
            trim_qty = int((market_value - cap_value) / current)
            if trim_qty < 1:
                continue
            side = OrderSide.SELL if qty > 0 else OrderSide.BUY  # BUY-to-cover trims a short
            try:
                self._free_shares_for_trim(sym, trim_qty)
                self._submit_closing_order(sym, trim_qty, side, current)
                log.warning(
                    f"CONCENTRATION TRIM {sym}: {trim_qty} shares — ${market_value:,.0f} was "
                    f"{market_value / acct.equity:.0%} of equity, cap {cap_pct:.0f}% "
                    f"(gain {gain_pct:+.1f}%)"
                )
            except Exception as e:
                log.error(f"enforce_position_concentration {sym}: trim failed: {e}")

    def _free_shares_for_trim(self, symbol: str, trim_qty: int) -> None:
        """Shrink symbol's resting GTC protective stop by trim_qty shares
        BEFORE a trim/close order for that qty gets submitted.

        Alpaca reserves a position's ENTIRE quantity against any open order
        on it, so a second, competing order for even part of the position
        always fails with "insufficient qty available" while a full-qty
        stop rests -- confirmed live, 2026-08-17: TTD's concentration trim
        failed exactly this way every ~10 min for 6+ hours straight (0/36
        attempts succeeded, see enforce_position_concentration's ERROR log),
        because its 8% GTC trailing stop held all 64 shares.

        Resizing the stop down first, instead of cancelling and re-arming
        it, means the position is never left without stop coverage for even
        an instant -- there's no gap where a fast move has nothing resting
        to catch it. Deliberately conservative: no-op (the caller's trim is
        then left to fail on its own, same as before this fix existed) if
        no matching resting order is found, or if shrinking it would leave
        less than 1 share of coverage. Never touches price/trail, never
        cancels -- qty only."""
        try:
            orders = self.client.get_orders() or []
        except Exception as e:
            log.warning(f"_free_shares_for_trim {symbol}: order fetch failed: {e}")
            return
        stop_order = next(
            (o for o in orders if o.symbol == symbol and getattr(o, "time_in_force", None) == TimeInForce.GTC),
            None,
        )
        if stop_order is None:
            return  # nothing resting to shrink -- let the trim attempt itself surface any issue
        stop_qty = int(float(stop_order.qty))
        new_qty = stop_qty - trim_qty
        if new_qty < 1:
            return  # would zero out (or invert) the stop's coverage -- leave it alone
        self.client.replace_order_by_id(str(stop_order.id), ReplaceOrderRequest(qty=new_qty))
        time.sleep(0.4)  # let the reduced hold register before the trim order competes for the freed shares

    def enforce_correlation_concentration(self) -> None:
        """Trim a correlated basket (e.g. leveraged inverse-market ETFs) whose
        COMBINED market value exceeds that group's cap. enforce_position_concentration
        can't catch this: several different tickers that move together can each
        stay under MAX_POSITION_CONCENTRATION_PCT individually while adding up to
        one oversized directional bet combined (confirmed in production:
        SQQQ+SOXS+TZA+LABD held simultaneously on 2026-07-30)."""
        if not CORRELATION_GROUPS:
            return
        try:
            acct = self._get_account()
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"enforce_correlation_concentration: fetch failed: {e}")
            return
        if acct.equity <= 0:
            return

        for group_name, group in CORRELATION_GROUPS.items():
            members = group["symbols"]
            group_positions = [
                p for p in positions
                if p.symbol in members and int(float(p.qty)) != 0
            ]
            if not group_positions:
                continue

            total_value = sum(abs(float(p.market_value)) for p in group_positions)
            cap_value = acct.equity * group["max_pct"] / 100.0
            if total_value <= cap_value:
                continue

            excess = total_value - cap_value
            # Trim largest positions first — fewer orders, and it's the biggest
            # single contributor to the breach.
            for pos in sorted(group_positions, key=lambda p: abs(float(p.market_value)), reverse=True):
                if excess <= 0:
                    break
                sym = pos.symbol
                qty = int(float(pos.qty))
                current = float(pos.current_price)
                pos_value = abs(float(pos.market_value))
                trim_qty = int(min(excess, pos_value) / current)
                if trim_qty < 1:
                    continue
                side = OrderSide.SELL if qty > 0 else OrderSide.BUY  # BUY-to-cover trims a short
                try:
                    self._submit_closing_order(sym, trim_qty, side, current)
                    log.warning(
                        f"CORRELATION TRIM [{group_name}] {sym}: {trim_qty} shares — group was "
                        f"${total_value:,.0f} ({total_value / acct.equity:.0%} of equity), cap {group['max_pct']:.0f}%"
                    )
                    excess -= trim_qty * current
                except Exception as e:
                    log.error(f"enforce_correlation_concentration {sym}: trim failed: {e}")

    def enforce_portfolio_leverage(self) -> None:
        """Trim the largest position(s) if TOTAL market value across every
        open position exceeds MAX_PORTFOLIO_LEVERAGE x equity. 2026-08-17,
        user request: cap total exposure independent of whatever margin the
        broker's buying_power would otherwise allow -- _size_with_buying_power
        already blocks a NEW entry from pushing total exposure past this cap;
        this is the backstop for the book drifting over it through price
        appreciation alone on positions already held (same relationship
        enforce_position_concentration has to per-symbol sizing)."""
        try:
            acct = self._get_account()
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"enforce_portfolio_leverage: fetch failed: {e}")
            return
        if acct.equity <= 0:
            return

        equity_positions = [
            p for p in positions
            if int(float(p.qty)) != 0 and not re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', p.symbol)
        ]
        if not equity_positions:
            return

        total_value = sum(abs(float(p.market_value)) for p in equity_positions)
        cap_value = acct.equity * MAX_PORTFOLIO_LEVERAGE
        if total_value <= cap_value:
            return

        excess = total_value - cap_value
        # Trim largest positions first — fewest orders, biggest single
        # contributor to the breach comes down fastest.
        for pos in sorted(equity_positions, key=lambda p: abs(float(p.market_value)), reverse=True):
            if excess <= 0:
                break
            sym = pos.symbol
            qty = int(float(pos.qty))
            current = float(pos.current_price)
            pos_value = abs(float(pos.market_value))
            trim_qty = int(min(excess, pos_value) / current)
            if trim_qty < 1:
                continue
            side = OrderSide.SELL if qty > 0 else OrderSide.BUY  # BUY-to-cover trims a short
            try:
                self._submit_closing_order(sym, trim_qty, side, current)
                log.warning(
                    f"PORTFOLIO LEVERAGE TRIM {sym}: {trim_qty} shares — book was "
                    f"${total_value:,.0f} ({total_value / acct.equity:.1f}x equity), cap {MAX_PORTFOLIO_LEVERAGE:.1f}x"
                )
                excess -= trim_qty * current
            except Exception as e:
                log.error(f"enforce_portfolio_leverage {sym}: trim failed: {e}")

    # ── EOD Close ─────────────────────────────────────────────────────────────
    @staticmethod
    def _guardrail_fail_reason(
        avg_daily_vol: Optional[float], shares_float: Optional[float], market_cap: Optional[float]
    ) -> Optional[str]:
        """Pure decision logic for close_guardrail_fail_positions: return the
        reason string if any known metric is below its guardrail, else None
        (passes, or all three are unavailable — missing data never forces a
        close). Split out from the method below so it's unit-testable without
        a broker connection."""
        if avg_daily_vol is not None and avg_daily_vol < MIN_AVG_DAILY_VOLUME_REGULAR_HOURS:
            return f"avg_volume {avg_daily_vol:.0f} < {MIN_AVG_DAILY_VOLUME_REGULAR_HOURS:.0f}"
        if shares_float is not None and shares_float < MIN_FLOAT_SHARES:
            return f"float {shares_float/1e6:.1f}M < {MIN_FLOAT_SHARES/1e6:.0f}M"
        if market_cap is not None and market_cap < MIN_MARKET_CAP:
            return f"mcap ${market_cap/1e6:.0f}M < ${MIN_MARKET_CAP/1e6:.0f}M"
        return None

    def close_eod_positions(self) -> Optional[dict]:
        """Close every same-day position at EOD_CLOSE_TIME, regardless of
        strategy.

        2026-08-24, user request ("I wouldn't expect any positions to stay
        active at 3:50pm ET" / "don't leave it for trail order"): dropped
        the EOD_CLOSE_STRATEGIES allow-list gate. Confirmed live: SPXU
        (Technical) and WULF (LiquiditySweep) both sat open past 15:50 ET
        because neither strategy was on that list -- close_eod_positions
        logged "EOD email skipped" every minute without ever attempting
        either close, and both only closed ~15 min after the 16:00 ET
        market close via the passive after-hours stop instead. The
        strategy list dates to a narrower 2026-08-22 version of this
        function and was never kept in sync as new strategies (Technical,
        LiquiditySweep, TrendBreaker, ...) were added -- rather than keep
        patching that list, EOD close now just means every same-day
        position, no allow-list to fall out of date again. A multi-day
        swing hold is still out of scope (gated by the same-day-entry
        check below, unchanged). close_guardrail_fail_positions is the
        other half of the overnight picture but is itself disabled (see
        GUARDRAIL_EOD_CLOSE_ENABLED in config.py).

        2026-08-17, user request: runs every minute through the window
        (schedule.every(1).minutes) rather than once per day -- the old
        once-per-day flag meant a position opened AFTER the first post-
        15:45 tick (ASST/NUAI, opened 15:57 ET) had already missed its
        only chance to be flattened. Safe to call repeatedly: a symbol
        already closed has its _entry_log entry popped below, so the next
        tick's `if not entry_info: continue` is a no-op for it -- only
        newly-opened, not-yet-processed positions actually submit an order."""
        if not EOD_CLOSE_ENABLED:
            return None

        import pytz
        now_et = datetime.datetime.now(pytz.timezone("America/New_York"))
        close_h, close_m = map(int, EOD_CLOSE_TIME.split(":"))
        if now_et.hour < close_h or (now_et.hour == close_h and now_et.minute < close_m):
            return None  # Not yet EOD close time
        if now_et.hour >= 16:
            return None  # Market already closed

        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.error(f"close_eod_positions: fetch failed: {e}")
            return None

        today = datetime.date.today()
        closed_items = []
        failed_items = []

        for pos in positions:
            sym = pos.symbol
            qty = int(float(pos.qty))
            if qty == 0:
                continue

            entry_info = self._entry_log.get(sym)
            if not entry_info:
                continue
            if entry_info.get("date") != today:
                continue

            try:
                # Cancel only DAY-TIF orders holding shares for this symbol before
                # submitting the market close ("insufficient qty available" error).
                # GTC trailing stops are intentionally preserved — they protect the
                # position until the close fill settles and should not be cancelled.
                try:
                    sym_orders = [
                        o for o in (self.client.get_orders() or [])
                        if o.symbol == sym
                        and getattr(o, "time_in_force", None) != TimeInForce.GTC
                    ]
                    for _o in sym_orders:
                        try:
                            self.client.cancel_order_by_id(str(_o.id))
                        except Exception:
                            pass
                    if sym_orders:
                        time.sleep(0.4)
                except Exception:
                    pass

                side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                # 2026-08-18, user request: no force_extended_hours -- EOD closes
                # never chase into after-hours anymore. Unfilled by the 16:00 close,
                # this expires worthless and the position carries overnight under its
                # existing GTC trailing stop; _sweep_force_closes (below) gives up
                # the same way once regular hours end instead of re-chasing.
                self._submit_closing_order(sym, abs(qty), side, float(pos.current_price), no_extended_hours=True)
                self._entry_log.pop(sym, None)
                self._force_close_pending[sym] = {"reason": f"eod:{entry_info.get('strategy', 'unknown')}", "chase_count": 0}

                pnl = float(pos.unrealized_pl)
                closed_items.append({
                    "symbol": sym,
                    "qty": abs(qty),
                    "strategy": entry_info.get("strategy", "unknown"),
                    "pnl": pnl,
                })

                log.info(
                    f"EOD CLOSE {sym}: {abs(qty)} shares | "
                    f"strategy={entry_info.get('strategy', 'unknown')} | P&L ${pnl:.2f}"
                )
            except Exception as e:
                failed_items.append({"symbol": sym, "error": str(e)})
                log.error(f"EOD close failed {sym}: {e}")

        summary = {
            "date": today.isoformat(),
            "closed_count": len(closed_items),
            "failed_count": len(failed_items),
            "closed_items": closed_items,
            "failed_items": failed_items,
            "asof": now_et.isoformat(),
        }
        return summary

    def close_guardrail_fail_positions(self) -> Optional[dict]:
        """5 min before close, force-close any open position (any strategy —
        unlike close_eod_positions, not limited to EOD_CLOSE_STRATEGIES) that
        currently fails the standard liquidity/quality guardrails: avg daily
        volume, float shares, or market cap. Only guardrail-passing names get
        held after-hours/overnight.

        2026-08-17, user request: runs every minute through the window
        (schedule.every(1).minutes) instead of once per day -- the old
        once-per-day flag meant a position opened AFTER the first post-
        close-time tick (ASST/NUAI, opened 15:57 ET) had already missed its
        only chance to be checked. Unlike close_eod_positions, this function
        has no natural per-symbol idempotency marker (nothing it pops on
        success), so _guardrail_eod_closed tracks which symbols already had
        a close attempted today -- without it, a reruns-every-minute version
        would re-cancel and resubmit a still-unfilled close order on the
        same illiquid symbol every single minute instead of leaving it to
        _sweep_force_closes's own re-chase cadence."""
        if not GUARDRAIL_EOD_CLOSE_ENABLED:
            return None

        import pytz
        now_et = datetime.datetime.now(pytz.timezone("America/New_York"))
        close_h, close_m = map(int, GUARDRAIL_EOD_CLOSE_TIME.split(":"))
        if now_et.hour < close_h or (now_et.hour == close_h and now_et.minute < close_m):
            return None  # Not yet the guardrail close time
        if now_et.hour >= 16:
            return None  # Market already closed

        today = datetime.date.today()
        already_closed = self._guardrail_eod_closed.setdefault(today, set())
        self._guardrail_eod_closed = {today: already_closed}  # drop any stale prior-day entries

        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.error(f"close_guardrail_fail_positions: fetch failed: {e}")
            return None

        closed_items = []
        failed_items = []

        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately

            qty = int(float(pos.qty))
            if qty == 0:
                continue
            if sym in already_closed:
                continue  # close already attempted today -- _sweep_force_closes chases it if still unfilled

            try:
                daily = get_daily_volume_bars(sym)
                avg_daily_vol = (
                    float(daily["volume"].iloc[:-1].mean())
                    if not daily.empty and len(daily) >= 2 else None
                )
            except Exception as e:
                log.warning(f"close_guardrail_fail_positions {sym}: volume lookup failed: {e}")
                avg_daily_vol = None
            shares_float = _get_float_shares(sym)
            market_cap   = _get_market_cap(sym)

            fail_reason = self._guardrail_fail_reason(avg_daily_vol, shares_float, market_cap)
            if fail_reason is None:
                continue  # passes guardrails (or data unavailable) — fine to hold overnight

            try:
                sym_orders = [o for o in (self.client.get_orders() or []) if o.symbol == sym]
                for _o in sym_orders:
                    try:
                        self.client.cancel_order_by_id(str(_o.id))
                    except Exception:
                        pass
                if sym_orders:
                    time.sleep(0.4)
            except Exception as e:
                log.warning(f"close_guardrail_fail_positions {sym}: order fetch/cancel failed, will retry next cycle: {e}")
                continue

            close_side = OrderSide.SELL if qty > 0 else OrderSide.BUY
            try:
                # 2026-08-18, user request: same as close_eod_positions -- no
                # force_extended_hours, no after-hours chase. Carries overnight
                # under its GTC trailing stop if unfilled by the 16:00 close.
                self._submit_closing_order(sym, abs(qty), close_side, float(pos.current_price), no_extended_hours=True)
                self._force_close_pending[sym] = {"reason": f"guardrail:{fail_reason}", "chase_count": 0}
                already_closed.add(sym)
                pnl = float(pos.unrealized_pl)
                closed_items.append({"symbol": sym, "qty": abs(qty), "reason": fail_reason, "pnl": pnl})
                log.info(f"GUARDRAIL EOD CLOSE {sym}: {abs(qty)} shares | {fail_reason} | P&L ${pnl:.2f}")
            except Exception as e:
                failed_items.append({"symbol": sym, "error": str(e)})
                log.error(f"GUARDRAIL EOD CLOSE failed {sym}: {e}")

        summary = {
            "date": today.isoformat(),
            "closed_count": len(closed_items),
            "failed_count": len(failed_items),
            "closed_items": closed_items,
            "failed_items": failed_items,
            "asof": now_et.isoformat(),
        }
        return summary

    def _sweep_force_closes(self) -> None:
        """Poll every symbol close_eod_positions / close_guardrail_fail_positions
        submitted a close for but hasn't confirmed flat yet (self._force_close_pending).
        A single limit order can miss its fill -- price drifted past the limit, or
        it was still resting when the regular/extended session boundary hit --
        without this the position would just sit open, silently surviving the
        force-close it was supposed to get. Re-chases with a fresh live-bid/ask
        limit at escalating slip (same shape as check_afterhours_stops) until
        it's actually flat. Meant to be polled frequently (the 10s software-stop
        thread) so it catches a stale order quickly.
        ponytail: no cap on total re-chase attempts within regular hours (only
        slip% is capped, at 3%) -- a genuinely halted/no-bid symbol would retry
        indefinitely. Add a max-attempts giveup (with an alert) if that's ever
        observed live.

        2026-08-18, user request: the only two reasons that land in
        _force_close_pending (close_eod_positions/close_guardrail_fail_positions,
        "eod:..."/"guardrail:...") are deadline/liquidity driven, not "price
        moved against the position" -- so neither chases into extended hours
        anymore. Gives up the instant regular hours end and leaves the position
        to carry overnight under its GTC trailing stop; check_afterhours_stops
        is the only path allowed to actually exit a position outside regular
        hours."""
        if not self._force_close_pending:
            return
        try:
            positions   = {p.symbol: p for p in self.client.get_all_positions()}
            open_orders = self.client.get_orders() or []
        except Exception as e:
            log.warning(f"_sweep_force_closes: fetch failed: {e}")
            return

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        orders_by_sym: Dict[str, list] = {}
        for o in open_orders:
            orders_by_sym.setdefault(o.symbol, []).append(o)

        for sym, info in list(self._force_close_pending.items()):
            pos = positions.get(sym)
            qty = int(float(pos.qty)) if pos is not None else 0
            if pos is None or qty == 0:
                self._force_close_pending.pop(sym, None)  # confirmed flat
                continue

            sym_orders = orders_by_sym.get(sym, [])

            if not self._current_market_state().is_regular_hours:
                # Give up -- EOD/guardrail closes don't chase into extended hours
                # (see docstring). Carries overnight under its GTC trailing stop,
                # re-arming one only if a still-regular-hours chase attempt above
                # already cancelled it and never got a replacement order in.
                has_gtc = any(getattr(o, "time_in_force", None) == TimeInForce.GTC for o in sym_orders)
                if has_gtc:
                    self._force_close_pending.pop(sym, None)
                    log.warning(f"_sweep_force_closes {sym} [{info.get('reason')}]: market closed — giving up chase, carrying overnight under existing GTC stop")
                    continue

                # No GTC resting -- cancel any stale non-GTC close order first (it
                # still reserves the qty against a new order, same reservation
                # issue _free_shares_for_trim exists for) before re-arming.
                # Deliberately NOT popped from _force_close_pending on failure --
                # left for the next 10s poll to retry rather than silently giving
                # up with the position unprotected.
                stale = next((o for o in sym_orders if getattr(o, "time_in_force", None) != TimeInForce.GTC), None)
                if stale is not None:
                    try:
                        self.client.cancel_order_by_id(str(stale.id))
                        time.sleep(0.4)
                    except Exception as e:
                        log.warning(f"_sweep_force_closes {sym} [{info.get('reason')}]: market closed, stale-close cancel failed before GTC re-arm, will retry next poll: {e}")
                        continue

                side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                try:
                    trail_pct, _ = _trail_pct_for(sym, float(pos.current_price), self._entry_log)
                    self.client.submit_order(TrailingStopOrderRequest(
                        symbol=sym, qty=abs(qty), side=side,
                        type=AlpacaOrderType.TRAILING_STOP,
                        time_in_force=TimeInForce.GTC, trail_percent=trail_pct,
                    ))
                    self._force_close_pending.pop(sym, None)
                    log.warning(f"_sweep_force_closes {sym} [{info.get('reason')}]: market closed — giving up chase, re-armed GTC trailing stop, carrying overnight")
                except Exception as e:
                    log.error(f"_sweep_force_closes {sym} [{info.get('reason')}]: market closed, giving up chase, GTC re-arm failed, will retry next poll — position may be UNPROTECTED overnight in the meantime: {e}")
                continue

            pending = next((o for o in sym_orders if getattr(o, "time_in_force", None) != TimeInForce.GTC), None)
            if pending is not None:
                submitted_at = getattr(pending, "submitted_at", None) or getattr(pending, "created_at", None)
                age_s = (now_utc - submitted_at).total_seconds() if submitted_at else 0.0
                if age_s < AFTERHOURS_CHASE_STALE_SECONDS:
                    continue  # still fresh — give it time to fill
                try:
                    self.client.cancel_order_by_id(str(pending.id))
                    time.sleep(0.4)
                except Exception as e:
                    log.warning(f"_sweep_force_closes {sym}: stale-close cancel failed, will retry next poll: {e}")
                    continue

            # A resting GTC (re-armed as a fallback by another path, or never
            # cancelled) reserves the qty and would reject the replacement close.
            gtc_cancelled = False
            gtc = next((o for o in sym_orders if getattr(o, "time_in_force", None) == TimeInForce.GTC), None)
            if gtc:
                try:
                    self.client.cancel_order_by_id(str(gtc.id))
                    time.sleep(0.4)
                    gtc_cancelled = True
                except Exception as e:
                    log.warning(f"_sweep_force_closes {sym}: GTC cancel failed, will retry next poll: {e}")
                    continue

            side = OrderSide.SELL if qty > 0 else OrderSide.BUY
            try:
                chase_n  = info.get("chase_count", 0)
                slip_pct = min(0.5 * (chase_n + 1), 3.0)
                # no_extended_hours=True -- still regular hours here (checked above),
                # and this reason (eod/guardrail) must never spill into extended
                # hours, even if MarketState.from_now() has already flipped by the
                # time this fires (right at the 16:00 boundary).
                self._submit_closing_order(sym, abs(qty), side, float(pos.current_price), slip_pct=slip_pct, no_extended_hours=True)
                info["chase_count"] = chase_n + 1
                log.warning(
                    f"FORCE-CLOSE RE-CHASE {sym} [{info.get('reason')}]: unfilled after prior attempt "
                    f"— resubmitted @ {slip_pct:.1f}% slip (attempt {chase_n + 1})"
                )
            except Exception as e:
                log.error(f"_sweep_force_closes {sym}: re-chase failed: {e}")
                # GTC is gone and the replacement didn't go through -- without a
                # fallback the position would sit fully unprotected until the next
                # poll. Re-arm one now, same as check_afterhours_stops.
                if gtc_cancelled:
                    try:
                        trail_pct, _ = _trail_pct_for(sym, float(pos.current_price), self._entry_log)
                        self.client.submit_order(TrailingStopOrderRequest(
                            symbol        = sym,
                            qty           = abs(qty),
                            side          = side,
                            type          = AlpacaOrderType.TRAILING_STOP,
                            time_in_force = TimeInForce.GTC,
                            trail_percent = trail_pct,
                        ))
                        log.warning(f"_sweep_force_closes {sym}: re-armed GTC trailing stop as fallback after failed re-chase")
                    except Exception as rearm_err:
                        log.error(f"_sweep_force_closes {sym}: re-chase failed AND GTC re-arm failed — position may be UNPROTECTED: {rearm_err}")

    def _sweep_pending_entries(self) -> None:
        """Re-chase a resting ENTRY order that hasn't filled within
        AFTERHOURS_CHASE_STALE_SECONDS -- cancel and resubmit at a fresh
        live-mid-bounded limit with escalating slip, same shape as
        _sweep_force_closes on the exit side. 2026-08-14, confirmed live:
        without this, an entry that misses its initial 1%-bounded limit (a
        fast-moving or wide-spread name -- MF: bid $12.95/ask $17.20,
        order resting unfilled at $15.21) just sits until end of day and
        is silently never entered, no matter how good the signal was --
        every close path already re-chases, entries never did.
        ponytail: no cap on total re-chase attempts (only slip% is capped,
        at 3%) -- same known ceiling as _sweep_force_closes.

        2026-08-17, faded/stale entries (info["price_ceiling"] present, see
        _create_bracket_order): the FIRST wait uses
        FADED_ENTRY_PASSIVE_WINDOW_SECONDS instead of the normal (shorter)
        AFTERHOURS_CHASE_STALE_SECONDS -- give the passive limit its full
        window before touching it at all. Every chase after that is capped
        at price_ceiling (today's baseline price) until
        FADED_ENTRY_CEILING_TIMEOUT_SECONDS have passed since the ORIGINAL
        submission, so a reversal makes the fix wait, never chase upward
        into it -- only after that timeout does it fall through to the
        normal uncapped escalation, as a last resort so the trade isn't
        lost entirely (2026-08-14 "trade it anyway" rule).

        2026-08-17: also only ever re-chases the QUANTITY STILL UNFILLED,
        not the original full size -- a partial fill (routine on the thin/
        illiquid names this mostly applies to, and far more likely now that
        faded entries can rest for minutes instead of filling near-
        instantly) used to get topped up with a second full-size order on
        cancel+resubmit, silently over-buying the position."""
        if not self._entry_pending:
            return
        try:
            open_orders = self.client.get_orders() or []
        except Exception as e:
            log.warning(f"_sweep_pending_entries: fetch failed: {e}")
            return

        orders_by_id = {str(o.id): o for o in open_orders}
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        for sym, info in list(self._entry_pending.items()):
            pending = orders_by_id.get(info.get("order_id"))
            if pending is None:
                # No longer resting under that order id -- filled, cancelled
                # elsewhere, or expired. Either way, nothing left to chase.
                self._entry_pending.pop(sym, None)
                continue

            is_faded = "price_ceiling" in info
            submitted_at = getattr(pending, "submitted_at", None) or getattr(pending, "created_at", None)
            age_s = (now_utc - submitted_at).total_seconds() if submitted_at else 0.0
            stale_threshold = (
                FADED_ENTRY_PASSIVE_WINDOW_SECONDS
                if is_faded and info.get("chase_count", 0) == 0
                else AFTERHOURS_CHASE_STALE_SECONDS
            )
            if age_s < stale_threshold:
                continue  # still fresh — give it time to fill

            filled_so_far = int(float(getattr(pending, "filled_qty", 0) or 0))
            remaining = info["qty"] - filled_so_far
            if remaining <= 0:
                self._entry_pending.pop(sym, None)  # fully filled already, nothing left to chase
                continue

            try:
                self.client.cancel_order_by_id(str(pending.id))
                time.sleep(0.4)
            except Exception as e:
                log.warning(f"_sweep_pending_entries {sym}: stale-order cancel failed, will retry next poll: {e}")
                continue

            is_long = info["is_long"]
            side = OrderSide.BUY if is_long else OrderSide.SELL
            try:
                chase_n  = info.get("chase_count", 0)
                slip_pct = _entry_rechase_slip_pct(chase_n)
                mid = _live_quote_mid(self.client, sym, float(pending.limit_price or 0) or 0.01)
                fresh_limit = _marketable_limit_price(mid, is_long=is_long, buffer_pct=slip_pct)

                ceiling = info.get("price_ceiling")
                if ceiling is not None:
                    first_at = info.get("first_submitted_at")
                    ceiling_age_s = (now_utc - first_at).total_seconds() if first_at else float("inf")
                    if ceiling_age_s < FADED_ENTRY_CEILING_TIMEOUT_SECONDS:
                        fresh_limit = min(fresh_limit, ceiling) if is_long else max(fresh_limit, ceiling)

                req = LimitOrderRequest(
                    symbol=sym, qty=remaining, side=side, time_in_force=TimeInForce.DAY,
                    limit_price=fresh_limit,
                    client_order_id=f"apex-rechase-{sym}-{int(time.time())}",
                )
                new_order = self.client.submit_order(req)
                self.order_cache[sym] = new_order.id
                info["order_id"] = str(new_order.id)
                info["qty"] = remaining
                info["chase_count"] = chase_n + 1
                log.warning(
                    f"ENTRY RE-CHASE {sym}: unfilled after prior attempt "
                    f"— resubmitted {remaining} @ ${fresh_limit:.2f} ({slip_pct:.1f}% off mid, attempt {chase_n + 1})"
                    + (f" [capped @ ${ceiling:.2f}]" if ceiling is not None and ceiling_age_s < FADED_ENTRY_CEILING_TIMEOUT_SECONDS else "")
                )
            except Exception as e:
                log.error(f"_sweep_pending_entries {sym}: re-chase failed: {e}")
                self._entry_pending.pop(sym, None)  # give up tracking rather than loop on a hard failure

    # ── Stale Swing Exit ─────────────────────────────────────────────────────
    def _get_entry_date(self, symbol: str) -> Optional[datetime.date]:
        """Return the date a position was opened.

        Checks the in-memory entry log first, then falls back to the broker's
        MOST RECENT filled BUY order for the symbol — covers positions opened
        on a prior day whose entry_log record was lost to a bot restart (the
        startup rebuild in _rebuild_entry_log_from_orders only restores today's
        orders).

        2026-08-14, confirmed live: this used Sort.ASC (oldest first) with no
        date bound, so a symbol bought, sold, and re-bought weeks apart (SNXX:
        2026-07-21 and again 2026-08-14) always returned the ANCIENT fill, not
        the one that actually opened the currently-open lot -- close_stale_
        swing_positions then saw "held 24d" for a position that was 52 minutes
        old and force-closed it. The most recent matching BUY is always the
        right one for a position that's currently open (if it had been closed
        after an earlier buy, the position wouldn't be open now) -- Sort.DESC."""
        info = self._entry_log.get(symbol)
        if info and info.get("date"):
            return info["date"]
        try:
            import pytz
            from alpaca.common.enums import Sort
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            et  = pytz.timezone("America/New_York")
            req = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED, symbols=[symbol],
                side=OrderSide.BUY, direction=Sort.DESC, limit=50,
            )
            orders = self.client.get_orders(filter=req) or []
            for order in orders:
                filled_at = getattr(order, "filled_at", None)
                if filled_at is None:
                    continue
                entry_date = filled_at.astimezone(et).date() if hasattr(filled_at, "astimezone") else filled_at
                self._entry_log.setdefault(symbol, {"strategy": "restored", "confidence": 0.0})["date"] = entry_date
                return entry_date
        except Exception as e:
            log.warning(f"_get_entry_date {symbol}: lookup failed: {e}")
        return None

    def _get_entry_datetime(self, symbol: str, is_long: bool = True) -> Optional[datetime.datetime]:
        """Return the UTC fill timestamp a position was opened — hour-precision
        counterpart to _get_entry_date, needed for the NO_GAIN_EXIT_HOURS check.
        Same broker fallback for positions opened before a bot restart.

        2026-08-14: two bugs fixed here together, same root pattern as
        _get_entry_date (see its docstring for the SNXX case that surfaced
        this) --
          1. Sort.ASC with no date bound returned the OLDEST matching fill
             ever, not the one that opened the currently-open lot. Sort.DESC
             (most recent first) is always correct for a position that's
             still open. Same fix already confirmed necessary for QNT
             2026-08-13, where held_hours came back inflated after a restart.
          2. side was hardcoded to BUY regardless of the position's actual
             direction -- a SHORT is opened via a SELL, so this fallback
             could never find the right order for a short at all (silently
             fell through to a wrong, unrelated BUY or None). is_long now
             selects the correct side; callers must pass their own qty sign."""
        info = self._entry_log.get(symbol)
        if info and info.get("filled_at"):
            return info["filled_at"]
        try:
            from alpaca.common.enums import Sort
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            req = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED, symbols=[symbol],
                side=(OrderSide.BUY if is_long else OrderSide.SELL), direction=Sort.DESC, limit=50,
            )
            orders = self.client.get_orders(filter=req) or []
            for order in orders:
                filled_at = getattr(order, "filled_at", None)
                if filled_at is None:
                    continue
                self._entry_log.setdefault(symbol, {"strategy": "restored", "confidence": 0.0})["filled_at"] = filled_at
                return filled_at
        except Exception as e:
            log.warning(f"_get_entry_datetime {symbol}: lookup failed: {e}")
        return None

    def close_stale_swing_positions(self) -> Optional[dict]:
        """Close swing-strategy positions (i.e. any long NOT opened by a strategy
        in EOD_CLOSE_STRATEGIES, since those already close same-day) that have
        been held SWING_STALE_DAYS+ calendar days without reaching
        SWING_STALE_MIN_GAIN_PCT% unrealized gain. Runs once per calendar day.

        These positions otherwise ride only the GTC trailing stop, which only
        protects against a reversal from the peak — it never exits a position
        that just goes nowhere. This is the "cut dead capital loose" check."""
        if not SWING_STALE_EXIT_ENABLED:
            return None

        today = datetime.date.today()
        if getattr(self, "_stale_exit_done", None) == today:
            return None

        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.error(f"close_stale_swing_positions: fetch failed: {e}")
            return None

        closed_items = []
        failed_items = []

        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately

            qty = int(float(pos.qty))
            if qty <= 0:
                continue  # only long swing positions are subject to this policy

            strategy = self._entry_log.get(sym, {}).get("strategy")
            if strategy in EOD_CLOSE_STRATEGIES:
                continue  # already force-closed same-day by close_eod_positions

            entry_date = self._get_entry_date(sym)
            if entry_date is None:
                log.warning(f"close_stale_swing_positions {sym}: can't determine entry date, skipping")
                continue

            held_days = (today - entry_date).days
            if held_days < SWING_STALE_DAYS:
                continue

            try:
                gain_pct = float(pos.unrealized_plpc) * 100
            except (AttributeError, TypeError, ValueError):
                continue

            if gain_pct >= SWING_STALE_MIN_GAIN_PCT:
                continue  # performing fine — leave it to the trailing stop

            try:
                # Cancel ALL resting orders first, including GTC — this method has no
                # regular-hours gate (only "once per calendar day"), so it can run
                # after-hours too, and a resting GTC trailing stop reserves qty and
                # gets a close rejected as a wash trade regardless of time of day
                # (same root cause already fixed for check_afterhours_stops,
                # close_no_gain_positions, the weakest-swap path, and check_tp_targets —
                # confirmed in production via BHC's repeated "insufficient qty
                # available" TP-close rejections on 2026-07-31).
                try:
                    sym_orders = [o for o in (self.client.get_orders() or []) if o.symbol == sym]
                    for _o in sym_orders:
                        try:
                            self.client.cancel_order_by_id(str(_o.id))
                        except Exception:
                            pass
                    if sym_orders:
                        time.sleep(0.4)
                except Exception:
                    pass

                # _submit_closing_order handles the after-hours case (plain
                # MarketOrderRequest gets rejected outside regular hours).
                self._submit_closing_order(sym, abs(qty), OrderSide.SELL, float(pos.current_price))
                _strategy = self._entry_log.get(sym, {}).get("strategy", "unknown")
                try:
                    _pnl = float(pos.unrealized_pl)
                except (AttributeError, TypeError, ValueError):
                    _pnl = 0.0
                self._entry_log.pop(sym, None)

                closed_items.append({
                    "symbol": sym, "qty": abs(qty),
                    "held_days": held_days, "gain_pct": round(gain_pct, 2),
                })
                log.info(
                    f"STALE EXIT {sym} [{_strategy}]: {qty} shares | held {held_days}d | "
                    f"gain {gain_pct:+.1f}% < {SWING_STALE_MIN_GAIN_PCT:.1f}% threshold | P&L ${_pnl:+,.2f}"
                )
            except Exception as e:
                failed_items.append({"symbol": sym, "error": str(e)})
                log.error(f"STALE EXIT failed {sym}: {e}")

        self._stale_exit_done = today

        return {
            "date": today.isoformat(),
            "closed_count": len(closed_items),
            "failed_count": len(failed_items),
            "closed_items": closed_items,
            "failed_items": failed_items,
        }

    def close_no_gain_positions(self) -> Optional[dict]:
        """Close any position (long or short) that hasn't settled into a clear
        positive trend within NO_GAIN_EXIT_HOURS of entry: exit on ANY
        positive gain (stop waiting once it's decided), or on a
        NO_GAIN_EXIT_MAX_LOSS_PCT drop (cut it early rather than riding the
        full trailing stop down). Only a narrow flat/small-loss band survives
        the check and keeps holding. Checked every scan cycle (unlike
        close_stale_swing_positions, which only runs once/day) since the
        N-hour mark can land mid-session, not just at EOD.

        Was long-only ("if qty <= 0: continue") until 2026-08-12, at the
        user's request after finding a live short (ACHR) that had been open
        well past NO_GAIN_EXIT_HOURS with no exit path at all -- this rule
        skipped it by direction, same blind spot as the qty_available sign
        bug in protect_positions() found the same day. pos.unrealized_plpc is
        already sign-correct for shorts (negative when a short is losing, i.e.
        price rose) so the gain_pct band check below needs no changes for
        direction -- only the close side does: SELL for longs, BUY (cover)
        for shorts.
        """
        if not NO_GAIN_EXIT_ENABLED:
            return None

        now_utc = datetime.datetime.now(datetime.timezone.utc)

        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.error(f"close_no_gain_positions: fetch failed: {e}")
            return None

        _live_syms = {p.symbol for p in positions}
        for _sym in [s for s in self._no_gain_chase_count if s not in _live_syms]:
            self._no_gain_chase_count.pop(_sym, None)

        closed_items = []
        failed_items = []

        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately

            qty = int(float(pos.qty))
            if qty == 0:
                continue  # no position

            strategy = self._entry_log.get(sym, {}).get("strategy")
            if strategy in EOD_CLOSE_STRATEGIES:
                continue  # already force-closed same-day by close_eod_positions

            entry_dt = self._get_entry_datetime(sym, is_long=qty > 0)
            if entry_dt is None:
                log.warning(f"close_no_gain_positions {sym}: can't determine entry time, skipping")
                continue

            held_hours = (now_utc - entry_dt).total_seconds() / 3600
            if held_hours < NO_GAIN_EXIT_HOURS:
                continue

            try:
                gain_pct = float(pos.unrealized_plpc) * 100
            except (AttributeError, TypeError, ValueError):
                continue

            if NO_GAIN_EXIT_MAX_LOSS_PCT < gain_pct <= NO_GAIN_EXIT_MIN_PCT:
                continue  # still flat / a small loss — give it more time
            # Otherwise exit: either gain_pct > NO_GAIN_EXIT_MIN_PCT (positive —
            # stop waiting once it's decided) or gain_pct <= NO_GAIN_EXIT_MAX_LOSS_PCT
            # (dropped enough to cut early rather than ride the full trailing stop).

            # A close already in flight for this symbol? Don't blindly cancel-and-resubmit
            # every cycle (that's what spammed FRMI 186x and NG 38x — the old version
            # re-issued an identical close order every scan cycle with no fill check).
            # Give a fresh close AFTERHOURS_CHASE_STALE_SECONDS to fill; only re-chase,
            # with escalating slip, once it's actually stale.
            try:
                sym_orders = self.client.get_orders() or []
                sym_orders = [o for o in sym_orders if o.symbol == sym]
            except Exception as e:
                log.warning(f"close_no_gain_positions {sym}: order fetch failed, will retry next cycle: {e}")
                continue

            pending = next((o for o in sym_orders if getattr(o, "time_in_force", None) != TimeInForce.GTC), None)
            if pending is not None:
                submitted_at = getattr(pending, "submitted_at", None) or getattr(pending, "created_at", None)
                age_s = (now_utc - submitted_at).total_seconds() if submitted_at else 0.0
                if age_s < AFTERHOURS_CHASE_STALE_SECONDS:
                    continue  # close already in flight — give it time to fill
                try:
                    self.client.cancel_order_by_id(str(pending.id))
                    time.sleep(0.4)
                except Exception as e:
                    log.warning(f"close_no_gain_positions {sym}: stale-close cancel failed, will retry next cycle: {e}")
                    continue

            # The resting GTC trailing stop reserves this position's qty and can cause
            # the close to be rejected as a wash trade — cancel it first, same fix as
            # check_afterhours_stops. Re-armed below as a fallback if the close fails.
            gtc_order = next((o for o in sym_orders if getattr(o, "time_in_force", None) == TimeInForce.GTC), None)
            if gtc_order:
                try:
                    self.client.cancel_order_by_id(str(gtc_order.id))
                    time.sleep(0.4)
                except Exception as e:
                    log.warning(f"close_no_gain_positions {sym}: GTC cancel failed, will retry next cycle: {e}")
                    continue

            close_side = OrderSide.SELL if qty > 0 else OrderSide.BUY  # SELL to close a long, BUY to cover a short
            try:
                chase_n  = self._no_gain_chase_count.get(sym, 0)
                slip_pct = min(0.5 * (chase_n + 1), 3.0)
                self._submit_closing_order(sym, abs(qty), close_side, float(pos.current_price), slip_pct=slip_pct)
                self._no_gain_chase_count[sym] = chase_n + 1
                _strategy = self._entry_log.get(sym, {}).get("strategy", "unknown")
                try:
                    _pnl = float(pos.unrealized_pl)
                except (AttributeError, TypeError, ValueError):
                    _pnl = 0.0
                self._entry_log.pop(sym, None)

                closed_items.append({
                    "symbol": sym, "qty": abs(qty),
                    "held_hours": round(held_hours, 1), "gain_pct": round(gain_pct, 2),
                })
                _why = "positive gain" if gain_pct > NO_GAIN_EXIT_MIN_PCT else f"<= {NO_GAIN_EXIT_MAX_LOSS_PCT:.1f}% loss"
                log.info(
                    f"NO-GAIN EXIT {sym} [{_strategy}]: {qty} shares | held {held_hours:.1f}h | "
                    f"gain {gain_pct:+.1f}% ({_why}) | P&L ${_pnl:+,.2f} "
                    f"@ {slip_pct:.1f}% slip (attempt {chase_n + 1})"
                )
            except Exception as e:
                failed_items.append({"symbol": sym, "error": str(e)})
                log.error(f"NO-GAIN EXIT failed {sym}: {e}")
                if gtc_order:
                    # GTC is gone and the replacement didn't go through — re-arm one now
                    # rather than leave the position unprotected until the next cycle.
                    try:
                        trail_pct = _trail_pct_for(sym, float(pos.current_price), self._entry_log)[0]
                        self.client.submit_order(TrailingStopOrderRequest(
                            symbol=sym, qty=abs(qty), side=close_side,
                            type=AlpacaOrderType.TRAILING_STOP,
                            time_in_force=TimeInForce.GTC, trail_percent=trail_pct,
                        ))
                        log.warning(f"NO-GAIN EXIT {sym}: re-armed GTC trailing stop after failed close")
                    except Exception as rearm_err:
                        log.error(f"NO-GAIN EXIT {sym}: close failed AND GTC re-arm failed — position may be UNPROTECTED: {rearm_err}")

        return {
            "closed_count": len(closed_items),
            "failed_count": len(failed_items),
            "closed_items": closed_items,
            "failed_items": failed_items,
        }

    # ── Price Drift Stop (10-min poll, 30-min lookback, same-day entries) ──
    @staticmethod
    def _drift_stop_reason(
        current: float, entry: Optional[float], reference: Optional[float], is_long: bool, stop_pct: float,
    ) -> Optional[str]:
        """Pure decision logic for check_price_drift_stop: return a reason
        string if the adverse move versus EITHER `entry` (the position's own
        entry price) OR `reference` (the price PRICE_DRIFT_LOOKBACK_MIN ago)
        exceeds stop_pct, else None. Split out for unit-testability without a
        broker connection. Either reference being None/<=0 (missing data, or
        not enough rolling history yet) just drops that leg of the check --
        never a false trigger, and the other leg still applies independently.

        2026-08-14, user correction: entry-price leg restored after being
        dropped 2026-08-13 -- confirmed live TE dropped 2.69% off its OWN
        entry price with the 30-min-ago-only version never even looking at
        entry, so a slow bleed that never shows a full move within any
        single 10-min window went completely uncaught. Both legs checked
        again, OR'd together."""
        def _adverse_pct(ref: Optional[float]) -> Optional[float]:
            if ref is None or ref <= 0:
                return None
            return ((ref - current) / ref * 100) if is_long else ((current - ref) / ref * 100)

        drift_entry = _adverse_pct(entry)
        if drift_entry is not None and drift_entry > stop_pct:
            return f"entry ${entry:.2f}->${current:.2f} ({drift_entry:+.1f}%)"
        drift_ref = _adverse_pct(reference)
        if drift_ref is not None and drift_ref > stop_pct:
            return f"{PRICE_DRIFT_LOOKBACK_MIN}min ${reference:.2f}->${current:.2f} ({drift_ref:+.1f}%)"
        return None

    def _backfill_drift_reference(self, symbol: str) -> Optional[float]:
        """When _price_drift_history has no rolling history yet for symbol
        (a fresh position, or a restart wiped it), reconstruct an
        approximate PRICE_DRIFT_LOOKBACK_MIN-minutes-ago reference from real
        1-min bar data instead of leaving the position with zero drift
        protection until PRICE_DRIFT_LOOKBACK_MIN more minutes of in-memory
        history rebuilds on its own. Same "row N back ~= N minutes ago"
        approximation _check_momentum_freshness already uses. Returns None
        if bars are unavailable -- missing data never forces a decision,
        same fail-safe as everywhere else in this file."""
        try:
            bars = get_bars(symbol, period="1d", interval="1m")
            if bars.empty or "close" not in bars.columns or len(bars) <= PRICE_DRIFT_LOOKBACK_MIN:
                return None
            return float(bars["close"].iloc[-1 - PRICE_DRIFT_LOOKBACK_MIN])
        except Exception as e:
            log.warning(f"_backfill_drift_reference {symbol}: failed: {e}")
            return None

    def check_price_drift_stop(self) -> None:
        """Every PRICE_DRIFT_CHECK_INTERVAL_MIN (10 min), exit any same-day
        position that's moved against it by more than PRICE_DRIFT_STOP_PCT
        versus EITHER its own entry price OR its price PRICE_DRIFT_LOOKBACK_MIN
        (30 min) ago (2026-08-14: restored the entry-price leg -- a
        30-min-ago-only check misses a slow bleed that never shows a full
        move within any single 10-min window; see _drift_stop_reason).
        Tighter and faster than the normal trailing stop -- see the
        PRICE_DRIFT_STOP block in config.py for why (2026-08-13, confirmed
        live: DFSC/HLIT/EROC/JACK all bought right at the open, all faded
        4-8% before the wider trailing stop caught them; polling every 10
        min instead of 30 gives a fast 10-15 min collapse a real chance of
        being caught by the very next check). Longs: drop > PRICE_DRIFT_STOP_PCT%.
        Shorts: rise > PRICE_DRIFT_STOP_PCT% (mirrored). Scoped to same-day
        entries only (self._entry_log date), not by strategy -- survives the strategy-name
        loss a restart causes.

        No re-entry cooldown (2026-08-24, user request) -- this drift stop,
        the trailing stop, and check_ema15_exit are the whole protection
        stack; nothing here throttles how soon a symbol re-enters."""
        if not PRICE_DRIFT_STOP_ENABLED:
            return

        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"check_price_drift_stop: fetch failed: {e}")
            return

        today = datetime.date.today()
        live_syms = set()
        lookback_ticks = max(1, PRICE_DRIFT_LOOKBACK_MIN // PRICE_DRIFT_CHECK_INTERVAL_MIN)

        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately
            qty = int(float(pos.qty))
            if qty == 0:
                continue

            entry_info = self._entry_log.get(sym)
            if not entry_info or entry_info.get("date") != today:
                continue  # not a same-day entry — out of scope, leave on its normal trailing stop

            live_syms.add(sym)
            try:
                current = float(pos.current_price)
                entry   = float(pos.avg_entry_price)
            except (TypeError, ValueError):
                continue
            if current <= 0:
                continue

            is_long  = qty > 0
            history  = self._price_drift_history.setdefault(sym, deque(maxlen=lookback_ticks))
            # deque[0] is the oldest sample once full -- exactly lookback_ticks
            # checks back, i.e. ~PRICE_DRIFT_LOOKBACK_MIN minutes ago at this
            # check's own cadence. Not enough history yet (a fresh position, OR
            # a restart wiped it -- confirmed live 2026-08-14: TE entered
            # 09:33, the bot restarted twice before 30 clean minutes had
            # elapsed, so the in-memory history never rebuilt and TE sat with
            # zero drift protection past an hour while down -2.8%) -- backfill
            # an approximate reference from real 1-min bar data instead of
            # leaving the position unwatched until history rebuilds on its own.
            #
            # 2026-08-18, user request: that backfill is only valid once the
            # POSITION ITSELF is at least PRICE_DRIFT_LOOKBACK_MIN old -- for
            # anything younger, "PRICE_DRIFT_LOOKBACK_MIN minutes ago" lands
            # BEFORE entry, in bars that have nothing to do with this trade
            # (e.g. a LiquiditySweep long entered right after a sweep-low: the
            # 30 min before entry routinely include a HIGH above the entry
            # price, so a since-entry-flat position could "drift" >1% against
            # that stale pre-entry high and get stopped for a move that never
            # happened after we were even in the trade). Force reference=None
            # (drops that leg, per _drift_stop_reason) until the position has
            # actually been held that long -- the entry-price leg alone still
            # covers it in the meantime.
            entry_dt = self._get_entry_datetime(sym, is_long)
            age_min  = (
                (datetime.datetime.now(datetime.timezone.utc) - entry_dt).total_seconds() / 60
                if entry_dt else None
            )
            if age_min is not None and age_min < PRICE_DRIFT_LOOKBACK_MIN:
                reference = None
            else:
                reference = history[0] if len(history) == lookback_ticks else self._backfill_drift_reference(sym)
            reason = self._drift_stop_reason(current, entry, reference, is_long, PRICE_DRIFT_STOP_PCT)

            # Record this check's price regardless of outcome — the deque
            # naturally evicts the oldest sample once full, keeping the
            # lookback window rolling forward.
            history.append(current)

            if reason is None:
                continue

            try:
                sym_orders = [o for o in (self.client.get_orders() or []) if o.symbol == sym]
                for _o in sym_orders:
                    try:
                        self.client.cancel_order_by_id(str(_o.id))
                    except Exception:
                        pass
                if sym_orders:
                    time.sleep(0.4)
            except Exception as e:
                log.warning(f"check_price_drift_stop {sym}: order fetch/cancel failed, will retry next cycle: {e}")
                continue

            side = OrderSide.SELL if is_long else OrderSide.BUY
            try:
                self._submit_closing_order(sym, abs(qty), side, current)
                log.warning(f"PRICE DRIFT STOP {sym}: {abs(qty)} shares | {reason}")
            except Exception as e:
                log.error(f"PRICE DRIFT STOP {sym}: close failed: {e}")
                # The resting GTC was just cancelled above — re-arm a fallback
                # so the position isn't left fully unprotected.
                try:
                    trail_pct, _ = _trail_pct_for(sym, current, self._entry_log)
                    self.client.submit_order(TrailingStopOrderRequest(
                        symbol=sym, qty=abs(qty), side=side,
                        type=AlpacaOrderType.TRAILING_STOP, time_in_force=TimeInForce.GTC,
                        trail_percent=trail_pct,
                    ))
                    log.warning(f"check_price_drift_stop {sym}: re-armed GTC trailing stop after failed close")
                except Exception as rearm_err:
                    log.error(f"check_price_drift_stop {sym}: close failed AND GTC re-arm failed — position may be UNPROTECTED: {rearm_err}")

        # Drop history for symbols no longer held or no longer in scope
        for sym in [s for s in self._price_drift_history if s not in live_syms]:
            self._price_drift_history.pop(sym, None)

    @staticmethod
    def _ema15_exit_reason(close: float, ema15: float, entry_delta: float, is_long: bool) -> Optional[str]:
        """Pure decision function for check_ema15_exit().

        2026-08-24, user request: replaced the old zero-buffer "exit the
        instant close crosses EMA15" check -- it fired on ordinary 1-min
        noise (confirmed live: AZTA/SBET/VYX all oscillated within pennies
        of their own EMA15 and got exited/re-entered repeatedly without
        ever making a real move). Now entry-anchored: compare the CURRENT
        (close - ema15) against its value AT ENTRY (entry_delta, captured
        in _create_bracket_order) -- exit only once it's worsened by
        EMA15_EXIT_DELTA_PCT% of ema15's value, not on any single cross.
        Lets a position that entered already below its own EMA15 stay open
        as long as it isn't getting WORSE relative to where it started.
        Deliberately anchored to entry, not the best delta seen since --
        that give-back protection is the trailing stop's job, this answers
        "is this still deteriorating," not "give back some of the best case."
        """
        current_delta = close - ema15
        threshold = EMA15_EXIT_DELTA_PCT / 100.0 * ema15
        against = (current_delta <= entry_delta - threshold) if is_long else (current_delta >= entry_delta + threshold)
        if not against:
            return None
        return (
            f"1-min (price-EMA15) delta {current_delta:+.3f} vs entry {entry_delta:+.3f} "
            f"(close ${close:.2f}, EMA15 ${ema15:.2f})"
        )

    @staticmethod
    def _ema15_trend_drop_reason(ema15_now: float, ema15_entry: float, is_long: bool) -> Optional[str]:
        """Pure decision function for check_ema15_exit()'s second, independent
        check: has the EMA15 trend LINE ITSELF moved against the position by
        EMA15_TREND_DROP_PCT% since entry -- not price vs. EMA15 (that's
        _ema15_exit_reason's job), the trend line vs. its own earlier value.

        2026-08-24, user request: closes the blind spot _ema15_exit_reason
        has on a slow, steady bleed -- if price declines in step with its
        own EMA15, (price - ema15) stays roughly flat even though the trend
        has clearly turned (confirmed in backtest: MUZ drifted down all
        session without ever tripping the delta check, because EMA15 was
        drifting down right alongside price the whole way). This check
        catches that directly, independent of where price sits relative to
        EMA15 at any given instant. check_ema15_exit() exits if EITHER this
        or _ema15_exit_reason fires -- they're deliberately separate checks
        on separate signals, not merged into one condition."""
        move_pct = (ema15_now - ema15_entry) / ema15_entry * 100.0
        against = (move_pct <= -EMA15_TREND_DROP_PCT) if is_long else (move_pct >= EMA15_TREND_DROP_PCT)
        if not against:
            return None
        return (
            f"EMA15 itself moved {move_pct:+.2f}% since entry "
            f"(${ema15_entry:.2f} -> ${ema15_now:.2f}) — trend turned against the position"
        )

    @staticmethod
    def _ema15_breakdown_reason(close: float, ema15: float, is_long: bool) -> Optional[str]:
        """Pure decision function for check_ema15_exit() -- the SINGLE rule
        used instead of (_ema15_exit_reason + _ema15_trend_drop_reason) when
        the position entered AT OR ABOVE its own EMA15 (entry_delta >= 0).

        2026-08-24, user request: "the ema conditions I have currently in
        place are for the price is normally below the ema15, before entry.
        for price above ema15 at the entry consider only one rule exit if
        the price below ema15 minus 0.5% of ema" (confirmed via follow-up:
        0.5%, not the 5% the worked example implied). Not entry-anchored at
        all -- a fresh line under wherever the CURRENT EMA15 sits, since an
        above-EMA15 entry is already the stronger/more conventional setup
        and doesn't need the below-EMA15 checks' entry-relative tolerance."""
        threshold = EMA15_BREAKDOWN_PCT / 100.0 * ema15
        against = (close < ema15 - threshold) if is_long else (close > ema15 + threshold)
        if not against:
            return None
        level = ema15 - threshold if is_long else ema15 + threshold
        return (
            f"1-min close ${close:.2f} broke {'below' if is_long else 'above'} "
            f"EMA15{'-' if is_long else '+'}{EMA15_BREAKDOWN_PCT:.1f}% (${level:.2f}, EMA15 ${ema15:.2f})"
        )

    def check_ema15_exit(self) -> None:
        """Every STAGNANT_STOP_CHECK_INTERVAL_MIN (1 min), exit any same-day
        position whose (price - EMA15) has worsened, vs. its value at entry,
        by EMA15_EXIT_DELTA_PCT% of EMA15. 2026-08-22, user request: "check
        price close below ema15 to exit a long position, and price above
        ema15 to exit short position" -- supersedes the EMA9-slope version
        of this same check (check_ema_slope_exit, itself a same-day
        replacement for the flat/negative-vs-reference stagnant check and
        the disabled check_price_drift_stop).

        2026-08-24, user request: that original version was a zero-buffer
        cross (exit the instant close < EMA15) -- fired on ordinary 1-min
        noise, confirmed live (AZTA/SBET/VYX all oscillated within pennies
        of their own EMA15 and got exited/re-entered repeatedly). Now
        entry-anchored via self._entry_ema15_delta (captured in
        _create_bracket_order) -- see _ema15_exit_reason. Unlike the old
        version, this DOES need in-memory state (the entry delta) to
        compare against; a restart or a position with no captured delta
        falls back to treating this check's first observation as the new
        baseline (logged, not silently guessed) rather than force an exit
        decision on a reference it doesn't have.

        EMA15 itself is still recomputed fresh from real bar data every
        check. Fail-open on missing/insufficient bar data. Scoped to
        same-day entries only, same reasoning as check_price_drift_stop.

        2026-08-22, user request ("market orders executed with other
        pending orders removed ... price protection from observed price to
        filled prices"): both already covered by the shared exit plumbing
        below -- any resting order (e.g. the deferred GTC trailing stop) is
        cancelled before this submits a close, and _submit_closing_order
        itself is a marketable LIMIT bounded off the live bid/ask mid, not
        a naked MarketOrderRequest -- that bound IS the observed-to-filled
        price protection (see _submit_closing_order's docstring for the
        NBIL incident a true unbounded market order caused).

        2026-08-22, user request ("always create a complementary trail
        order to reenter, if the stock is still in the top trade universe
        list"): if `sym` is still in get_ti_primary() (the current top TI
        scrape list, ti_primary.json) after the exit fires, immediately
        re-arms a same-direction REENTRY_TRAIL_PCT trailing entry -- see
        the re-entry block right after the close below."""
        if not STAGNANT_STOP_ENABLED:
            return

        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"check_ema15_exit: fetch failed: {e}")
            return

        today = datetime.date.today()

        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately
            qty = int(float(pos.qty))
            if qty == 0:
                continue

            entry_info = self._entry_log.get(sym)
            if not entry_info or entry_info.get("date") != today:
                continue  # not a same-day entry — out of scope

            try:
                current = float(pos.current_price)
            except (TypeError, ValueError):
                continue
            if current <= 0:
                continue
            is_long = qty > 0

            bars = get_bars(sym, period="1d", interval="1m")
            if bars.empty or "close" not in bars.columns or len(bars) < EMA15_EXIT_MIN_BARS:
                continue  # not enough data -- never force a decision on it
            last_close = float(bars["close"].iloc[-1])
            ema15      = float(bars["close"].ewm(span=15, adjust=False).mean().iloc[-1])

            entry_delta = self._entry_ema15_delta.get(sym)
            entry_ema15 = self._entry_ema15.get(sym)
            if entry_delta is None or entry_ema15 is None:
                # No captured reference (restart wiped it, or this position
                # predates the delta capture) -- adopt this observation as
                # the new baseline instead of guessing; next check compares
                # against it.
                self._entry_ema15_delta[sym] = last_close - ema15
                self._entry_ema15[sym] = ema15
                continue

            # 2026-08-24, user request: two different rule sets depending on
            # which side of its own EMA15 the position entered on.
            #   - Entered on the FAVORABLE side (long: at/above EMA15;
            #     short: at/below) -- the stronger, more conventional
            #     entry. Single rule: _ema15_breakdown_reason, a fresh line
            #     under/over the CURRENT EMA15, not anchored to entry.
            #   - Entered on the WEAKER side (already lagging its own
            #     trend at entry, e.g. a dip-buy) -- the two entry-anchored
            #     checks: _ema15_exit_reason (price-EMA15 delta worsening
            #     vs. entry) OR _ema15_trend_drop_reason (the trend line
            #     itself moving against the position since entry -- catches
            #     a slow bleed where price and EMA15 decline together and
            #     the delta check alone never fires). Either firing exits.
            favorable_entry = (entry_delta >= 0) if is_long else (entry_delta <= 0)
            if not favorable_entry:
                # 2026-08-24, user request: "ema 15 above doesn't have to be
                # only for the stocks entered above ema 15, if the price
                # exceeds ema 15 by 1% then these stocks should hold above
                # ema15 minus 0.5%" -- a below-EMA15 entry that's since
                # reclaimed it by EMA15_RECLAIM_PCT% permanently switches to
                # the breakdown rule, same as if it had entered favorably.
                # One-way: checked every cycle only until reclaimed once.
                if sym in self._reclaimed_ema15:
                    favorable_entry = True
                else:
                    reclaim_pct = ((last_close - ema15) / ema15 * 100.0) if is_long else ((ema15 - last_close) / ema15 * 100.0)
                    if reclaim_pct >= EMA15_RECLAIM_PCT:
                        self._reclaimed_ema15.add(sym)
                        favorable_entry = True
                        log.info(f"{sym}: reclaimed EMA15 by {reclaim_pct:.2f}% — switching to the breakdown exit rule")

            if favorable_entry:
                reason = self._ema15_breakdown_reason(last_close, ema15, is_long)
            else:
                reason = self._ema15_exit_reason(last_close, ema15, entry_delta, is_long)
                if reason is None:
                    reason = self._ema15_trend_drop_reason(ema15, entry_ema15, is_long)
            if reason is None:
                continue

            # Cancel any resting order (the deferred GTC trailing stop, most
            # commonly) before closing -- the broker won't accept a second
            # order against qty that's already reserved by one.
            try:
                sym_orders = [o for o in (self.client.get_orders() or []) if o.symbol == sym]
                for _o in sym_orders:
                    try:
                        self.client.cancel_order_by_id(str(_o.id))
                    except Exception:
                        pass
                if sym_orders:
                    time.sleep(0.4)
            except Exception as e:
                log.warning(f"check_ema15_exit {sym}: order fetch/cancel failed, will retry next cycle: {e}")
                continue

            side = OrderSide.SELL if is_long else OrderSide.BUY
            try:
                # Marketable limit bounded off the live mid, not a naked
                # market order -- this bound is the observed-to-filled price
                # protection (see _submit_closing_order).
                self._submit_closing_order(sym, abs(qty), side, current)
                log.warning(f"EMA15 EXIT {sym}: {abs(qty)} shares | {reason}")

                # 2026-08-22, user request: "always create a complementary
                # trail order to reenter, if the stock is still in the top
                # trade universe list" -- don't just exit and forget a name
                # that's still actively on the TI radar; re-arm a trailing
                # entry (same direction as the position just closed) so it
                # re-enters automatically if/when the trend resumes. Best-
                # effort: a failure here must never be treated as the exit
                # itself failing.
                try:
                    if sym in get_ti_primary():
                        reentry_side = OrderSide.BUY if is_long else OrderSide.SELL
                        self.client.submit_order(TrailingStopOrderRequest(
                            symbol          = sym,
                            qty             = abs(qty),
                            side            = reentry_side,
                            type            = AlpacaOrderType.TRAILING_STOP,
                            time_in_force   = TimeInForce.DAY,
                            trail_percent   = REENTRY_TRAIL_PCT,
                            client_order_id = f"apex-ema15reentry-{sym}-{int(time.time())}",
                        ))
                        log.info(
                            f"EMA15 EXIT {sym}: still in top TI universe — armed a "
                            f"{REENTRY_TRAIL_PCT:.1f}% trailing {'BUY' if is_long else 'SELL'} to re-enter"
                        )
                except Exception as e:
                    log.warning(f"EMA15 EXIT {sym}: re-entry watch order failed (exit itself still succeeded): {e}")
            except Exception as e:
                log.error(f"EMA15 EXIT {sym}: close failed: {e}")
                # The resting GTC was just cancelled above — re-arm a fallback
                # so the position isn't left fully unprotected.
                try:
                    trail_pct, _ = _trail_pct_for(sym, current, self._entry_log)
                    self.client.submit_order(TrailingStopOrderRequest(
                        symbol=sym, qty=abs(qty), side=side,
                        type=AlpacaOrderType.TRAILING_STOP, time_in_force=TimeInForce.GTC,
                        trail_percent=trail_pct,
                    ))
                    log.warning(f"check_ema15_exit {sym}: re-armed GTC trailing stop after failed close")
                except Exception as rearm_err:
                    log.error(f"check_ema15_exit {sym}: close failed AND GTC re-arm failed — position may be UNPROTECTED: {rearm_err}")

    def check_pending_entries_ema(self) -> None:
        """Every STAGNANT_STOP_CHECK_INTERVAL_MIN (1 min), re-check the EMA7
        trend-alignment gate (_check_ema_trend_alignment) for every entry
        order still resting unfilled (tracked in self.order_cache), and
        cancel it if the condition no longer holds.

        2026-08-24, user request: "every minute check for the placed orders
        again for ema condition if the orders are not place already, often
        when the order is place the ema delta is met, but next minute the
        order doesn't execute but the ema delta condition is not met
        anymore." The entry gate is normally checked ONCE, at signal time,
        right before the trailing-buy order is submitted -- but that order
        is a resting TrailingStopOrderRequest that only fills once price
        reverses REENTRY_TRAIL_PCT% off its extreme (see
        _create_bracket_order), so it can sit unfilled for a while. By the
        time it would fill, the trend that justified placing it may have
        already turned back over. Rather than let it fill anyway on a
        setup that's no longer valid, this cancels it -- a fresh scan
        cycle is free to re-signal and re-submit if the setup comes back.

        Only touches orders in self.order_cache -- that dict is populated
        exclusively by entry submissions (_create_bracket_order and the
        EMA15-exit re-entry arm), never by exit/protective orders, so
        there's no risk of this cancelling a stop or a closing order."""
        for sym, order_id in list(self.order_cache.items()):
            try:
                order = self.client.get_order_by_id(order_id)
            except Exception as e:
                log.debug(f"check_pending_entries_ema {sym}: order lookup failed: {e}")
                continue
            status = str(getattr(order, "status", "")).lower()
            if status not in {"new", "partially_filled", "pending_new", "accepted", "held"}:
                continue  # already filled, cancelled, or expired elsewhere -- nothing to re-check
            raw_side = getattr(order, "side", "")
            side = str(getattr(raw_side, "value", raw_side)).lower()
            is_long = side == "buy"
            sig_stub = SimpleNamespace(symbol=sym)
            ok, reason = _check_ema_trend_alignment(sig_stub, is_long)
            if ok:
                continue
            try:
                self.client.cancel_order_by_id(order_id)
                self.order_cache.pop(sym, None)
                log.warning(f"PENDING ENTRY CANCELLED {sym}: still unfilled and {reason}")
            except Exception as e:
                log.warning(f"check_pending_entries_ema {sym}: cancel failed: {e}")

    @staticmethod
    def _swing_drift_stop_reason(current: float, entry: Optional[float], is_long: bool, stop_pct: float) -> Optional[str]:
        """Pure decision function for check_swing_drift_stop() -- entry price
        only (no 30-min-ago leg; doesn't map across multiple days the way it
        does intraday). Longs: current below entry by more than stop_pct%.
        Shorts: mirrored (current above entry)."""
        if entry is None or entry <= 0:
            return None
        adverse_pct = ((entry - current) / entry * 100) if is_long else ((current - entry) / entry * 100)
        if adverse_pct > stop_pct:
            return f"${entry:.2f}->${current:.2f} ({adverse_pct:+.1f}%)"
        return None

    def check_swing_drift_stop(self) -> None:
        """Wider-threshold sibling of check_price_drift_stop() for positions
        it doesn't cover -- anything NOT a same-day entry (multi-day swing
        holds). 2026-08-15, user request: idea #3 of six suggested
        improvements, built after TrendBreaker's multi-day losers (NWL
        -5.41% held 55h) sat unwatched between entry and its normal, much
        wider trailing stop for days at a time. See SWING_DRIFT_STOP_PCT in
        config.py for the reasoning and which trades this would/wouldn't
        have caught."""
        if not SWING_DRIFT_STOP_ENABLED:
            return

        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"check_swing_drift_stop: fetch failed: {e}")
            return

        today = datetime.date.today()
        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — managed separately
            qty = int(float(pos.qty))
            if qty == 0:
                continue

            entry_info = self._entry_log.get(sym)
            if entry_info and entry_info.get("date") == today:
                continue  # same-day — covered by check_price_drift_stop() already

            try:
                current = float(pos.current_price)
                entry   = float(pos.avg_entry_price)
            except (TypeError, ValueError):
                continue
            if current <= 0:
                continue

            is_long = qty > 0
            reason = self._swing_drift_stop_reason(current, entry, is_long, SWING_DRIFT_STOP_PCT)
            if reason is None:
                continue

            try:
                sym_orders = [o for o in (self.client.get_orders() or []) if o.symbol == sym]
                for _o in sym_orders:
                    try:
                        self.client.cancel_order_by_id(str(_o.id))
                    except Exception:
                        pass
                if sym_orders:
                    time.sleep(0.4)
            except Exception as e:
                log.warning(f"check_swing_drift_stop {sym}: order fetch/cancel failed, will retry next cycle: {e}")
                continue

            side = OrderSide.SELL if is_long else OrderSide.BUY
            try:
                self._submit_closing_order(sym, abs(qty), side, current)
                log.warning(f"SWING DRIFT STOP {sym}: {abs(qty)} shares | {reason}")
            except Exception as e:
                log.error(f"SWING DRIFT STOP {sym}: close failed: {e}")
                try:
                    trail_pct, _ = _trail_pct_for(sym, current, self._entry_log)
                    self.client.submit_order(TrailingStopOrderRequest(
                        symbol=sym, qty=abs(qty), side=side,
                        type=AlpacaOrderType.TRAILING_STOP, time_in_force=TimeInForce.GTC,
                        trail_percent=trail_pct,
                    ))
                    log.warning(f"check_swing_drift_stop {sym}: re-armed GTC trailing stop after failed close")
                except Exception as rearm_err:
                    log.error(f"check_swing_drift_stop {sym}: close failed AND GTC re-arm failed — position may be UNPROTECTED: {rearm_err}")

    # ── Kill Mode: Emergency Close All ───────────────────────────────────────
    def emergency_close_all(self, equity: float) -> None:
        """
        Kill mode emergency exit. Closes every open position as safely as possible.

        PDT rules (equity < $25k):
          - Positions opened on a PRIOR day → cancel any open orders then market-close.
            These are NOT day trades so no PDT count is consumed.
          - Positions opened TODAY → cannot close without a day-trade violation.
            Instead, a hairpin trailing stop of KILL_MODE_TRAIL_PCT (0.5%) is placed
            so the position exits automatically within minutes via the stop engine.

        PDT-exempt (equity >= $25k): cancel all open orders + market-close everything.
        """
        import time as _t

        pdt_exempt = equity >= PDT_ACCOUNT_MIN
        today      = datetime.date.today()

        try:
            positions   = self.client.get_all_positions()
            open_orders = self.client.get_orders()
        except Exception as e:
            log.error(f"KILL MODE: failed to fetch data: {e}")
            return

        orders_by_sym: dict = {}
        for o in open_orders:
            orders_by_sym.setdefault(o.symbol, []).append(o)

        closed: list    = []
        protected: list = []

        for pos in positions:
            sym = pos.symbol
            qty = int(float(pos.qty))
            if qty == 0:
                continue

            entry_date = self._entry_log.get(sym, {}).get("date")
            is_today   = entry_date == today

            if not pdt_exempt and is_today:
                # Today's position — tighten trailing stop to hairpin; do NOT market-close
                for o in orders_by_sym.get(sym, []):
                    try:
                        self.client.cancel_order_by_id(str(o.id))
                    except Exception:
                        pass
                _t.sleep(0.3)
                try:
                    stop_side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                    self.client.submit_order(TrailingStopOrderRequest(
                        symbol        = sym,
                        qty           = abs(qty),
                        side          = stop_side,
                        type          = AlpacaOrderType.TRAILING_STOP,
                        time_in_force = TimeInForce.GTC,
                        trail_percent = KILL_MODE_TRAIL_PCT,
                    ))
                    cur = float(pos.current_price or 0)
                    log.warning(
                        f"KILL MODE [PDT-SAFE] {sym}: hairpin trailing stop "
                        f"{KILL_MODE_TRAIL_PCT}% @ ${cur:.2f} "
                        f"(opened today — closing via stop to avoid PDT violation)"
                    )
                    protected.append(sym)
                except Exception as e:
                    log.error(f"KILL MODE: hairpin stop failed {sym}: {e}")
                continue

            # Prior-day position (or PDT-exempt): cancel standing orders, then market-close
            for o in orders_by_sym.get(sym, []):
                try:
                    self.client.cancel_order_by_id(str(o.id))
                except Exception:
                    pass
            _t.sleep(0.3)

            try:
                side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                # A plain MarketOrderRequest gets rejected outside regular hours —
                # kill mode is only reachable while is_market_open (07:00-20:00 ET),
                # not just regular hours, and every crash this account has actually
                # hit (BIOA, FIRY, SQQQ) happened after-hours. This is the emergency
                # exit; it can't be the one path that silently no-ops exactly when
                # it's needed most. _submit_closing_order handles the extended-hours
                # limit-order fallback the same as every other close path.
                self._submit_closing_order(sym, abs(qty), side, float(pos.current_price or 0))
                pnl = float(pos.unrealized_pl or 0)
                log.warning(
                    f"KILL MODE CLOSE {sym}: {abs(qty)} shares "
                    f"{'SELL' if qty > 0 else 'BUY-TO-COVER'} | unrealized ${pnl:+.2f}"
                )
                closed.append(sym)
            except Exception as e:
                log.error(f"KILL MODE: close failed {sym}: {e}")

        log.warning(
            f"KILL MODE COMPLETE — "
            f"market-closed: {len(closed)} {closed} | "
            f"hairpin stops (PDT-safe): {len(protected)} {protected}"
        )

    # ── Stale Order Updater ───────────────────────────────────────────────────
    def update_stale_orders(self) -> None:
        """
        Find open orders older than STALE_ORDER_MINUTES and re-submit them:
          - Regular hours   → cancel + market order (instant fill)
          - Extended hours  → cancel + limit order at current price (IOC)
        Only applies to entry/exit orders (buy/sell), not bracket legs (stop/limit TP-SL).
        Also resets _swap_cycle_closed so each scan cycle starts fresh.
        """
        import time
        self._swap_cycle_closed.clear()  # reset per-cycle swap dedup
        try:
            open_orders = self.client.get_orders()
        except Exception as e:
            log.warning(f"update_stale_orders: fetch failed: {e}")
            return

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        regular = self._current_market_state().is_regular_hours

        for order in open_orders:
            # Only handle plain entry/exit orders, not bracket legs or protective stops
            order_type = getattr(order, "order_type", "") or ""
            order_class = str(getattr(order, "order_class", "") or "")
            if order_class in ("bracket", "oco"):
                continue
            # Never cancel GTC trailing stop orders — they are protective stops,
            # not stale entry orders.  Killing them leaves positions unprotected.
            if "trailing_stop" in str(order_type).lower():
                continue

            created_at = getattr(order, "created_at", None)
            if created_at is None:
                continue

            # Pick timeout: intraday strategies use short cutoff to avoid lunchtime fills
            coid = str(getattr(order, "client_order_id", "") or "")
            is_intraday = False
            if coid.startswith("apex-"):
                parts = coid.split("-", 2)   # ["apex", strategy, symbol]
                if len(parts) >= 2 and parts[1] in EOD_CLOSE_STRATEGIES:
                    is_intraday = True
            cutoff_secs = (STALE_ORDER_MINUTES_INTRADAY if is_intraday else STALE_ORDER_MINUTES) * 60

            age_secs = (now_utc - created_at).total_seconds()
            if age_secs < cutoff_secs:
                continue

            sym = order.symbol
            qty = int(float(order.qty))
            side = order.side  # OrderSide enum
            order_id = str(order.id)

            log.info(
                f"STALE ORDER: {sym} {side} {qty} — age {age_secs/60:.1f}m "
                f"(cutoff {'intraday 30m' if is_intraday else '6h'}) "
                f"→ {'market' if regular else 'limit @ current price'}"
            )

            try:
                self.client.cancel_order_by_id(order_id)
                time.sleep(0.3)

                if regular:
                    # If the original was a limit buy and the limit was more than 1%
                    # below the current ask, the order was defensive/passive — don't
                    # blast it to market (bad fill); just cancel and let the next
                    # scan cycle re-evaluate.
                    orig_limit = float(getattr(order, "limit_price", None) or 0)
                    if orig_limit > 0 and str(order_type).lower() == "limit":
                        try:
                            quote = self.client.get_latest_quote(sym)
                            cur_ask = float(getattr(quote, "ask_price", orig_limit))
                        except Exception:
                            cur_ask = orig_limit
                        if cur_ask > 0 and orig_limit < cur_ask * 0.99:
                            log.info(
                                f"STALE ORDER {sym}: limit ${orig_limit:.2f} is defensive "
                                f"(ask=${cur_ask:.2f}) — cancelling without re-entry"
                            )
                            continue  # skip re-submit; cancelled above

                    req = MarketOrderRequest(
                        symbol=sym, qty=qty, side=side,
                        time_in_force=TimeInForce.DAY,
                    )
                else:
                    # Best-effort limit at current price for extended hours
                    try:
                        bar = self.client.get_latest_quote(sym)
                        cur_price = round(
                            (float(bar.ask_price) + float(bar.bid_price)) / 2, 2
                        )
                    except Exception:
                        cur_price = float(getattr(order, "limit_price", None) or 0)
                    if cur_price <= 0:
                        log.warning(f"STALE ORDER {sym}: can't determine price, skipping")
                        continue
                    req = LimitOrderRequest(
                        symbol=sym, qty=qty, side=side,
                        limit_price=cur_price,
                        time_in_force=TimeInForce.DAY,
                        extended_hours=True,
                    )

                self.client.submit_order(req)
                log.info(f"STALE ORDER {sym}: replaced successfully")
            except Exception as e:
                log.warning(f"STALE ORDER {sym}: replace failed: {e}")

    # ── ATR Take-Profit Checker ────────────────────────────────────────────────
    def check_tp_targets(self) -> None:
        """Scan open positions against stored ATR-based TP targets.
        Submits a market close (sell/buy-to-cover) when current price reaches TP.
        Called once per scan cycle alongside update_stale_orders().
        """
        if not self._tp_targets:
            return
        try:
            positions = {p.symbol: p for p in self.client.get_all_positions()}
        except Exception as e:
            log.warning(f"check_tp_targets: fetch failed: {e}")
            return

        triggered = []
        for sym, tp_price in list(self._tp_targets.items()):
            pos = positions.get(sym)
            if pos is None:
                triggered.append(sym)  # position already closed, clean up
                continue
            qty = int(float(pos.qty))
            if qty == 0:
                triggered.append(sym)
                continue
            cur_price = float(getattr(pos, "current_price", 0) or 0)
            if cur_price <= 0:
                continue
            is_long = qty > 0
            hit = (is_long and cur_price >= tp_price) or (not is_long and cur_price <= tp_price)
            if hit:
                try:
                    # Cancel ALL resting orders for this symbol first — a GTC trailing
                    # stop (or leftover DAY order) reserves qty and gets this rejected
                    # as "insufficient qty available" (confirmed in production: BHC
                    # rejected 13+ times over an hour on 2026-07-31, same root cause
                    # already fixed for check_afterhours_stops/close_no_gain_positions/
                    # the weakest-swap path — this one just never got it).
                    try:
                        for o in (self.client.get_orders() or []):
                            if o.symbol == sym:
                                self.client.cancel_order_by_id(str(o.id))
                                time.sleep(0.4)
                    except Exception as cancel_err:
                        log.warning(f"TP close {sym}: order cancel failed, close may reject: {cancel_err}")

                    side = OrderSide.SELL if is_long else OrderSide.BUY
                    # A plain MarketOrderRequest also gets rejected outside regular
                    # hours (07:00-20:00 is_market_open spans well past 09:30-16:00,
                    # and this method runs on every cycle in that whole window) —
                    # _submit_closing_order already handles the extended-hours case.
                    self._submit_closing_order(sym, abs(qty), side, cur_price)
                    _strategy = self._entry_log.get(sym, {}).get("strategy", "unknown")
                    try:
                        _pnl = float(getattr(pos, "unrealized_pl", 0) or 0)
                    except (TypeError, ValueError):
                        _pnl = 0.0
                    log.info(
                        f"TP HIT {sym} [{_strategy}]: ${cur_price:.2f} {'>=  ' if is_long else '<= '}"
                        f"${tp_price:.2f} | P&L ${_pnl:+,.2f} → {'sell' if is_long else 'buy-to-cover'} submitted"
                    )
                    triggered.append(sym)
                except Exception as e:
                    log.warning(f"TP close failed {sym}: {e}")

        for sym in triggered:
            self._tp_targets.pop(sym, None)

    # ── Health ─────────────────────────────────────────────────────────────────
    def get_health(self) -> Dict:
        try:
            acct = self._get_account(force_refresh=True)
            dt_left = self.pdt.remaining(acct.equity, acct.daytrade_count)
            return {
                "equity":           acct.equity,
                "cash":             acct.buying_power,
                "buying_power":     acct.buying_power,
                "pdt_protected":    acct.equity >= PDT_ACCOUNT_MIN,
                "day_trade_count":  acct.daytrade_count,
                "day_trades_left":  dt_left,
            }
        except Exception as e:
            log.error(f"Health check error: {e}")
            return {}


# 2026-08-24, user request: this guard used to sit right after _demo()'s own
# definition (line ~424), well before EnhancedExecutor exists below it --
# _demo() references EnhancedExecutor._ema15_exit_reason, so running this
# file directly always raised NameError before ever reaching that class's
# checks, silently since 2026-08-22. Moved to the actual end of the file so
# `python engine/execution/enhanced.py` runs every check it's meant to.
if __name__ == "__main__":
    _demo()
