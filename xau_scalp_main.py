"""XAUUSD скальпинг бот — entry point."""

from __future__ import annotations

import asyncio
import logging
import sys

from xau_scalp.config import XauScalpSettings
from xau_scalp.risk_guard import required_min_confidence
from xau_scalp.runner import run_cycle
from mt5_trade import MT5Trader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("xau_scalp_main")


async def run_loop(settings: XauScalpSettings) -> None:
    trader = MT5Trader(dry_run=settings.dry_run)
    if not trader.connect():
        logger.error("MT5 қосылмады")
        sys.exit(1)

    interval = max(5, settings.poll_seconds)
    zones = ",".join(f"{a}-{b}" for a, b in settings.kill_zones)
    logger.info(
        "XAUUSD pro skalp (%s, poll=%ss, lot=%s, DRY_RUN=%s, magic=%s, zones=%s, smart=%s, invert=%s, adaptive=%s, conf≥%s, dd=-%s)",
        settings.symbol,
        interval,
        settings.lot,
        settings.dry_run,
        settings.magic,
        zones,
        "ON" if settings.smart_indicators else "off",
        "ON" if settings.invert_signals and not settings.adaptive_mode and not settings.smart_indicators else "off",
        "ON" if settings.adaptive_mode else "off",
        required_min_confidence(settings),
        int(settings.max_daily_loss),
    )
    if settings.smart_indicators:
        logger.info(
            "Smart indicators: әр индикатор follow/invert оқылады (min_tr=%s, min_w=%s)",
            settings.indicator_min_trades,
            int(settings.indicator_min_weight),
        )
    if settings.consensus_entry:
        logger.info(
            "Consensus entry: sweep жоқ → web Strong + EMA/H1 + vote (conf≥%s)",
            settings.consensus_min_confidence,
        )

    try:
        while True:
            try:
                run_cycle(trader, settings)
            except Exception as exc:
                logger.exception("Цикл қате: %s", exc)
            await asyncio.sleep(interval)
    finally:
        trader.shutdown()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="XAUUSD скальпинг bot")
    parser.add_argument("--once", action="store_true", help="Бір рет тексеру")
    args = parser.parse_args()

    settings = XauScalpSettings.load()

    if args.once:

        async def _once() -> None:
            trader = MT5Trader(dry_run=settings.dry_run)
            trader.connect()
            try:
                run_cycle(trader, settings)
            finally:
                trader.shutdown()

        asyncio.run(_once())
    else:
        asyncio.run(run_loop(settings))


if __name__ == "__main__":
    main()
