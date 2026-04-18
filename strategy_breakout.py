from dataclasses import dataclass
from typing import Literal

import pandas as pd

Signal = Literal["LONG_ENTRY", "LONG_EXIT", "SHORT_ENTRY", "SHORT_EXIT", "HOLD"]


@dataclass
class BreakoutStrategy:
    lookback: int = 20
    breakout_buffer_pct: float = 0.001
    volume_window: int = 20
    min_volume_ratio: float = 1.1

    def generate_signal(self, df: pd.DataFrame, market_type: str, has_position: bool, side: str | None) -> Signal:
        if len(df) < max(self.lookback + 1, self.volume_window + 1):
            return "HOLD"

        prev = df.iloc[-2]
        now = df.iloc[-1]
        highs = df["high"].rolling(window=self.lookback).max()
        lows = df["low"].rolling(window=self.lookback).min()
        vol_ma = df["volume"].rolling(window=self.volume_window).mean()

        resistance = highs.iloc[-2]
        support = lows.iloc[-2]
        volume_ratio = now["volume"] / max(vol_ma.iloc[-2], 1e-9)

        breakout_up = now["close"] > resistance * (1 + self.breakout_buffer_pct)
        breakout_down = now["close"] < support * (1 - self.breakout_buffer_pct)
        volume_ok = volume_ratio >= self.min_volume_ratio

        if not has_position:
            if breakout_up and volume_ok:
                return "LONG_ENTRY"
            if market_type == "margin" and breakout_down and volume_ok:
                return "SHORT_ENTRY"
            return "HOLD"

        if side == "long" and (breakout_down or now["close"] < prev["close"] * (1 - self.breakout_buffer_pct)):
            return "LONG_EXIT"
        if side == "short" and (breakout_up or now["close"] > prev["close"] * (1 + self.breakout_buffer_pct)):
            return "SHORT_EXIT"
        return "HOLD"
