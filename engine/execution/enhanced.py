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
    MIN_AVG_DAILY_VOLUME_REGULAR_HOURS, MIN_FLOAT_SHARES, MIN_MARKET_CAP,
    SWING_STALE_EXIT_ENABLED, SWING_STALE_DAYS, SWING_STALE_MIN_GAIN_PCT,
    NO_GAIN_EXIT_ENABLED, NO_GAIN_EXIT_HOURS, NO_GAIN_EXIT_MIN_PCT, NO_GAIN_EXIT_MAX_LOSS_PCT,
    AFTERHOURS_STOP_CHECK_ENABLED, AFTERHOURS_CHASE_STALE_SECONDS, AFTERHOURS_STOP_COOLDOWN_MIN,
    MAX_POSITION_CONCENTRATION_PCT, CORRELATION_GROUPS,
    LONG_ONLY_MODE,
    STALE_ORDER_MINUTES, STALE_ORDER_MINUTES_INTRADAY,
    KILL_MODE_TRAIL_PCT,
    SMALL_ACCOUNT_EQUITY_THRESHOLD, SMALL_ACCOUNT_MAX_POSITIONS,
    SMALL_ACCOUNT_MIN_POSITION_DOLLARS,
    POSITION_SIZE_PCT, SMALL_ACCOUNT_POSITION_SIZE_PCT,
    CONF_SCALE_MIN_MULT, CONF_SCALE_FULL_CONF,
    HIGH_CONFIDENCE_BONUS_THRESHOLD, HIGH_CONFIDENCE_BONUS_MULT,
    CONF_RATCHET_ENABLED, CONF_RATCHET_TRIGGER_GAIN_PCT, CONF_RATCHET_MAX_TIGHTEN,
    MOMENTUM_FRESHNESS_ENABLED, MOMENTUM_FRESHNESS_STRATEGIES,
    TRADE_STALE_MOMENTUM_REJECTS,
    MOMENTUM_FRESHNESS_LOOKBACK_MIN, MOMENTUM_FRESHNESS_MAX_PULLBACK_PCT,
    THIN_LIQUIDITY_POSITION_SIZE_PCT,
    THIN_LIQUIDITY_TRAILING_STOP_MULT,
    MARKETABLE_LIMIT_BUFFER_PCT,
    LIVE,
)
from engine.equity.strategies import Signal, _get_float_shares, _get_market_cap
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


def _resolve_freshness_reject(signal: Signal, fresh: bool, fade_reason: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Decide what _validate_trade does with a _check_momentum_freshness
    result: (valid, block_reason). fresh=True -> always valid, signal
    untouched. fresh=False -> hard-blocked (valid=False, block_reason=
    fade_reason) unless TRADE_STALE_MOMENTUM_REJECTS, in which case the
    signal is flagged thin_liquidity=True (same reduced sizing as a
    guardrail admit, see _apply_thin_liquidity_override) and treated as
    valid so it still trades. Split out for unit-testability without a
    broker/bars connection — mutates signal in place same as the inline
    version would, callers pass their own Signal instance."""
    if fresh:
        return True, None
    if not TRADE_STALE_MOMENTUM_REJECTS:
        return False, fade_reason
    signal.thin_liquidity = True
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


def _apply_high_confidence_bonus(risk_info: Dict, confidence: float, equity: float) -> Dict:
    """2026-08-13, user request: confidence-scaling (_execute_entry's
    CONF_SCALE_MIN_MULT..CONF_SCALE_FULL_CONF ramp) plateaus at 1.0x for any
    confidence >= 85% -- 85% and 99% get sized identically. This adds one
    more tier: allocation_pct x HIGH_CONFIDENCE_BONUS_MULT (7.5% -> 11.25%
    at the default 1.5x -- a multiplier, not a flat point-add: "7.5 to 11%
    not 9") for confidence strictly above HIGH_CONFIDENCE_BONUS_THRESHOLD
    (92%). Returns risk_info unchanged otherwise. Applied before
    _apply_thin_liquidity_override in the caller, which fully overrides --
    not stacks with -- either scaling step."""
    if confidence <= HIGH_CONFIDENCE_BONUS_THRESHOLD:
        return risk_info
    bonus_pct = risk_info["allocation_pct"] * HIGH_CONFIDENCE_BONUS_MULT
    return dict(risk_info, allocation_pct=bonus_pct, dollar_amount=round(equity * bonus_pct / 100.0, 2))


def _trail_pct_for(symbol: str, price: float, entry_log: Dict) -> Tuple[float, str]:
    """Trailing-stop % + tier label for `symbol`, with the thin-liquidity
    override applied: a symbol admitted only via TRADE_THIN_LIQUIDITY_REJECTS
    (entry_log[symbol]['thin_liquidity'] -- set at entry, see _execute_entry)
    always gets HALF the normal dynamic-tier trail% (THIN_LIQUIDITY_
    TRAILING_STOP_MULT) instead of the tier's own value -- these names
    already failed a liquidity guardrail, so they're held on a shorter leash
    for their whole life. Single source of truth for every trailing-stop
    placement/re-place/tighten in this file (protect_positions, ratchet,
    after-hours virtual-stop, all re-arm fallbacks) instead of 6 separate
    get_dynamic_tier() call sites drifting out of sync with each other."""
    tier_info = get_dynamic_tier(symbol, price)
    trail_pct, tier_label = tier_info["ts"], tier_info["tier"]
    if entry_log.get(symbol, {}).get("thin_liquidity"):
        return round(trail_pct * THIN_LIQUIDITY_TRAILING_STOP_MULT, 2), f"{tier_label}/THIN"
    return trail_pct, tier_label


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


if __name__ == "__main__":
    _demo()


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
        self._pdt_stop_blocked: Dict[str, float] = {}  # {symbol: stop_price} — broker-rejected stops; monitored in software
        self._afterhours_stop_cooldown: Dict[str, float] = {}  # {symbol: monotonic expiry} — blocks re-entry after a stop-loss exit (despite the name, populated by ANY stop-loss close now, not just the after-hours software path — see detect_stopped_out_positions)
        self._last_known_positions: Dict[str, dict] = {}  # {symbol: {entry_price, last_price, is_long}} — snapshot used to notice a position disappearing between polls
        self._afterhours_chase_count: Dict[str, int] = {}  # {symbol: consecutive re-chase attempts} — widens slip each retry so a fast-falling after-hours book actually fills
        self._no_gain_chase_count: Dict[str, int] = {}  # same, for close_no_gain_positions's re-chase
        self._pdt_overnight_forced: set = set()  # symbols where PDT also blocks close — forced overnight, no retries
        self._pdt_violation_alerted: bool = False  # tracks whether the PDT violation email has been sent this session
        self._eod_close_done: object = None  # date of last completed EOD close (prevents duplicate runs)
        self._force_close_pending: Dict[str, dict] = {}  # {symbol: {"reason": str, "chase_count": int}} — EOD/guardrail closes not yet confirmed flat; swept by _sweep_force_closes until filled
        # {symbol: deque of the last N check_price_drift_stop prices, maxlen = PRICE_DRIFT_LOOKBACK_MIN / PRICE_DRIFT_CHECK_INTERVAL_MIN}
        # deque[0] is the oldest sample kept — the ~PRICE_DRIFT_LOOKBACK_MIN-minutes-ago reference once full.
        self._price_drift_history: Dict[str, Deque[float]] = {}
        # {symbol: {"order_id": str, "qty": int, "is_long": bool, "chase_count": int}}
        # — resting entry orders not yet confirmed filled; swept by _sweep_pending_entries
        self._entry_pending: Dict[str, dict] = {}
        self._stale_exit_done: object = None  # date of last completed swing stale-exit check
        self.market_state: Optional[MarketState] = None
        self._rebuild_entry_log_from_orders()

    def update_market_state(self, market_state: MarketState) -> None:
        """Store the active market snapshot for per-cycle execution decisions."""
        self.market_state = market_state

    # -- Entry Log Rebuild (survive restarts) ----------------------------
    def _rebuild_entry_log_from_orders(self) -> None:
        """On startup, reconstruct today's entry log from Alpaca filled buy orders.
        Prevents swap-closes of same-day positions after a bot restart, which would
        trigger Alpaca PDT protection (error 40310100)."""
        try:
            today = datetime.date.today()
            import pytz
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            et       = pytz.timezone("America/New_York")
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
                side = str(getattr(order, "side", "")).lower()
                if side != "buy":
                    continue
                sym = order.symbol
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

        # Post-loss re-entry cooldown (long or short) — checked live here, not just
        # at scan-target build time (_build_scan_targets), which only excludes a
        # cooling-down symbol from that cycle's scan universe as a one-time snapshot.
        # The cooldown itself is set by a background thread polling every 10s
        # (detect_stopped_out_positions), so a symbol that closes at a loss and gets
        # rescanned within that ~10s window was slipping through — confirmed
        # 2026-08-11: PLUG closed at a loss, STOP-COOLDOWN logged, then EXECUTE: BUY
        # PLUG fired 6 seconds later anyway, on into a second, bigger loss. This is
        # the actual backstop; the scan-target filter is just an optimization to
        # avoid wastefully re-scanning a symbol we already know is blocked.
        if signal.symbol in self.get_afterhours_cooldown_symbols():
            return False, f"{signal.symbol} in post-loss re-entry cooldown"

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
        """Returns (shares, skip_reason). Downsizes if BP constrained, skips if below min."""
        margin  = 2.0 if order_type == OrderType.SHORT else 1.0
        usable  = buying_power * (1.0 - MIN_BUYING_POWER_PCT / 100.0)
        desired = int(risk_info["dollar_amount"] / signal.price)
        max_bp  = int(usable / (signal.price * margin))

        account_snapshot = self._account_cache or self._get_account()  # use cached if available
        max_concentration = int(account_snapshot.equity * MAX_POSITION_CONCENTRATION_PCT / 100.0 / signal.price)
        shares  = min(desired, max_bp, max_concentration)

        min_position = SMALL_ACCOUNT_MIN_POSITION_DOLLARS if account_snapshot.equity < SMALL_ACCOUNT_EQUITY_THRESHOLD else MIN_POSITION_DOLLARS

        if shares < 1:
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

    def _create_bracket_order(self, signal: Signal, shares: int, risk_info: Dict, order_type: OrderType) -> bool:
        """Submit a bounded-limit entry (see _marketable_limit_price) then a
        GTC trailing stop at risk_info['stop_loss_pct']%. TP bracket leg is
        intentionally dropped — the trailing stop locks in gains
        automatically; swap logic and EOD close handle opportunity exits."""
        side      = OrderSide.BUY  if order_type == OrderType.LONG else OrderSide.SELL
        stop_side = OrderSide.SELL if order_type == OrderType.LONG else OrderSide.BUY
        trail_pct = risk_info["stop_loss_pct"]  # tiered: NORMAL=3%, MEDIUM=4%, HIGH=5%, EXTREME=7%

        # ── Step 1: Entry order (failure aborts the whole bracket) ──────────
        try:
            mid = _live_quote_mid(self.client, signal.symbol, signal.price)
            entry_limit = _marketable_limit_price(mid, is_long=(order_type == OrderType.LONG))
            entry_req = LimitOrderRequest(
                symbol          = signal.symbol,
                qty             = shares,
                side            = side,
                time_in_force   = TimeInForce.DAY,
                limit_price     = entry_limit,
                client_order_id = f"apex-{signal.strategy}-{signal.symbol}-{int(time.time())}",
            )
            order = self.client.submit_order(entry_req)
            self.order_cache[signal.symbol] = order.id
            self._entry_pending[signal.symbol] = {
                "order_id": str(order.id), "qty": shares,
                "is_long": order_type == OrderType.LONG, "chase_count": 0,
            }

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
        mid (see _marketable_limit_price/_live_quote_mid) -- extended hours
        needs extended_hours=True to be eligible to fill at all; regular
        hours used to be a plain MarketOrderRequest with no price bound,
        same NBIL-class risk _create_bracket_order had (see
        MARKETABLE_LIMIT_BUFFER_PCT in config.py)."""
        side   = OrderSide.BUY if order_type == OrderType.LONG else OrderSide.SELL
        action = "BUY"         if order_type == OrderType.LONG else "SHORT"

        try:
            coid     = f"apex-{signal.strategy}-{signal.symbol}-{int(time.time())}"
            extended = EXTENDED_HOURS and not self._current_market_state().is_regular_hours
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
        risk_info = _apply_high_confidence_bonus(risk_info, signal.confidence, acct.equity)
        if risk_info["allocation_pct"] != _pre_bonus_pct:
            log.debug(
                f"[SIZE] {signal.symbol} conf={signal.confidence:.0%} > {HIGH_CONFIDENCE_BONUS_THRESHOLD:.0%} "
                f"— high-confidence bonus: allocation {_pre_bonus_pct:.1f}% → {risk_info['allocation_pct']:.1f}% "
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

                trail_pct, tier_label = _trail_pct_for(sym, current, self._entry_log)

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
    ) -> None:
        """Submit a position-closing order as a marketable limit crossing the
        spread by slip_pct off the LIVE bid/ask mid (see _live_quote_mid) --
        never a naked MarketOrderRequest during regular hours either
        anymore: same unbounded-spread risk as the entry side (NBIL,
        MARKETABLE_LIMIT_BUFFER_PCT), just on the way out instead of in.
        extended_hours is set whenever we're actually outside regular hours,
        since Alpaca rejects market orders (and non-extended limits) then --
        force_extended_hours=True overrides that for callers submitted DURING
        regular hours that still need to survive past the close if unfilled
        (EOD/guardrail force-closes at 15:45 ET: a plain DAY order expires
        worthless at 16:00 instead of carrying into the after-hours session).
        Callers that keep missing the fill (fast-moving book) should widen
        slip_pct on retry rather than resubmitting at the same price forever."""
        mid  = _live_quote_mid(self.client, symbol, current_price)
        slip = (1.0 - slip_pct / 100.0) if side == OrderSide.SELL else (1.0 + slip_pct / 100.0)
        req = LimitOrderRequest(
            symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY,
            limit_price=round(mid * slip, 2),
            extended_hours=force_extended_hours or not MarketState.from_now().is_regular_hours,
        )
        self.client.submit_order(req)

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

    def get_afterhours_cooldown_symbols(self) -> set:
        """Symbols currently blocked from re-entry after an after-hours stop-loss exit."""
        now = time.monotonic()
        expired = [s for s, exp in self._afterhours_stop_cooldown.items() if now >= exp]
        for s in expired:
            self._afterhours_stop_cooldown.pop(s, None)
        return set(self._afterhours_stop_cooldown.keys())

    def detect_stopped_out_positions(self) -> None:
        """Catch a position closing via ANY route — most commonly a normal
        broker-side GTC trailing stop filling on its own — and apply the same
        re-entry cooldown that check_afterhours_stops() already sets for its
        own software-triggered closes.

        Confirmed necessary 2026-08-05: SOXS was stopped out and immediately
        re-bought the identical VWAPFade signal repeatedly (22 trades, -$605
        net) because the cooldown only ever fired from
        check_afterhours_stops()'s own close path — a normal GTC fill, the
        far more common way a stop actually triggers, never touched it.

        Approximate on purpose: compares against the last-seen mark price
        rather than looking up the exact closing fill via the orders API —
        good enough to tell "this was heading toward/at a loss," which is all
        a re-entry cooldown needs; not meant to be an exact realized-P&L
        figure (see the trade-history report for that).
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

        for sym, info in self._last_known_positions.items():
            if sym in current:
                continue  # still open
            # Closed via any route — eligible for confidence-ratchet protection
            # again next time it's re-entered (was symbol-keyed and add-only,
            # so a re-entry after an earlier ratcheted win got none — 2026-08-10:
            # ABCL ran +6.3% unrealized on a fresh entry after an earlier lot had
            # already ratcheted, and never got tightened).
            self._ratchet_done.discard(sym)
            if sym in self._afterhours_stop_cooldown:
                continue  # already cooling down from elsewhere
            entry, last, is_long = info["entry_price"], info["last_price"], info["is_long"]
            was_loss = (last < entry) if is_long else (last > entry)
            if was_loss:
                self._afterhours_stop_cooldown[sym] = time.monotonic() + AFTERHOURS_STOP_COOLDOWN_MIN * 60
                log.info(
                    f"STOP-COOLDOWN {sym}: closed near a loss (last ${last:.2f} vs entry ${entry:.2f}) "
                    f"— blocking re-entry for {AFTERHOURS_STOP_COOLDOWN_MIN} min"
                )

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
                    self._afterhours_stop_cooldown[sym] = time.monotonic() + AFTERHOURS_STOP_COOLDOWN_MIN * 60
                    _strategy = self._entry_log.get(sym, {}).get("strategy", "unknown")
                    _pnl = (current - entry) * qty
                    log.warning(
                        f"AFTER-HOURS SL HIT {sym} [{_strategy}]: price ${current:.2f} crossed stop ${stop_price:.2f} "
                        f"({trail_pct:.1f}% from entry ${entry:.2f}) | P&L ${_pnl:+,.2f} — extended-hours "
                        f"{'SELL' if is_long else 'BUY-TO-COVER'} submitted @ {slip_pct:.1f}% slip "
                        f"(attempt {chase_n + 1}), re-entry blocked {AFTERHOURS_STOP_COOLDOWN_MIN // 60}h"
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
    def enforce_position_concentration(self) -> None:
        """Trim any position whose market value exceeds MAX_POSITION_CONCENTRATION_PCT
        of account equity. Entry sizing already caps new buys at this limit (see
        _size_with_buying_power), but an existing winner can still drift past it
        through price appreciation alone — this is the backstop for that case."""
        try:
            acct = self._get_account()
            positions = self.client.get_all_positions()
        except Exception as e:
            log.warning(f"enforce_position_concentration: fetch failed: {e}")
            return
        if acct.equity <= 0:
            return
        cap_value = acct.equity * MAX_POSITION_CONCENTRATION_PCT / 100.0
        for pos in positions:
            sym = pos.symbol
            if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', sym):
                continue  # options legs — sized/managed separately
            qty = int(float(pos.qty))
            if qty == 0:
                continue
            market_value = abs(float(pos.market_value))
            if market_value <= cap_value:
                continue
            current = float(pos.current_price)
            trim_qty = int((market_value - cap_value) / current)
            if trim_qty < 1:
                continue
            side = OrderSide.SELL if qty > 0 else OrderSide.BUY  # BUY-to-cover trims a short
            try:
                self._submit_closing_order(sym, trim_qty, side, current)
                log.warning(
                    f"CONCENTRATION TRIM {sym}: {trim_qty} shares — ${market_value:,.0f} was "
                    f"{market_value / acct.equity:.0%} of equity, cap {MAX_POSITION_CONCENTRATION_PCT:.0f}%"
                )
            except Exception as e:
                log.error(f"enforce_position_concentration {sym}: trim failed: {e}")

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
        """Close all intraday-strategy positions at EOD_CLOSE_TIME.
        Targets FloatRotation, GapBreakout, ORB, VWAPReclaim opened today."""
        if not EOD_CLOSE_ENABLED:
            return None

        import pytz
        now_et = datetime.datetime.now(pytz.timezone("America/New_York"))
        close_h, close_m = map(int, EOD_CLOSE_TIME.split(":"))
        if now_et.hour < close_h or (now_et.hour == close_h and now_et.minute < close_m):
            return None  # Not yet EOD close time
        if now_et.hour >= 16:
            return None  # Market already closed

        today = datetime.date.today()
        if getattr(self, "_eod_close_done", None) == today:
            return None  # EOD close already processed for today

        try:
            positions = self.client.get_all_positions()
        except Exception as e:
            log.error(f"close_eod_positions: fetch failed: {e}")
            return None

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
            if entry_info.get("strategy") not in EOD_CLOSE_STRATEGIES:
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
                # force_extended_hours=True: submitted during regular hours (15:45 ET)
                # but must survive past the 16:00 close if unfilled, not expire worthless
                # -- _sweep_force_closes (below) re-chases it into after-hours if needed.
                self._submit_closing_order(sym, abs(qty), side, float(pos.current_price), force_extended_hours=True)
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
                    f"strategy={entry_info['strategy']} | P&L ${pnl:.2f}"
                )
            except Exception as e:
                failed_items.append({"symbol": sym, "error": str(e)})
                log.error(f"EOD close failed {sym}: {e}")

        self._eod_close_done = today

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
        held after-hours/overnight. Runs once/day, gated the same way as
        close_eod_positions."""
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
        if getattr(self, "_guardrail_eod_close_done", None) == today:
            return None  # Already processed today

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
                # force_extended_hours=True: same reasoning as close_eod_positions --
                # submitted during regular hours but must survive past 16:00 if
                # unfilled; _sweep_force_closes re-chases it into after-hours.
                self._submit_closing_order(sym, abs(qty), close_side, float(pos.current_price), force_extended_hours=True)
                self._force_close_pending[sym] = {"reason": f"guardrail:{fail_reason}", "chase_count": 0}
                pnl = float(pos.unrealized_pl)
                closed_items.append({"symbol": sym, "qty": abs(qty), "reason": fail_reason, "pnl": pnl})
                log.info(f"GUARDRAIL EOD CLOSE {sym}: {abs(qty)} shares | {fail_reason} | P&L ${pnl:.2f}")
            except Exception as e:
                failed_items.append({"symbol": sym, "error": str(e)})
                log.error(f"GUARDRAIL EOD CLOSE failed {sym}: {e}")

        self._guardrail_eod_close_done = today

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
        thread) so it catches a stale order quickly, including after the initial
        close's regular-hours submit rolls into the after-hours session.
        ponytail: no cap on total re-chase attempts (only slip% is capped, at
        3%) -- a genuinely halted/no-bid symbol would retry indefinitely. Add a
        max-attempts giveup (with an alert) if that's ever observed live."""
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
                self._submit_closing_order(sym, abs(qty), side, float(pos.current_price), slip_pct=slip_pct, force_extended_hours=True)
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
        at 3%) -- same known ceiling as _sweep_force_closes."""
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

            submitted_at = getattr(pending, "submitted_at", None) or getattr(pending, "created_at", None)
            age_s = (now_utc - submitted_at).total_seconds() if submitted_at else 0.0
            if age_s < AFTERHOURS_CHASE_STALE_SECONDS:
                continue  # still fresh — give it time to fill

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
                req = LimitOrderRequest(
                    symbol=sym, qty=info["qty"], side=side, time_in_force=TimeInForce.DAY,
                    limit_price=fresh_limit,
                    client_order_id=f"apex-rechase-{sym}-{int(time.time())}",
                )
                new_order = self.client.submit_order(req)
                self.order_cache[sym] = new_order.id
                info["order_id"] = str(new_order.id)
                info["chase_count"] = chase_n + 1
                log.warning(
                    f"ENTRY RE-CHASE {sym}: unfilled after prior attempt "
                    f"— resubmitted @ ${fresh_limit:.2f} ({slip_pct:.1f}% off mid, attempt {chase_n + 1})"
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
        current: float, reference: Optional[float], is_long: bool, stop_pct: float,
    ) -> Optional[str]:
        """Pure decision logic for check_price_drift_stop: return a reason
        string if the adverse move versus `reference` (the price
        PRICE_DRIFT_LOOKBACK_MIN ago) exceeds stop_pct, else None. Split out
        for unit-testability without a broker connection. reference<=0 or
        None (not enough history yet) means "no signal", not a false
        trigger."""
        if reference is None or reference <= 0:
            return None
        drift = ((reference - current) / reference * 100) if is_long else ((current - reference) / reference * 100)
        if drift > stop_pct:
            return f"${reference:.2f}->${current:.2f} ({drift:+.1f}% vs {PRICE_DRIFT_LOOKBACK_MIN}min ago)"
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
        versus its own price PRICE_DRIFT_LOOKBACK_MIN (30 min) ago. Tighter
        and faster than the normal trailing stop -- see the PRICE_DRIFT_STOP
        block in config.py for why (2026-08-13, confirmed live: DFSC/HLIT/
        EROC/JACK all bought right at the open, all faded 4-8% before the
        wider trailing stop caught them; polling every 10 min instead of 30
        gives a fast 10-15 min collapse a real chance of being caught by the
        very next check). Longs: drop > PRICE_DRIFT_STOP_PCT%. Shorts: rise
        > PRICE_DRIFT_STOP_PCT% (mirrored). Scoped to same-day entries only
        (self._entry_log date), not by strategy -- survives the strategy-name
        loss a restart causes.

        Re-entry cooldown is automatic: detect_stopped_out_positions() (10s
        thread) applies AFTERHOURS_STOP_COOLDOWN_MIN to ANY position that
        disappears near a loss, regardless of which path closed it -- no
        separate cooldown logic needed here."""
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
            reference = history[0] if len(history) == lookback_ticks else self._backfill_drift_reference(sym)
            reason = self._drift_stop_reason(current, reference, is_long, PRICE_DRIFT_STOP_PCT)

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
