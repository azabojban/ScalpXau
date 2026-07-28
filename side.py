"""MT5 бағыт."""

from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
