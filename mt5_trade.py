"""MetaTrader 5 ордерлері."""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

from side import Side

logger = logging.getLogger(__name__)

_last_symbol_warn: dict[str, float] = {}

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore

# symbol_info.filling_mode биттері (MQL5 SYMBOL_FILLING_*)
_FILL_IOC = 2
_FILL_FOK = 1


def _filling_candidates(filling_mode: int) -> list[int]:
    """Broker қолдайтын order filling реті."""
    if mt5 is None:
        return []
    order: list[int] = []
    if filling_mode & _FILL_IOC:
        order.append(mt5.ORDER_FILLING_IOC)
    if filling_mode & _FILL_FOK:
        order.append(mt5.ORDER_FILLING_FOK)
    order.append(mt5.ORDER_FILLING_RETURN)
    seen: set[int] = set()
    out: list[int] = []
    for f in order:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _send_deal(request: dict[str, Any]) -> Any:
    if mt5 is None:
        return None
    symbol = request["symbol"]
    info = mt5.symbol_info(symbol)
    fill_mode = info.filling_mode if info else 0
    last = None
    for type_filling in _filling_candidates(fill_mode):
        req = {**request, "type_filling": type_filling}
        last = mt5.order_send(req)
        if last is None:
            continue
        if last.retcode == mt5.TRADE_RETCODE_DONE:
            return last
        invalid_fill = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)
        if last.retcode == invalid_fill:
            continue
        return last
    return last


def _volume_digits(step: float) -> int:
    s = f"{step:.8f}".rstrip("0")
    if "." in s:
        return len(s.split(".")[1])
    return 0


def calc_partial_close_volume(
    total: float, pct: int, vmin: float, vstep: float
) -> float:
    """Save X%: позиция көлемінің X%-ін жабу (broker step/min ескеріледі)."""
    pct = max(1, min(100, int(pct)))
    if total <= 0 or vstep <= 0:
        return 0.0
    digits = _volume_digits(vstep)
    raw = total * pct / 100.0
    close = math.floor(raw / vstep + 1e-9) * vstep
    close = round(close, digits)
    if close < vmin:
        if pct >= 100 or total <= vmin:
            return round(total, digits)
        if total - vmin >= vmin:
            return round(vmin, digits)
        return 0.0
    remain = round(total - close, digits)
    if remain > 0 and remain < vmin:
        return round(total, digits)
    return close


def _normalize_price(symbol: str, price: float) -> float:
    if mt5 is None:
        return price
    info = mt5.symbol_info(symbol)
    if info is None:
        return price
    return round(price, info.digits)


def _entry_pending_type(side: Side, entry: float, bid: float, ask: float) -> int:
    """Кіру бағасына жету үшін limit немесе stop pending."""
    if mt5 is None:
        return 0
    if side == Side.SELL:
        return mt5.ORDER_TYPE_SELL_LIMIT if entry > ask else mt5.ORDER_TYPE_SELL_STOP
    return mt5.ORDER_TYPE_BUY_LIMIT if entry < bid else mt5.ORDER_TYPE_BUY_STOP


class MT5Trader:
    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run
        self._connected = False
        self._pending_placed_at: dict[int, float] = {}

    def connect(self, path: Optional[str] = None) -> bool:
        if mt5 is None:
            if self.dry_run:
                logger.info("DRY_RUN: MetaTrader5 пакеті жоқ, баға/ордер — mock")
            else:
                logger.error("MetaTrader5 орнатылмаған: pip install MetaTrader5")
            return self.dry_run
        ok = mt5.initialize(path) if path else mt5.initialize()
        self._connected = bool(ok)
        if not ok:
            logger.error("MT5 initialize сәтсіз: %s", mt5.last_error())
        elif not self.dry_run:
            ai = mt5.account_info()
            if ai:
                logger.info(
                    "MT5 қосылды: %s #%s %s",
                    ai.server,
                    ai.login,
                    ai.name,
                )
        return self._connected

    def ensure_connected(self, path: Optional[str] = None) -> bool:
        """MT5 terminal байланысын тексеру / qayta qosylu."""
        if mt5 is None:
            return self.dry_run
        if self._connected:
            ti = mt5.terminal_info()
            if ti is not None and ti.connected:
                return True
            logger.warning("MT5 байланысы үзілген — qayta qosyludamyz...")
            self._connected = False
            try:
                mt5.shutdown()
            except Exception:
                pass
        return self.connect(path)

    def _ensure_symbol(self, symbol: str) -> bool:
        """symbol_select + retry; warning 60s сайын."""
        if mt5 is None:
            return self.dry_run
        if not self.ensure_connected():
            return False
        for _ in range(3):
            if mt5.symbol_select(symbol, True):
                return True
            time.sleep(0.5)
        err = mt5.last_error()
        now = time.time()
        last = _last_symbol_warn.get(symbol, 0.0)
        if now - last >= 60:
            _last_symbol_warn[symbol] = now
            ti = mt5.terminal_info()
            connected = getattr(ti, "connected", None) if ti else None
            logger.warning(
                "symbol_select сәтсіз: %s (%s) terminal_connected=%s — "
                "MT5 ashik, XAUUSD Market Watch, Algo Trading",
                symbol,
                err,
                connected,
            )
        return False

    def resolve_symbol(self, symbol: str, fallbacks: tuple[str, ...] = ()) -> str | None:
        """Негізгі символ + fallback; табылса MT5 атауын қайтарады."""
        for name in (symbol, *fallbacks):
            if not name:
                continue
            if self._ensure_symbol(name):
                if name != symbol:
                    logger.info("Symbol resolve: %s → %s", symbol, name)
                return name
        return None

    def sync_pending_times(self, symbol: str, magic: int = 202607) -> None:
        """Бот қайта іске қосылғанда MT5 pending уақыттарын жадқа алу."""
        if self.dry_run or not mt5 or not self._connected:
            return
        orders = mt5.orders_get(symbol=symbol)
        if not orders:
            return
        for o in orders:
            if o.magic != magic:
                continue
            ticket = int(o.ticket)
            setup_msc = getattr(o, "time_setup_msc", 0) or 0
            if setup_msc > 0:
                self._pending_placed_at[ticket] = setup_msc / 1000.0
                continue
            setup = getattr(o, "time_setup", None)
            if setup is not None and hasattr(setup, "timestamp"):
                self._pending_placed_at[ticket] = setup.timestamp()
        if self._pending_placed_at:
            logger.info(
                "Pending sync: %s ордер(ler) уақыты жүктелді (%s)",
                len(self._pending_placed_at),
                symbol,
            )

    def shutdown(self) -> None:
        if mt5 and self._connected:
            mt5.shutdown()
        self._connected = False

    def tick_bid(self, symbol: str) -> Optional[float]:
        if self.dry_run and not self._connected:
            return 3990.0
        if mt5 is None:
            return None
        if not self._ensure_symbol(symbol):
            return None
        tick = mt5.symbol_info_tick(symbol)
        return float(tick.bid) if tick else None

    def tick_ask(self, symbol: str) -> Optional[float]:
        if self.dry_run and not self._connected:
            return 3992.0
        if mt5 is None:
            return None
        if not self._ensure_symbol(symbol):
            return None
        tick = mt5.symbol_info_tick(symbol)
        return float(tick.ask) if tick else None

    def open_market(
        self,
        symbol: str,
        side: Side,
        lot: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: str = "tg_signal",
        magic: int = 202607,
    ) -> Optional[int]:
        if self.dry_run:
            logger.info(
                "DRY_RUN OPEN %s %s lot=%s sl=%s tp=%s (MT5-ке жіберілмейді)",
                side.value,
                symbol,
                lot,
                sl,
                tp,
            )
            return 0

        if not mt5 or not self._connected:
            logger.error("MT5 қосылмаған — ордер жіберілмеді")
            return None

        if not self._ensure_symbol(symbol):
            return None

        info = mt5.symbol_info(symbol)
        if info is None:
            logger.error("symbol_info жоқ: %s", symbol)
            return None

        order_type = mt5.ORDER_TYPE_BUY if side == Side.BUY else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error("symbol_info_tick жоқ: %s", symbol)
            return None
        price = tick.ask if side == Side.BUY else tick.bid

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if sl is not None:
            request["sl"] = _normalize_price(symbol, sl)
        if tp is not None:
            request["tp"] = _normalize_price(symbol, tp)

        result = _send_deal(request)
        if result is None:
            logger.error("order_send None: %s", mt5.last_error())
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("order_send retcode=%s comment=%s", result.retcode, result.comment)
            return None
        pos_ticket = self._latest_position_ticket(symbol, magic)
        return pos_ticket if pos_ticket is not None else int(result.order)

    def pending_orders_count(self, symbol: str, magic: int = 202607) -> int:
        if self.dry_run or not mt5 or not self._connected:
            return 0
        orders = mt5.orders_get(symbol=symbol)
        if not orders:
            return 0
        return sum(1 for o in orders if o.magic == magic)

    def cancel_our_pending(self, symbol: str, magic: int = 202607) -> int:
        if self.dry_run:
            logger.info("DRY_RUN cancel pending %s", symbol)
            return 0
        if not mt5 or not self._connected:
            return 0
        orders = mt5.orders_get(symbol=symbol)
        if not orders:
            return 0
        removed = 0
        for o in orders:
            if o.magic != magic:
                continue
            req = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": o.ticket,
            }
            result = mt5.order_send(req)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                removed += 1
                self._pending_placed_at.pop(o.ticket, None)
                logger.info("Pending #%s жойылды", o.ticket)
        return removed

    def _pending_age_seconds(self, order: Any) -> float:
        ticket = int(order.ticket)
        placed = self._pending_placed_at.get(ticket)
        if placed is None:
            setup_msc = getattr(order, "time_setup_msc", 0) or 0
            if setup_msc > 0:
                placed = setup_msc / 1000.0
            else:
                setup = getattr(order, "time_setup", None)
                if setup is not None and hasattr(setup, "timestamp"):
                    placed = setup.timestamp()
            if placed is not None:
                self._pending_placed_at[ticket] = placed
        if placed is None:
            return 0.0
        return max(0.0, time.time() - placed)

    def pending_orders_summary(
        self, symbol: str, magic: int = 202607
    ) -> list[tuple[int, float, float]]:
        """(ticket, entry_price, age_sec) — мониторинг."""
        if self.dry_run or not mt5 or not self._connected:
            return []
        orders = mt5.orders_get(symbol=symbol) or []
        out: list[tuple[int, float, float]] = []
        for o in orders:
            if o.magic != magic:
                continue
            out.append((int(o.ticket), float(o.price_open), self._pending_age_seconds(o)))
        return out

    def expire_stale_pending(
        self, symbol: str, max_age_seconds: float, magic: int = 202607
    ) -> int:
        """Уақыты өткен pending ордерлерді жою."""
        if self.dry_run or not mt5 or not self._connected:
            return 0
        orders = mt5.orders_get(symbol=symbol)
        if not orders:
            return 0
        removed = 0
        for o in orders:
            if o.magic != magic:
                continue
            age = self._pending_age_seconds(o)
            if age < max_age_seconds:
                continue
            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
            result = mt5.order_send(req)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                removed += 1
                self._pending_placed_at.pop(int(o.ticket), None)
                logger.info(
                    "Pending #%s мерзімі өтті (%.0f сек, лимит %.0f) — жойылды",
                    o.ticket,
                    age,
                    max_age_seconds,
                )
        return removed

    def open_at_zone_mid(
        self,
        symbol: str,
        side: Side,
        lot: float,
        zone_low: float,
        zone_high: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: str = "tg_zone_mid",
    ) -> Optional[int]:
        entry = _normalize_price(symbol, (zone_low + zone_high) / 2.0)
        if entry <= 0 or zone_low <= 0 or zone_high <= 0:
            logger.error(
                "Zone mid жарамсыз: zone=%s-%s entry=%s",
                zone_low,
                zone_high,
                entry,
            )
            return None
        if self.dry_run:
            logger.info(
                "DRY_RUN LIMIT/STOP %s %s lot=%s entry=%s (zone %s-%s) sl=%s tp=%s",
                side.value,
                symbol,
                lot,
                entry,
                zone_low,
                zone_high,
                sl,
                tp,
            )
            return 0

        if not mt5 or not self._connected:
            logger.error("MT5 қосылмаған — pending ордер жіберілмеді")
            return None

        if not mt5.symbol_select(symbol, True):
            logger.error("symbol_select сәтсіз: %s", symbol)
            return None

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error("symbol_info_tick жоқ: %s", symbol)
            return None

        order_type = _entry_pending_type(side, entry, tick.bid, tick.ask)
        type_name = "LIMIT" if order_type in (
            mt5.ORDER_TYPE_SELL_LIMIT,
            mt5.ORDER_TYPE_BUY_LIMIT,
        ) else "STOP"

        self.cancel_our_pending(symbol)

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": entry,
            "magic": 202607,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if sl is not None:
            request["sl"] = sl
        if tp is not None:
            request["tp"] = tp

        result = mt5.order_send(request)
        if result is None:
            logger.error("pending order_send None: %s", mt5.last_error())
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                "pending retcode=%s comment=%s (entry=%s %s bid=%s ask=%s)",
                result.retcode,
                result.comment,
                entry,
                type_name,
                tick.bid,
                tick.ask,
            )
            return None
        logger.info(
            "Pending %s %s entry=%s (zone mid %s-%s) order=%s",
            type_name,
            side.value,
            entry,
            zone_low,
            zone_high,
            result.order,
        )
        self._pending_placed_at[int(result.order)] = time.time()
        return int(result.order)

    def place_limit_at_price(
        self,
        symbol: str,
        side: Side,
        price: float,
        lot: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: str = "ft_intel",
        magic: int = 202608,
    ) -> Optional[int]:
        """Нақты бағада Buy Limit / Sell Limit (FT Intel сияқты)."""
        entry = _normalize_price(symbol, price)
        if entry <= 0:
            logger.error("Limit price жарамсыз: %s", price)
            return None

        if self.dry_run:
            type_name = "BUY_LIMIT" if side == Side.BUY else "SELL_LIMIT"
            logger.info(
                "DRY_RUN %s %s lot=%s entry=%s sl=%s tp=%s",
                type_name,
                symbol,
                lot,
                entry,
                sl,
                tp,
            )
            return 0

        if not mt5 or not self._connected:
            logger.error("MT5 қосылмаған — limit ордер жіберілмеді")
            return None

        if side == Side.BUY:
            order_type = mt5.ORDER_TYPE_BUY_LIMIT
            type_name = "BUY_LIMIT"
        else:
            order_type = mt5.ORDER_TYPE_SELL_LIMIT
            type_name = "SELL_LIMIT"

        if not mt5.symbol_select(symbol, True):
            logger.error("symbol_select сәтсіз: %s", symbol)
            return None

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": entry,
            "magic": magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if sl is not None:
            request["sl"] = _normalize_price(symbol, sl)
        if tp is not None:
            request["tp"] = _normalize_price(symbol, tp)

        result = mt5.order_send(request)
        if result is None:
            logger.error("limit order_send None: %s", mt5.last_error())
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                "limit retcode=%s comment=%s (%s %s @ %s)",
                result.retcode,
                result.comment,
                type_name,
                symbol,
                entry,
            )
            return None
        logger.info(
            "Limit %s %s entry=%s sl=%s tp=%s order=%s",
            type_name,
            symbol,
            entry,
            sl,
            tp,
            result.order,
        )
        self._pending_placed_at[int(result.order)] = time.time()
        return int(result.order)

    def cancel_pending_by_magic(self, symbol: str, magic: int) -> int:
        """Белгілі magic pending ордерлерді жою."""
        if self.dry_run:
            logger.info("DRY_RUN cancel pending magic=%s %s", magic, symbol)
            return 0
        if not mt5 or not self._connected:
            return 0
        orders = mt5.orders_get(symbol=symbol) or []
        removed = 0
        for o in orders:
            if o.magic != magic:
                continue
            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
            result = mt5.order_send(req)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                removed += 1
                self._pending_placed_at.pop(int(o.ticket), None)
        if removed:
            logger.info("Pending жойылды: %s (%s, magic=%s)", removed, symbol, magic)
        return removed

    def cancel_pending_if_position_open(self, magic: int = 202608) -> int:
        """Position ашылған symbol үшін қалған pending ордерлерді жою (OCO)."""
        if self.dry_run:
            return 0
        if not mt5 or not self._connected:
            return 0

        positions = mt5.positions_get() or []
        symbols_with_pos = {p.symbol for p in positions if p.magic == magic}
        if not symbols_with_pos:
            return 0

        removed_total = 0
        for sym in symbols_with_pos:
            orders = [
                o for o in (mt5.orders_get(symbol=sym) or []) if o.magic == magic
            ]
            if not orders:
                continue
            for o in orders:
                req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
                result = mt5.order_send(req)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    removed_total += 1
                    self._pending_placed_at.pop(int(o.ticket), None)
                    side = "BUY" if o.type in (
                        mt5.ORDER_TYPE_BUY_LIMIT,
                        mt5.ORDER_TYPE_BUY_STOP,
                    ) else "SELL"
                    logger.info(
                        "OCO %s: %s Limit #%s жойылды (position ашылды)",
                        sym,
                        side,
                        o.ticket,
                    )
                elif result:
                    logger.warning(
                        "OCO cancel #%s retcode=%s %s",
                        o.ticket,
                        result.retcode,
                        result.comment,
                    )
        return removed_total

    def pending_orders_by_magic(
        self, magic: int = 202608
    ) -> list[tuple[str, int, str, float]]:
        """(symbol, ticket, side, price) — ft_intel pending тізімі."""
        if self.dry_run or not mt5 or not self._connected:
            return []
        out: list[tuple[str, int, str, float]] = []
        orders = mt5.orders_get() or []
        for o in orders:
            if o.magic != magic:
                continue
            if o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP):
                side = "BUY"
            elif o.type in (mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP):
                side = "SELL"
            else:
                side = "?"
            out.append((o.symbol, int(o.ticket), side, float(o.price_open)))
        return out

    def copy_rates(
        self, symbol: str, timeframe: int, count: int = 200
    ) -> list[dict[str, float]]:
        """OHLC деректер (MT5 timeframe constant)."""
        if self.dry_run and not self._connected:
            return []
        if not mt5:
            return []
        if not self.ensure_connected():
            return []
        if not self._ensure_symbol(symbol):
            return []
        raw = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if raw is None:
            err = mt5.last_error()
            logger.warning("copy_rates бос: %s tf=%s (%s)", symbol, timeframe, err)
            return []
        return [
            {
                "time": float(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
            }
            for r in raw
        ]

    def symbol_point(self, symbol: str) -> float:
        if mt5 is None:
            return 0.0001
        info = mt5.symbol_info(symbol)
        if info is None:
            return 0.0001
        return float(info.point)

    def symbol_spread(self, symbol: str) -> float:
        """Spread (price units)."""
        if mt5 is None:
            return 0.0
        info = mt5.symbol_info(symbol)
        if info is None:
            return 0.0
        return float(info.spread) * float(info.point)

    def current_price(self, symbol: str) -> Optional[float]:
        bid = self.tick_bid(symbol)
        ask = self.tick_ask(symbol)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _latest_pending_ticket(self, symbol: str, magic: int = 202607) -> Optional[int]:
        if not mt5 or not self._connected:
            return None
        orders = mt5.orders_get(symbol=symbol)
        if not orders:
            return None
        ours = [o for o in orders if o.magic == magic]
        if not ours:
            return None
        order = max(ours, key=lambda o: o.time_setup)
        return int(order.ticket)

    def _modify_pending_sltp(
        self, symbol: str, sl: Optional[float], tp: Optional[float], magic: int = 202607
    ) -> bool:
        ticket = self._latest_pending_ticket(symbol, magic)
        if ticket is None:
            return False
        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            return False
        o = orders[0]
        new_sl = sl if sl is not None else o.sl
        new_tp = tp if tp is not None else o.tp
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "price": o.price_open,
            "symbol": symbol,
            "sl": new_sl,
            "tp": new_tp,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            if result:
                logger.error("modify pending retcode=%s", result.retcode)
            return False
        logger.info("Pending #%s SL=%s TP=%s", ticket, new_sl, new_tp)
        return True

    def modify_pending_entry(
        self,
        symbol: str,
        new_entry: float,
        magic: int = 202607,
    ) -> bool:
        """Отложенный ордер entry бағасын жаңарту (мыс. 4078 → 4073)."""
        if new_entry <= 0:
            return False
        new_price = _normalize_price(symbol, new_entry)
        if self.dry_run:
            logger.info(
                "DRY_RUN pending entry → %s (%s)",
                new_price,
                symbol,
            )
            return True
        if not mt5 or not self._connected:
            logger.error("MT5 қосылмаған — pending entry жаңартылмады")
            return False
        ticket = self._latest_pending_ticket(symbol, magic)
        if ticket is None:
            logger.info("Pending entry жаңарту: ордер жоқ (%s)", symbol)
            return False
        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            return False
        o = orders[0]
        old_price = o.price_open
        if abs(old_price - new_price) < 1e-6:
            logger.info("Pending #%s entry=%s — өзгеріс жоқ", ticket, new_price)
            return True
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "price": new_price,
            "symbol": symbol,
            "sl": o.sl,
            "tp": o.tp,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            if result:
                logger.error(
                    "modify pending entry retcode=%s (%s → %s)",
                    result.retcode,
                    old_price,
                    new_price,
                )
            return False
        self._pending_placed_at[ticket] = time.time()
        logger.info(
            "Pending #%s entry %s → %s",
            ticket,
            old_price,
            new_price,
        )
        return True

    def open_or_pending_count(self, symbol: str, magic: int = 202607) -> int:
        return self.open_positions_count(symbol, magic) + self.pending_orders_count(
            symbol, magic
        )

    def _latest_position_ticket(self, symbol: str, magic: int = 202607) -> Optional[int]:
        if not mt5 or not self._connected:
            return None
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None
        ours = [p for p in positions if p.magic == magic]
        pool = ours if ours else list(positions)
        pos = max(pool, key=lambda p: p.time)
        return int(pos.ticket)

    def set_take_profit(self, symbol: str, tp: float, magic: int = 202607) -> bool:
        if self.dry_run:
            logger.info("DRY_RUN SET TP %s tp=%s", symbol, tp)
            return True

        if not mt5 or not self._connected:
            logger.error("MT5 қосылмаған — TP орнатылмады")
            return False

        ticket = self._latest_position_ticket(symbol, magic)
        if ticket is None:
            if self._modify_pending_sltp(symbol, tp=tp, sl=None, magic=magic):
                logger.info("TP=%s pending ордерге орнатылды", tp)
                return True
            logger.error("Ашық позиция табылмады: %s", symbol)
            return False

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.error("Позиция ticket=%s жоқ", ticket)
            return False
        pos = positions[0]

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": symbol,
            "sl": pos.sl,
            "tp": tp,
        }
        result = mt5.order_send(request)
        if result is None:
            logger.error("TP order_send None: %s", mt5.last_error())
            return False
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("TP retcode=%s comment=%s", result.retcode, result.comment)
            return False
        logger.info("TP=%s орнатылды (position %s)", tp, ticket)
        return True

    def set_stop_loss(self, symbol: str, sl: float, magic: int = 202607) -> bool:
        if self.dry_run:
            logger.info("DRY_RUN SET SL %s sl=%s", symbol, sl)
            return True

        if not mt5 or not self._connected:
            logger.error("MT5 қосылмаған — SL орнатылмады")
            return False

        ticket = self._latest_position_ticket(symbol, magic)
        if ticket is None:
            if self._modify_pending_sltp(symbol, sl=sl, tp=None, magic=magic):
                logger.info("SL=%s pending ордерге орнатылды", sl)
                return True
            logger.error("Ашық позиция табылмады (SL): %s", symbol)
            return False

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        pos = positions[0]

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": symbol,
            "sl": sl,
            "tp": pos.tp,
        }
        result = mt5.order_send(request)
        if result is None:
            logger.error("SL order_send None: %s", mt5.last_error())
            return False
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("SL retcode=%s comment=%s", result.retcode, result.comment)
            return False
        logger.info("SL=%s жаңартылды (position %s)", sl, ticket)
        return True

    def position_entry_price(self, symbol: str, magic: int = 202607) -> Optional[float]:
        if self.dry_run or not mt5 or not self._connected:
            return None
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None
        ours = [p for p in positions if p.magic == magic]
        pool = ours if ours else list(positions)
        pos = max(pool, key=lambda p: p.time)
        return float(pos.price_open)

    def set_sl_and_tp(
        self, symbol: str, sl: float, tp: float, magic: int = 202607
    ) -> bool:
        if self.dry_run:
            logger.info("DRY_RUN BU SL=%s TP=%s %s", sl, tp, symbol)
            return True
        if not mt5 or not self._connected:
            return False
        ticket = self._latest_position_ticket(symbol, magic)
        if ticket is None:
            return self._modify_pending_sltp(symbol, sl=sl, tp=tp, magic=magic)
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False
        sl_n = _normalize_price(symbol, sl)
        tp_n = _normalize_price(symbol, tp)
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": symbol,
            "sl": sl_n,
            "tp": tp_n,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            if result:
                logger.error("BU SLTP retcode=%s %s", result.retcode, result.comment)
            return False
        logger.info("BU: SL=%s TP=%s (position %s)", sl_n, tp_n, ticket)
        return True

    def open_positions_count(self, symbol: str, magic: int = 202607) -> int:
        if self.dry_run or not mt5 or not self._connected:
            return 0
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return 0
        ours = [p for p in positions if p.magic == magic]
        return len(ours if ours else list(positions))

    def close_all_trading(self, symbol: str, magic: int = 202607) -> tuple[int, int]:
        """Pending жою + ашық позицияларды жабу."""
        if self.dry_run:
            logger.info("DRY_RUN CLOSE all (pending+positions) %s", symbol)
            return 0, 0
        pending_removed = self.cancel_our_pending(symbol, magic)
        positions_closed = self._close_positions(symbol)
        return pending_removed, positions_closed

    def _close_positions(self, symbol: str) -> int:
        if self.dry_run:
            logger.info("DRY_RUN CLOSE positions %s", symbol)
            return 0

        if not mt5 or not self._connected:
            return 0

        closed = 0
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return 0

        for pos in positions:
            order_type = (
                mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
            )
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                continue
            price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": pos.volume,
                "type": order_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": 202607,
                "comment": "tg_close",
                "type_time": mt5.ORDER_TIME_GTC,
            }
            result = _send_deal(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                closed += 1
        return closed

    def partial_close_percent(
        self, symbol: str, percent: int, magic: int = 202607
    ) -> tuple[bool, float, float]:
        """Позицияның percent%-ін жабу → (ok, closed_vol, remaining_vol)."""
        pct = max(1, min(100, int(percent)))
        if self.dry_run:
            mock_total = 0.10
            close = calc_partial_close_volume(mock_total, pct, 0.01, 0.01)
            remain = round(mock_total - close, 2)
            logger.info(
                "DRY_RUN Save %s%%: close %.2f of %.2f (%.2f қалды)",
                pct,
                close,
                mock_total,
                remain,
            )
            return True, close, remain

        if not mt5 or not self._connected:
            logger.error("MT5 қосылмаған — Save partial орындалмады")
            return False, 0.0, 0.0

        if not mt5.symbol_select(symbol, True):
            logger.error("symbol_select сәтсіз: %s", symbol)
            return False, 0.0, 0.0

        info = mt5.symbol_info(symbol)
        if info is None:
            logger.error("symbol_info жоқ: %s", symbol)
            return False, 0.0, 0.0

        ticket = self._latest_position_ticket(symbol, magic)
        if ticket is None:
            logger.warning("Save %s%%: ашық позиция жоқ (%s)", pct, symbol)
            return False, 0.0, 0.0

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.warning("Save %s%%: позиция ticket=%s табылмады", pct, ticket)
            return False, 0.0, 0.0

        pos = positions[0]
        total = float(pos.volume)
        close_vol = calc_partial_close_volume(
            total, pct, float(info.volume_min), float(info.volume_step)
        )
        if close_vol <= 0:
            logger.warning(
                "Save %s%%: жабу көлемі 0 (lot=%s, min=%s, step=%s)",
                pct,
                total,
                info.volume_min,
                info.volume_step,
            )
            return False, 0.0, total

        order_type = (
            mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        )
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error("symbol_info_tick жоқ: %s", symbol)
            return False, 0.0, total
        price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": close_vol,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": magic,
            "comment": f"tg_save_{pct}",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        result = _send_deal(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            remain = round(total - close_vol, _volume_digits(float(info.volume_step)))
            logger.info(
                "Save %s%%: %.2f lot жабылды, %.2f қалды (position %s)",
                pct,
                close_vol,
                remain,
                ticket,
            )
            return True, close_vol, remain

        if result:
            logger.error(
                "Save %s%% retcode=%s comment=%s",
                pct,
                result.retcode,
                result.comment,
            )
        else:
            logger.error("Save %s%% order_send None: %s", pct, mt5.last_error())
        return False, 0.0, total

    def close_symbol(self, symbol: str) -> int:
        """Кері үйлесімділік: позиция + pending."""
        pending_removed, closed = self.close_all_trading(symbol)
        return closed + pending_removed
