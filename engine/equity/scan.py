# Adaptive equity allocation based on pre-intelligence (market regime, signal quality, or pre-market indicators)
from engine.utils import MarketState
def get_adaptive_equity_allocation(market_state: MarketState, avg_signal_conf: float = None, premarket_strength: float = None) -> float:
    """
    Returns adaptive position size percentage for equities based on pre-intelligence.
    - In strong bull regime or high signal confidence, increase allocation.
    - In bear regime or weak signals, decrease allocation.
    - Optionally, use premarket_strength (0-1) if available.
    """
    from engine.config import POSITION_SIZE_PCT
    from engine.utils.market import get_allocation_split
    base = POSITION_SIZE_PCT
    equity_pct, _ = get_allocation_split(market_state)
    base *= equity_pct
    # Example logic: scale up in bull, down in bear
    if hasattr(market_state, 'resolve_regime'):
        bull = market_state.resolve_regime()
        if bull:
            base *= 1.2  # 20% more aggressive in bull
        else:
            base *= 0.8  # 20% more conservative in bear
    # If average signal confidence is provided, scale further
    if avg_signal_conf is not None:
        if avg_signal_conf > 0.85:
            base *= 1.15
        elif avg_signal_conf < 0.75:
            base *= 0.85
    # If premarket_strength is provided (0-1), scale linearly between 0.8x and 1.2x
    if premarket_strength is not None:
        base *= (0.8 + 0.4 * premarket_strength)
    # Clamp to reasonable bounds (e.g., 3% to 15%)
    return max(3.0, min(base, 15.0))
"""ApexTrader scan nucleus.

Contains reusable scanning functions for main loop and run_top3 tools.
"""

import datetime
import logging
import time
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Set, Optional

from engine import config as _cfg
from engine.config import (
    SCAN_WORKERS,
    SCAN_SYMBOL_TIMEOUT,
    MIN_DOLLAR_VOLUME,
    MIN_FLOAT_SHARES_REGULAR_HOURS,
    MIN_AVG_DAILY_VOLUME_REGULAR_HOURS,
    MIN_MARKET_CAP,
    MIN_STOCK_PRICE,
    LONG_ONLY_MODE,
    MIN_SIGNAL_CONFIDENCE,
    MAX_SIGNALS_PER_CYCLE,
    RVOL_MIN,
    MAX_GAP_CHASE_PCT,
    GAP_CHASE_CONSOL_BARS,
    GAP_CHASE_GUARD_ENABLED,
    HMM_REGIME_LOOKBACK_DAYS,
    HMM_REGIME_CONFIDENCE_BOOST,
    TRADE_THIN_LIQUIDITY_REJECTS,
    THIN_LIQUIDITY_EXCLUDED_STRATEGIES,
    EOD_CLOSE_TIME,
)
from engine.utils import MarketState, clear_bar_cache, get_bars, get_daily_volume_bars, is_dead_ticker, get_hmm_regime
from engine.utils.bars import get_data_client as _get_data_client
from alpaca.data import StockSnapshotRequest as _StockSnapshotRequest
from .universe import get_tier as _get_tier_live, get_latest_batch as _get_latest_batch, get_ti_primary as _get_ti_primary
from .discovery import get_alpaca_movers_queue as _get_alpaca_movers_queue

_ET  = pytz.timezone("America/New_York")
_log = logging.getLogger("ApexTrader")

# ── Adaptive Filter State ──
_adaptive_state = {
    "empty_scans": 0,
    "rvol_min": RVOL_MIN,
    "min_conf": MIN_SIGNAL_CONFIDENCE,
}
_ADAPTIVE_MAX_EMPTY = 3  # Number of empty scans before relaxing
_ADAPTIVE_MIN_RVOL = 1.2
_ADAPTIVE_MIN_CONF = 0.60
_ADAPTIVE_STEP_RVOL = 0.2
_ADAPTIVE_STEP_CONF = 0.03
from .strategies import get_strategy_instances, MomentumStrategy, TechnicalStrategy, SentimentStrategy, _get_float_shares, _get_market_cap

# ── Batch snapshot cache ──────────────────────────────────────────────────────
# Populated once at the start of each scan_universe() call via a single
# batch request.  _passes_guardrails() reads from this cache to avoid
# per-symbol 1-minute bars requests (390 bars × N symbols = dominant I/O cost).
_snapshot_cache: Dict = {}
_SNAPSHOT_STALE_SECONDS = 300  # snapshot's latest_trade older than this -> fall back to fresh intraday bars


def _prefetch_snapshots(symbols: List[str]) -> None:
    """Batch-fetch stock snapshots for *symbols* and store in _snapshot_cache.

    A single API call replaces N individual get_bars("1d","1m") requests in
    _passes_guardrails(), reducing scan latency significantly for large universes.
    Failures are silently swallowed — _passes_guardrails() falls back to bars.
    """
    global _snapshot_cache
    _snapshot_cache = {}
    if not symbols:
        return
    try:
        client = _get_data_client()
        snaps = client.get_stock_snapshot(
            _StockSnapshotRequest(symbol_or_symbols=symbols)
        )
        if isinstance(snaps, dict):
            _snapshot_cache = snaps
    except Exception:
        pass  # fall back to per-symbol get_bars in _passes_guardrails


# Every guardrail-rejection reason _passes_guardrails() can return that this
# path is allowed to rescue. Deliberately excludes:
#   - 'other': not a guardrail at all, the catch-all for non-guardrail skips
#     (stale data, symbol errors, etc.) this path was never meant to override.
#   - 'min_price': penny stocks stay hard-blocked even intraday, 2026-08-13
#     user request ("only avoid penny stocks") -- the one guardrail reason
#     that's about instrument quality (poor fill quality, wide spreads on
#     sub-MIN_STOCK_PRICE names) rather than liquidity/volume/momentum
#     thresholds this widening is meant to relax.
#   - 'dollar_vol', 'avg_volume', 'low_float', 'low_mcap' -- 2026-08-26, user
#     request ("shouldn't meet the non-negotiable limits of volume"):
#     removed. These four describe the security's actual tradability --
#     how much of it exists and how much really changes hands -- not a
#     noisy momentary reading, so admitting a signal that fails one of
#     them isn't "the setup looked thin for a second," it's "this stock
#     structurally doesn't have the volume to trade safely." Confirmed
#     live: RPGL admitted via dollar_vol (12x under the $900K floor,
#     0.0M float) and round-tripped for a real loss. rvol and gap_chase
#     stay admittable -- both are momentum-shape reads that can
#     legitimately wobble under/over their threshold minute to minute on
#     an otherwise normal-liquidity name.
_ALL_GUARDRAIL_REASONS = frozenset({
    'rvol', 'gap_chase',
})


def _should_admit_thin_liquidity(reason: Optional[str], market_state: Optional[MarketState] = None) -> bool:
    """True if a _passes_guardrails() rejection reason should be re-admitted
    (sized down via THIN_LIQUIDITY_POSITION_SIZE_PCT) instead of discarded.

    2026-08-12, user request, off by default (TRADE_THIN_LIQUIDITY_REJECTS).
    Originally only avg_volume/low_float qualified. Split out as its own
    function so this decision is unit-testable without driving the rest of
    scan_universe()'s threaded scan machinery.

    2026-08-13, user request ("no stocks to be held or traded overnight
    which fail guards"): regular-hours only. NRGV got admitted via this path
    at 16:02 ET (2 min after close, ext-hours) and sat failing the overnight
    guardrail all night until the no-gain-exit rule caught it at 06:37 the
    next morning -- an entry opened outside regular hours IS an overnight
    hold from the moment it fills, with no same-day close_guardrail_fail_
    positions run left to catch it before the close it already missed.

    2026-08-13, user request ("no guard rails for ANY scanner during intra
    day... check before closing end of day if the tickers pass guardrail,
    keep them overnight" -- refined same day to "only avoid penny stocks"):
    widened from avg_volume/low_float to every real guardrail reason except
    min_price -- RVOL, dollar_vol, gap_chase, avg_volume, and market cap were
    no longer hard-blocked at entry; penny stocks still are. The overnight
    side already enforces the real safety boundary regardless of which
    reasons got waived at entry: close_guardrail_fail_positions checks every
    open position, any strategy, against avg_volume/float/mcap at 15:45 ET
    and force-closes anything still failing -- that's the "check before
    closing end of day" the user asked for, already built (2026-08-12
    guardrail-fail overnight exit feature). This just stops the entry-side
    gate from being stricter than the exit-side one for intraday trades that
    are getting flattened by the close regardless.
    market_state=None (caller didn't pass one) fails closed -- no admit.

    2026-08-26, user request ("shouldn't meet the non-negotiable limits of
    volume"): narrowed back down. dollar_vol/avg_volume/low_float/low_mcap
    removed from _ALL_GUARDRAIL_REASONS -- those four describe actual
    tradability, not a momentary reading, and admitting a signal that fails
    one of them isn't the "thin for a second" case this path exists for.
    Only rvol and gap_chase remain admittable. See _ALL_GUARDRAIL_REASONS'
    own comment for the RPGL case this was measured against.

    2026-08-17, user request: also cut off at EOD_CLOSE_TIME (15:45 ET).
    is_regular_hours alone wasn't enough -- ASST and NUAI both got admitted
    at 15:57 ET, 12 min after close_guardrail_fail_positions' own once-per-
    day sweep already ran (gated on the same EOD_CLOSE_TIME) and marked
    itself done for the day. An admit past that point has no same-day
    guardrail check left to catch it before an overnight hold.
    """
    if not (TRADE_THIN_LIQUIDITY_REJECTS and reason in _ALL_GUARDRAIL_REASONS):
        return False
    if not (market_state and market_state.is_regular_hours):
        return False
    return market_state.now.strftime("%H:%M") < EOD_CLOSE_TIME


def _passes_guardrails(symbol: str, bull_regime: bool = None, market_state: Optional[MarketState] = None, return_reason: bool = False) -> bool:
    """Pre-scan gates: dollar-volume, RVOL, and gap-chase guard.
    Returns False to skip the symbol; never raises.

    bull_regime: pass the pre-computed regime from scan_universe() to avoid
    a concurrent re-fetch of _is_bull_regime() inside each worker thread.
    If None, falls back to calling _is_bull_regime() directly.

    market_state: shared MarketState for the current scan cycle. If None,
    it will be created lazily.
    """
    # return_reason is now an explicit argument
    try:
        # ── Fast path: use batch-prefetched snapshot (no per-symbol HTTP call) ─
        # Only trusted if latest_trade itself is recent — an unbounded-age snapshot
        # (thin after-hours book, dead feed for this symbol) previously fed straight
        # into every guardrail with no check at all.
        _snap = _snapshot_cache.get(symbol)
        _snap_fresh = False
        if (
            _snap is not None
            and _snap.daily_bar is not None
            and _snap.latest_trade is not None
        ):
            _trade_ts = getattr(_snap.latest_trade, "timestamp", None)
            if _trade_ts is None:
                _snap_fresh = True  # no timestamp to check — trust it, same as before
            else:
                if _trade_ts.tzinfo is None:
                    _trade_ts = _trade_ts.replace(tzinfo=datetime.timezone.utc)
                _snap_age = (datetime.datetime.now(datetime.timezone.utc) - _trade_ts).total_seconds()
                _snap_fresh = _snap_age <= _SNAPSHOT_STALE_SECONDS
                if not _snap_fresh:
                    _log.debug(f"[GUARDRAIL] {symbol}: snapshot stale ({_snap_age:.0f}s) — falling back to fresh intraday bars")

        if _snap_fresh:
            price   = float(_snap.latest_trade.price)
            day_vol = float(_snap.daily_bar.volume)
            open_px = float(_snap.daily_bar.open)
            prev_close = float(_snap.previous_daily_bar.close) if _snap.previous_daily_bar else 0.0
            intraday = None
        else:
            # ── Fallback: fetch 1-min intraday bars ───────────────────────────
            intraday = get_bars(symbol, "1d", "1m")
            if intraday.empty or len(intraday) < 5:
                if return_reason:
                    return False, 'other'
                return False
            price   = float(intraday["close"].iloc[-1])
            day_vol = float(intraday["volume"].sum())
            open_px = float(intraday["open"].iloc[0])
            prev_close = 0.0  # not available without an extra daily-bars call

        # true_day_vol: day_vol above is Alpaca/IEX-sourced — typically just a
        # few percent of real market volume (confirmed 2026-08-05, see
        # get_daily_volume_bars). avg_daily_vol below comes from yfinance's
        # full consolidated volume, so comparing raw day_vol against it — as
        # both RVOL gates below used to — compares apples to oranges and
        # crushes RVOL toward ~0 for everything (confirmed 2026-08-06: AAPL/
        # MSFT/NVDA all showing 0.01-0.07 RVOL mid-afternoon, blocking nearly
        # every candidate all day). Use yfinance's own running total for
        # today when its last bar actually is today; otherwise keep day_vol
        # rather than risk a stale number.
        true_day_vol = day_vol
        _vol_daily = get_daily_volume_bars(symbol)
        if not _vol_daily.empty and "time" in _vol_daily.columns:
            _last_bar_date = _vol_daily["time"].iloc[-1].date()
            if _last_bar_date == datetime.datetime.now(_ET).date():
                true_day_vol = float(_vol_daily["volume"].iloc[-1])

        # Resolve regime and VIX before adaptive gates
        vix = None
        if hasattr(market_state, 'vix') and market_state.vix is not None:
            vix = market_state.vix
        elif hasattr(market_state, 'resolve_vix'):
            vix, _, _ = market_state.resolve_vix()
        bull = bull_regime if bull_regime is not None else market_state.resolve_regime()

        # Adaptive MIN_STOCK_PRICE: more flexible for current market
        base_min_price = MIN_STOCK_PRICE
        base_dollar_vol = MIN_DOLLAR_VOLUME
        base_rvol = RVOL_MIN

        if bull:
            if vix and vix > 25:
                adaptive_min_price = base_min_price + 0.5
                adaptive_dollar_vol = base_dollar_vol * 1.2
                adaptive_rvol = base_rvol + 0.3
            elif vix and vix >= 18:
                adaptive_min_price = base_min_price
                adaptive_dollar_vol = base_dollar_vol
                adaptive_rvol = max(1.2, base_rvol - 0.3)
            elif vix and vix >= 15:
                adaptive_min_price = base_min_price
                adaptive_dollar_vol = base_dollar_vol * 0.9
                adaptive_rvol = max(1.0, base_rvol - 0.5)
            else:
                adaptive_min_price = max(1.0, base_min_price - 0.5)
                adaptive_dollar_vol = base_dollar_vol * 0.8
                adaptive_rvol = max(0.9, base_rvol - 0.6)
        else:
            if vix and vix < 18:
                adaptive_min_price = max(1.0, base_min_price - 0.7)
                adaptive_dollar_vol = base_dollar_vol * 0.6
                adaptive_rvol = max(0.8, base_rvol - 0.7)
            else:
                adaptive_min_price = max(1.0, base_min_price - 0.5)
                adaptive_dollar_vol = base_dollar_vol * 0.75
                adaptive_rvol = max(1.0, base_rvol - 0.4)

        if price < adaptive_min_price:
            _log.warning(f"[GUARDRAIL] {symbol} blocked: price {price:.2f} < adaptive_min_price {adaptive_min_price}")
            if return_reason:
                return False, 'min_price'
            return False

        # Adaptive RVOL_MIN: higher in bull/high VIX, lower in calm or bear conditions
        # Use regular market hours only so extended-hours volume does not distort the pace.
        if market_state.is_regular_hours and bull:
            daily = get_daily_volume_bars(symbol)
            if not daily.empty and len(daily) >= 2:
                avg_daily_vol = float(daily["volume"].iloc[:-1].mean())
                if avg_daily_vol > 0:
                    now_et       = datetime.datetime.now(_ET)
                    mkt_open     = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                    elapsed_min  = max((now_et - mkt_open).total_seconds() / 60, 1.0)
                    elapsed_frac = min(elapsed_min / 390.0, 1.0)
                    rvol = (true_day_vol / max(elapsed_frac, 0.02)) / avg_daily_vol
                    if rvol < adaptive_rvol:
                        _log.warning(f"[GUARDRAIL] {symbol} blocked: RVOL {rvol:.2f} < adaptive_rvol {adaptive_rvol:.2f} | day_vol={true_day_vol:.0f} | avg_daily_vol={avg_daily_vol:.0f}")
                        if return_reason:
                            return False, 'rvol'
                        return False

        # Adaptive MIN_DOLLAR_VOLUME: more flexible for current market
        dollar_vol = price * true_day_vol
        if dollar_vol < adaptive_dollar_vol:
            _log.warning(f"[GUARDRAIL] {symbol} blocked: dollar volume {dollar_vol:.0f} < adaptive_dollar_vol {adaptive_dollar_vol:.0f} | price={price:.2f} | day_vol={true_day_vol:.0f}")
            if return_reason:
                return False, 'dollar_vol'
            return False

        # Liquidity / quality floor — skip thin, low-float, micro-cap names prone to
        # violent, illiquid moves. 2026-08-23, user request: combined the old
        # two-layer system (an absolute hard floor plus a separate, session-
        # gated regular/pre-after-hours floor) into one flat set, applied the
        # same regardless of time of day or session: float > 10M, avg daily
        # volume >= 700K. See MIN_FLOAT_SHARES_REGULAR_HOURS/
        # MIN_AVG_DAILY_VOLUME_REGULAR_HOURS in config.py (names kept for the
        # overnight guardrail-fail check in enhanced.py, which still uses
        # them independently).
        daily = get_daily_volume_bars(symbol)
        if not daily.empty and len(daily) >= 2:
            avg_daily_vol = float(daily["volume"].iloc[:-1].mean())
            if avg_daily_vol < MIN_AVG_DAILY_VOLUME_REGULAR_HOURS:
                _log.warning(f"[GUARDRAIL] {symbol} blocked: avg daily volume {avg_daily_vol:.0f} < {MIN_AVG_DAILY_VOLUME_REGULAR_HOURS:.0f}")
                if return_reason:
                    return False, 'avg_volume'
                return False

        shares_float = _get_float_shares(symbol)
        if shares_float is not None and shares_float <= MIN_FLOAT_SHARES_REGULAR_HOURS:
            _log.warning(f"[GUARDRAIL] {symbol} blocked: float {shares_float/1e6:.1f}M <= {MIN_FLOAT_SHARES_REGULAR_HOURS/1e6:.0f}M")
            if return_reason:
                return False, 'low_float'
            return False

        market_cap = _get_market_cap(symbol)
        if market_cap is not None and market_cap < MIN_MARKET_CAP:
            _log.warning(f"[GUARDRAIL] {symbol} blocked: market cap ${market_cap/1e6:.0f}M < ${MIN_MARKET_CAP/1e6:.0f}M")
            if return_reason:
                return False, 'low_mcap'
            return False

        # RVOL gate (adaptive)
        if market_state.is_market_open and bull:
            daily = get_daily_volume_bars(symbol)
            if not daily.empty and len(daily) >= 2:
                avg_daily_vol = float(daily["volume"].iloc[:-1].mean())
                if avg_daily_vol > 0:
                    now_et       = datetime.datetime.now(_ET)
                    mkt_open     = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
                    elapsed_min  = max((now_et - mkt_open).total_seconds() / 60, 1.0)
                    elapsed_frac = min(elapsed_min / 390.0, 1.0)
                    rvol = (true_day_vol / max(elapsed_frac, 0.02)) / avg_daily_vol
                    if rvol < adaptive_rvol:
                        _log.warning(f"[GUARDRAIL] {symbol} blocked: RVOL {rvol:.2f} < adaptive_rvol {adaptive_rvol:.2f} | day_vol={true_day_vol:.0f} | avg_daily_vol={avg_daily_vol:.0f}")
                        if return_reason:
                            return False, 'rvol'
                        return False

        # Adaptive MAX_GAP_CHASE_PCT using market regime and VIX
        vix = None
        if hasattr(market_state, 'vix') and market_state.vix is not None:
            vix = market_state.vix
        elif hasattr(market_state, 'resolve_vix'):
            vix, _, _ = market_state.resolve_vix()
        bull = bull_regime if bull_regime is not None else market_state.resolve_regime()
        base_gap = MAX_GAP_CHASE_PCT
        if bull:
            if vix and vix > 25:
                adaptive_gap = min(20.0, base_gap + 5.0)
            else:
                adaptive_gap = base_gap
        else:
            if vix and vix < 18:
                adaptive_gap = max(10.0, base_gap - 5.0)
            else:
                adaptive_gap = max(12.0, base_gap - 3.0)

        # Gap-chase guard: skip if up >adaptive_gap% without a tight consolidation base.
        # Checked against BOTH today's tracked open (intraday chase) and the prior
        # close (overnight/pre-market gap) — a stock that already gapped huge before
        # its first tracked bar of the day would show ~0% day_gain and slip through
        # the open_px check alone, since that check resets its baseline to the
        # already-elevated open.
        gap_vs_open  = ((price - open_px) / open_px) * 100 if open_px > 0 else 0.0
        gap_vs_prev  = ((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
        if GAP_CHASE_GUARD_ENABLED and max(gap_vs_open, gap_vs_prev) > adaptive_gap:
            # Always check for consolidation on gapped-up stocks, even on snapshot path.
            # This is a critical risk-management gate to avoid chasing.
            # If intraday bars weren't fetched before, get them now.
            if intraday is None:
                intraday = get_bars(symbol, "1d", "1m")

            if intraday is not None and not intraday.empty and len(intraday) >= GAP_CHASE_CONSOL_BARS:
                last_n = intraday.iloc[-GAP_CHASE_CONSOL_BARS:]
                bar_range = float(last_n["high"].max() - last_n["low"].min())
                # If the range of the last few bars is > 2% of the price, it's not consolidating.
                if bar_range > price * 0.02:
                    _log.debug(f"[GUARDRAIL] {symbol} blocked: gap chase, bar range {bar_range:.2f} > 2% of price {price:.2f}")
                    if return_reason:
                        return False, 'gap_chase'
                    return False

        if return_reason:
            return True, None
        return True
    except Exception as e:
        _log.warning(f"Guardrail check failed for {symbol}: {e} — skipping symbol")
        if return_reason:
            return False, 'other'
        return False  # fail-safe: block on error, never bypass guardrails


# 2026-08-26, user request ("thinly traded stocks should be removed from
# universe to avoid too much fetchload to alpaca" / "ensure thinly traded
# stocks removed from list"): a symbol used to sit in the scan target list
# with genuinely thin liquidity for its whole time there -- it got fully
# scanned (Alpaca intraday bars, snapshot prefetch, strategy evaluation)
# every cycle, and only got rejected AFTER all that work, at the
# _passes_guardrails() avg-daily-volume check. This pre-filters it out of
# get_scan_targets() entirely, using the same threshold/data source as that
# guardrail (get_daily_volume_bars, MIN_AVG_DAILY_VOLUME_REGULAR_HOURS) so
# it's not a new liquidity bar, just enforced earlier.
#
# Cached separately from get_daily_volume_bars()'s own cache, which
# clear_bar_cache() wipes every single scan cycle (1-3 min) -- 3-month
# average daily volume doesn't meaningfully change within an hour, so a
# 1-hour TTL here avoids re-fetching the same yfinance data every cycle.
_thin_check_cache: Dict[str, Tuple[float, bool]] = {}
_THIN_CHECK_TTL_SEC = 3600


def _is_thinly_traded(symbol: str) -> bool:
    """True if symbol's 3mo avg daily volume is below MIN_AVG_DAILY_VOLUME_REGULAR_HOURS.
    Fails OPEN (not thin) on missing/errored data -- same as the guardrail's
    own behavior, so a data hiccup doesn't wrongly prune a real symbol.
    """
    now = time.time()
    cached = _thin_check_cache.get(symbol)
    if cached is not None and (now - cached[0]) < _THIN_CHECK_TTL_SEC:
        return cached[1]
    is_thin = False
    try:
        daily = get_daily_volume_bars(symbol)
        if not daily.empty and len(daily) >= 2:
            avg_daily_vol = float(daily["volume"].iloc[:-1].mean())
            is_thin = avg_daily_vol < MIN_AVG_DAILY_VOLUME_REGULAR_HOURS
    except Exception as e:
        _log.debug(f"{symbol}: thin-liquidity pre-check failed, failing open: {e}")
    _thin_check_cache[symbol] = (now, is_thin)
    return is_thin


def get_scan_targets(excluded: Set[str] = None) -> List[str]:
    """Equity scan universe = Alpaca-movers queue + top TI_PRIMARY_SCAN_BATCH_LIMIT
    tickers from the latest Trade Ideas capture (data/ti_primary.json), TI in
    its own rank order.

    2026-08-26, user request ("reduce the number of signals to what TI
    provides... top 20... the universe of stocks should limit to the latest
    trade ideas scrapping"): replaced the old multi-source assembly (EDGAR/
    sympathy/Alpaca-movers/watchlist priority queue, an inverse-ETF/
    BEAR_SHORT_UNIVERSE bear-regime seed, and an ~80-symbol rotating fallback
    universe — routinely 90-150+ symbols/cycle, confirmed live) with just TI
    top-N. ti_primary.json is refreshed in place by the ApexTraderTICapture
    scheduled task (3 min 8:25-9:30 ET, 10 min 9:30-14:50 ET), so this
    naturally tracks TI's latest read all day — no separate freeze/snapshot
    needed; whatever's newest in the file each cycle IS the universe.

    Same day, follow-up request ("remove edgar and sympathy but keep alpaca
    movers along with trade ideas.com"): Alpaca-movers added back in via its
    own dedicated queue (_alpaca_movers_queue / get_alpaca_movers_queue(),
    engine/equity/discovery.py) — separate from the EDGAR/sympathy/watchlist
    queue, which stays excluded from equity scan (still feeds the options
    scan). Backtest evidence for keeping movers: 2026-08-26 fills showed
    Alpaca-movers-sourced trades at 58.8% win rate / +$15.10 net vs.
    TI/other's 34.7% / -$17.82 (small sample, one outlier trade drove most of
    the movers P&L — not a confident signal, just the reason this wasn't
    reverted with EDGAR/sympathy).

    Falls back to the static config lists (get_dynamic_universe) only when
    ti_primary.json is critically thin/empty (_MIN_TI) — a TI-outage safety
    net, not a routine noise source. is_dead_ticker() (engine/utils/bars.py)
    still strips out names with persistent stale/empty data.
    """
    if excluded is None:
        excluded = set()

    delisted = set(_cfg.DELISTED_STOCKS)

    ti_primary = [s for s in _get_ti_primary() if s not in delisted]

    # Universe health check
    _MIN_TI = 5
    if len(ti_primary) < _MIN_TI:
        if len(ti_primary) == 0:
            _log.error("[UNIVERSE HEALTH] ti_primary.json is empty! No tickers to scan. Check data pipeline.")
        else:
            _log.warning(f"[UNIVERSE HEALTH] ti_primary.json too small ({len(ti_primary)}). Falling back to static config lists.")
        p1, p2, _ = _cfg.get_dynamic_universe()
        ti_slice = list(dict.fromkeys(p2 + p1))
        if len(ti_slice) == 0:
            _log.error("[UNIVERSE HEALTH] Static universe lists are empty! No tickers to scan. Check config/universe sources.")
    else:
        ti_slice = list(dict.fromkeys(ti_primary))[:_cfg.TI_PRIMARY_SCAN_BATCH_LIMIT]

    movers = [s for s in _get_alpaca_movers_queue() if s not in delisted]
    # 2026-08-26, user request ("keep it to top 30 signals together"): cap the
    # COMBINED (movers + TI) list at TI_PRIMARY_SCAN_BATCH_LIMIT, not each
    # source separately -- movers get priority (fresher/news-driven) and TI
    # fills whatever's left, up to the shared cap.
    base = list(dict.fromkeys(movers + ti_slice))[:_cfg.TI_PRIMARY_SCAN_BATCH_LIMIT]

    targets = []
    for s in base:
        if s in excluded or s in delisted or is_dead_ticker(s):
            continue
        if _is_thinly_traded(s):
            continue
        targets.append(s)
    return targets


def scan_universe(scan_targets: List[str], sentiment: str, market_state: MarketState) -> Tuple[List, Dict[str, int], int]:
    clear_bar_cache()

    # Batch-prefetch stock snapshots for all scan targets in one API call.
    # Populates _snapshot_cache so _passes_guardrails() avoids per-symbol
    # get_bars("1d","1m") requests — the dominant I/O cost of each scan cycle.
    _prefetch_snapshots(scan_targets)

    # Compute regime ONCE here before spawning workers — avoids a thread race where
    # multiple workers concurrently hit the 15-min TTL expiry and each make a
    # separate get_bars("SPY") call to refresh the shared _regime_cache dict.
    bull_regime = market_state.resolve_regime()
    regime_str  = "bull" if bull_regime else "bear"
    strats = get_strategy_instances(bull_regime)


    signals = []
    hit_counts = {}
    scan_errors = 0
    guardrail_rejections = {
        'dollar_vol': 0,
        'rvol': 0,
        'gap_chase': 0,
        'min_price': 0,
        'avg_volume': 0,
        'low_float': 0,
        'low_mcap': 0,
        'other': 0
    }
    thin_liquidity_stats = {'admitted': 0}  # rejected-list symbols scanned anyway; see TRADE_THIN_LIQUIDITY_REJECTS

    def _scan_one(symbol: str):
        # Dead-ticker check already done in get_scan_targets() — skip here.
        # Pass pre-computed regime into guardrails to avoid re-calling _is_bull_regime()
        # Custom: get rejection reason from _passes_guardrails
        passed, reason = _passes_guardrails(symbol, bull_regime=bull_regime, market_state=market_state, return_reason=True)
        thin_liquidity = False
        if not passed:
            if reason in guardrail_rejections:
                guardrail_rejections[reason] += 1
            else:
                guardrail_rejections['other'] += 1
            # Rejection itself is unchanged and still counted above — this is a
            # separate, toggleable path on top of it: a symbol rejected for ONLY
            # thin float/volume still gets scanned, just flagged so _execute_entry
            # sizes it at THIN_LIQUIDITY_POSITION_SIZE_PCT instead of skipping it
            # outright. min_price/RVOL/dollar_vol/mcap/gap_chase are never rescued.
            if _should_admit_thin_liquidity(reason, market_state):
                thin_liquidity = True
                thin_liquidity_stats['admitted'] += 1
            else:
                return None

        candidates = []
        for s in strats:
            try:
                if isinstance(s, TechnicalStrategy):
                    sig = s.scan(symbol, sentiment)
                elif isinstance(s, SentimentStrategy):
                    sig = s.scan(symbol, sentiment)
                elif isinstance(s, MomentumStrategy):
                    sig = s.scan(symbol, regime_str)
                else:
                    sig = s.scan(symbol)
                if sig:
                    candidates.append(sig)
            except Exception as _ex:
                _log.debug(f"[SCAN] {symbol} {type(s).__name__}: {_ex}")

        if not candidates:
            return None
        best = max(candidates, key=lambda s: s.confidence)
        if thin_liquidity:
            # 2026-08-15: the guardrail-admit decision above happens before we
            # know which strategy will actually fire (it's a symbol-level gate,
            # strategies get scanned after). ORB/GapBreakout measured net-
            # negative specifically on their bypass trades (see
            # THIN_LIQUIDITY_EXCLUDED_STRATEGIES in config.py) -- now that
            # `best` tells us the winning strategy, drop the signal entirely
            # for those two instead of admitting it at reduced size.
            if best.strategy in THIN_LIQUIDITY_EXCLUDED_STRATEGIES:
                return None
            best.thin_liquidity = True

        # Per-symbol HMM regime alignment: confidence bonus only, never a gate.
        # Buys get a boost when the symbol's own 2-state HMM regime is bullish;
        # shorts/sells get it when that regime is bearish.
        hmm_bull = get_hmm_regime(symbol, HMM_REGIME_LOOKBACK_DAYS)
        if hmm_bull is not None:
            aligned = (best.action == "buy" and hmm_bull) or (best.action in ("sell", "short") and not hmm_bull)
            if aligned:
                best.confidence = round(min(best.confidence + HMM_REGIME_CONFIDENCE_BOOST, 0.97), 3)

        return best



    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        future_map = {pool.submit(_scan_one, sym): sym for sym in scan_targets}
        for future in as_completed(future_map):
            sym = future_map[future]
            try:
                sig = future.result(timeout=SCAN_SYMBOL_TIMEOUT)
                if sig:
                    signals.append(sig)
                    hit_counts[sig.strategy] = hit_counts.get(sig.strategy, 0) + 1
            except Exception as e:
                scan_errors += 1
                _log.error(f"[SCAN ERROR] {sym}: {e}")

    # Log guardrail rejection summary
    total_rejected = sum(guardrail_rejections.values())
    if total_rejected > 0:
        _log.info(
            f"[GUARDRAIL SUMMARY] Rejected: {total_rejected} | DollarVol: {guardrail_rejections['dollar_vol']} | "
            f"RVOL: {guardrail_rejections['rvol']} | GapChase: {guardrail_rejections['gap_chase']} | "
            f"MinPrice: {guardrail_rejections['min_price']} | AvgVolume: {guardrail_rejections['avg_volume']} | "
            f"LowFloat: {guardrail_rejections['low_float']} | "
            f"LowMcap: {guardrail_rejections['low_mcap']} | "
            f"Other: {guardrail_rejections['other']}"
            + (f" | ThinLiquidityAdmitted: {thin_liquidity_stats['admitted']}" if thin_liquidity_stats['admitted'] else "")
        )

    signals.sort(key=lambda x: x.confidence, reverse=True)
    # Adaptive confidence filter using pre-intelligence (market regime, VIX)
    vix = None
    if hasattr(market_state, 'vix') and market_state.vix is not None:
        vix = market_state.vix
    elif hasattr(market_state, 'resolve_vix'):
        vix, _, _ = market_state.resolve_vix()
    bull = market_state.resolve_regime()
    base_conf = MIN_SIGNAL_CONFIDENCE
    if bull:
        if vix and vix > 25:
            adaptive_conf = min(0.80, base_conf + 0.05)  # stricter in high-vol bull
        else:
            adaptive_conf = base_conf
    else:
        if vix and vix < 18:
            adaptive_conf = max(0.65, base_conf - 0.05)  # looser in calm bear
        else:
            adaptive_conf = max(0.68, base_conf - 0.02)
    signals = [s for s in signals if s.confidence >= adaptive_conf]

    # Dynamic sector/industry weighting cap
    # Limit to max 3 signals per sector (can be tuned)
    from collections import defaultdict
    sector_cap = 3
    sector_counts = defaultdict(int)
    filtered_signals = []
    for sig in signals:
        sector = getattr(sig, 'sector', None)
        if sector is None:
            filtered_signals.append(sig)  # If no sector info, allow
            continue
        if sector_counts[sector] < sector_cap:
            filtered_signals.append(sig)
            sector_counts[sector] += 1
    signals = filtered_signals
    if LONG_ONLY_MODE:
        # Long-only enforcement: drop sell/short signals only when LONG_ONLY_MODE is active
        pre_len = len(signals)
        signals = [s for s in signals if s.action == "buy"]
        if len(signals) != pre_len:
            _log.info(f"Long-only enforced in scan_universe: dropping {pre_len-len(signals)} short signals")

    # Adaptive filter logic: relax after N empty scans, reset after success
    if len(signals) == 0:
        _adaptive_state["empty_scans"] += 1
        if _adaptive_state["empty_scans"] >= _ADAPTIVE_MAX_EMPTY:
            # Relax RVOL and confidence stepwise
            if _adaptive_state["rvol_min"] > _ADAPTIVE_MIN_RVOL:
                _adaptive_state["rvol_min"] = max(_ADAPTIVE_MIN_RVOL, _adaptive_state["rvol_min"] - _ADAPTIVE_STEP_RVOL)
                _log.info(f"[ADAPTIVE] Lowered RVOL_MIN to {_adaptive_state['rvol_min']:.2f}")
            if _adaptive_state["min_conf"] > _ADAPTIVE_MIN_CONF:
                _adaptive_state["min_conf"] = max(_ADAPTIVE_MIN_CONF, _adaptive_state["min_conf"] - _ADAPTIVE_STEP_CONF)
                _log.info(f"[ADAPTIVE] Lowered MIN_SIGNAL_CONFIDENCE to {_adaptive_state['min_conf']:.2f}")
    else:
        if _adaptive_state["empty_scans"] > 0:
            _log.info(f"[ADAPTIVE] Resetting adaptive filters after successful scan.")
        _adaptive_state["empty_scans"] = 0
        _adaptive_state["rvol_min"] = RVOL_MIN
        _adaptive_state["min_conf"] = MIN_SIGNAL_CONFIDENCE
    return signals, hit_counts, scan_errors


def filter_signals(signals, long_only: bool = False, min_conf: float = 0.0, cap: int = None):
    if long_only:
        signals = [s for s in signals if s.action == "buy"]

    signals = [s for s in signals if s.confidence >= min_conf]

    if cap is not None:
        signals = signals[:cap]
    return signals
