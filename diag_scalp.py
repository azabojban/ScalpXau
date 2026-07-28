"""Бір рет диагностика — неге сделка ашылмайды."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(message)s")

from xau_scalp.config import XauScalpSettings
from xau_scalp.indicator_intel import collect_raw_indicators, fetch_dxy_trend, resolve_trade_from_votes
from xau_scalp.liquidity import detect_sweep, sweep_to_side
from xau_scalp.news_filter import blocking_news
from xau_scalp.risk_guard import drawdown_blocks
from xau_scalp.session import in_kill_zone, session_name
from xau_scalp.state import can_trade
from xau_scalp.strategy import _spread_points, scan_scalp_signal
from xau_scalp.web_signals import get_cached_web_bias
from mt5_trade import MT5Trader

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


def main() -> None:
    settings = XauScalpSettings.load()
    now = datetime.now(timezone.utc)
    print(f"UTC: {now:%Y-%m-%d %H:%M} | session={session_name(now.hour, settings.kill_zones)}")
    print(f"kill_zone={in_kill_zone(settings.kill_zones, now)} zones={settings.kill_zones}")
    print(f"smart={settings.smart_indicators} lot={settings.lot} max_day={settings.max_trades_day}")

    ok, reason = can_trade(settings.state_file, settings.max_trades_day, settings.cooldown_sec)
    print(f"can_trade={ok} ({reason or 'ok'})")

    blocked, dd = drawdown_blocks(settings)
    print(f"drawdown_block={blocked} ({dd or 'ok'})")

    if settings.use_news_filter:
        nb, notes = blocking_news(
            block_minutes=settings.news_block_minutes,
            currencies=("USD",),
            block_high=True,
            block_medium=settings.news_block_medium,
        )
        print(f"news_block={nb} {notes[:2] if notes else ''}")

    trader = MT5Trader(dry_run=settings.dry_run)
    if not trader.connect():
        print("MT5 қосылмады")
        return

    sym = settings.symbol
    spread = _spread_points(trader, sym)
    print(f"spread={spread:.1f} pts (max={settings.max_spread_points})")

    pos = trader.open_positions_count(sym, settings.magic)
    print(f"open_positions(magic={settings.magic})={pos}")

    if mt5:
        tf_m5 = mt5.TIMEFRAME_M5
        tf_m1 = mt5.TIMEFRAME_M1
        tf_m15 = mt5.TIMEFRAME_M15
        tf_h1 = mt5.TIMEFRAME_H1
    else:
        tf_m5 = tf_m1 = tf_m15 = tf_h1 = 0

    m5 = trader.copy_rates(sym, tf_m5, 80)
    m1 = trader.copy_rates(sym, tf_m1, 120)
    m15 = trader.copy_rates(sym, tf_m15, 60)
    h1 = trader.copy_rates(sym, tf_h1, 60)
    point = trader.symbol_point(sym)

    sweep = detect_sweep(
        m5,
        swing_lookback=settings.swing_lookback,
        wick_min_points=settings.sweep_wick_min_points,
        point=point,
    )
    print(f"sweep={'YES ' + sweep.reason if sweep else 'NO — likvidlik sweep жоқ (негізгі себеп)'}")

    web = get_cached_web_bias(settings, force=True, m5=m5, m15=m15)
    print(f"web={web.summary if web else None} side={web.side if web else None}")

    if sweep and settings.smart_indicators:
        dxy_tr = fetch_dxy_trend(trader, settings)
        raw = collect_raw_indicators(
            sweep=sweep,
            web_bias=web,
            m1_closes=[b["close"] for b in m1],
            m5_closes=[b["close"] for b in m5],
            h1_closes=[b["close"] for b in h1],
            rsi_period=settings.rsi_period,
            dxy_trend_val=dxy_tr,
            use_web=settings.use_web_signals,
            use_rsi=settings.use_rsi_filter,
            use_ema=settings.use_ema_filter,
            use_h1=settings.use_h1_trend,
            use_dxy=settings.use_dxy_filter,
        )
        print("raw indicators:", {k: v.value if v else None for k, v in raw.items()})
        vote = resolve_trade_from_votes(settings, raw)
        print(f"smart_vote side={vote.side} | {vote.summary}")

    sig = scan_scalp_signal(trader, settings, invert=False)
    print(f"scan_scalp_signal={'SIGNAL ' + sig.side.value if sig else 'None'}")
    if sig is None and settings.consensus_entry and web:
        from xau_scalp.indicator_intel import try_consensus_entry
        c = try_consensus_entry(
            settings,
            web_bias=web,
            m1_closes=[b["close"] for b in m1],
            m5_closes=[b["close"] for b in m5],
            h1_closes=[b["close"] for b in h1],
            rsi_period=settings.rsi_period,
            dxy_trend_val=fetch_dxy_trend(trader, settings),
        )
        print(f"consensus_try={'OK ' + c.side.value if c and c.side else 'NO ' + (c.summary if c else 'fail')}")
    if sig:
        print(f"  conf={sig.confidence} lot={sig.lot} reason={sig.reason[:120]}")

    trader.shutdown()


if __name__ == "__main__":
    main()
