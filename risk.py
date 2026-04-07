from dataclasses import dataclass
from datetime import datetime


@dataclass
class RiskConfig:
    risk_per_trade_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    max_positions: int
    daily_max_drawdown_pct: float
    cooldown_bars_after_loss: int


class RiskManager:
    def __init__(self, cfg: RiskConfig, start_equity: float) -> None:
        self.cfg = cfg
        self.day_start_equity = start_equity
        self.current_day = datetime.utcnow().date()
        self.cooldown_bars_left = 0
        self.loss_streak = 0

    def on_new_bar(self) -> None:
        if self.cooldown_bars_left > 0:
            self.cooldown_bars_left -= 1
        today = datetime.utcnow().date()
        if today != self.current_day:
            self.current_day = today
            self.day_start_equity = max(self.day_start_equity, 1e-9)
            self.loss_streak = 0

    def on_trade_closed(self, pnl: float) -> None:
        if pnl < 0:
            self.loss_streak += 1
            self.cooldown_bars_left = self.cfg.cooldown_bars_after_loss
        else:
            self.loss_streak = 0

    def can_open_position(self, current_equity: float, open_positions: int) -> tuple[bool, str]:
        if open_positions >= self.cfg.max_positions:
            return False, "max_positions_reached"
        if self.cooldown_bars_left > 0:
            return False, "cooldown_active"

        drawdown = max((self.day_start_equity - current_equity) / max(self.day_start_equity, 1e-9), 0.0)
        if drawdown >= self.cfg.daily_max_drawdown_pct:
            return False, "daily_drawdown_limit_hit"
        return True, "ok"

    def position_size(self, equity: float, entry_price: float) -> float:
        risk_cash = equity * self.cfg.risk_per_trade_pct
        stop_distance = max(entry_price * self.cfg.stop_loss_pct, 1e-9)
        qty = risk_cash / stop_distance
        notional_cap_qty = equity / max(entry_price, 1e-9)
        return max(min(qty, notional_cap_qty), 0.0)
