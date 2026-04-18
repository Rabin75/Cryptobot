import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from strategy_breakout import Signal

Side = Literal["long", "short"]


@dataclass
class Position:
    side: Side
    qty: float
    entry_price: float
    stop_loss: float
    take_profit: float
    trailing_stop: float


@dataclass
class TradeRecord:
    side: Side
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    fee_paid: float
    reason: str
    ts: int


class PaperExecutor:
    def __init__(self, market_type: str, initial_equity: float, fee_rate: float, leverage: float = 1.0) -> None:
        self.market_type = market_type
        self.cash = initial_equity
        self.initial_equity = initial_equity
        self.fee_rate = fee_rate
        self.leverage = leverage if market_type == "margin" else 1.0
        self.position: Position | None = None
        self.realized_pnl = 0.0
        self.trade_log: list[TradeRecord] = []
        self.equity_curve: list[float] = []
        self.last_block_reason = ""

    def has_position(self) -> bool:
        return self.position is not None

    def position_side(self) -> str | None:
        return self.position.side if self.position else None

    def _entry_fee(self, notional: float) -> float:
        return notional * self.fee_rate

    def _exit_fee(self, notional: float) -> float:
        return notional * self.fee_rate

    def mark_equity(self, price: float) -> float:
        if not self.position:
            equity = self.cash
        else:
            direction = 1 if self.position.side == "long" else -1
            pnl = direction * (price - self.position.entry_price) * self.position.qty * self.leverage
            equity = self.cash + pnl
        self.equity_curve.append(equity)
        return equity

    def open_position(
        self,
        side: Side,
        qty: float,
        price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        trailing_stop_pct: float,
    ) -> bool:
        if self.position is not None or qty <= 0:
            return False

        notional = qty * price
        fee = self._entry_fee(notional)
        self.cash -= fee

        if side == "long":
            stop_loss = price * (1 - stop_loss_pct)
            take_profit = price * (1 + take_profit_pct)
            trailing_stop = price * (1 - trailing_stop_pct) if trailing_stop_pct > 0 else 0.0
        else:
            stop_loss = price * (1 + stop_loss_pct)
            take_profit = price * (1 - take_profit_pct)
            trailing_stop = price * (1 + trailing_stop_pct) if trailing_stop_pct > 0 else 0.0

        self.position = Position(
            side=side,
            qty=qty,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=trailing_stop,
        )
        return True

    def maybe_update_trailing_stop(self, price: float, trailing_stop_pct: float) -> None:
        if not self.position or trailing_stop_pct <= 0:
            return
        if self.position.side == "long":
            self.position.trailing_stop = max(self.position.trailing_stop, price * (1 - trailing_stop_pct))
        else:
            self.position.trailing_stop = min(self.position.trailing_stop, price * (1 + trailing_stop_pct))

    def stop_or_tp_hit(self, price: float) -> str | None:
        if not self.position:
            return None
        pos = self.position
        if pos.side == "long":
            if price <= pos.stop_loss:
                return "stop_loss"
            if price >= pos.take_profit:
                return "take_profit"
            if pos.trailing_stop and price <= pos.trailing_stop:
                return "trailing_stop"
        else:
            if price >= pos.stop_loss:
                return "stop_loss"
            if price <= pos.take_profit:
                return "take_profit"
            if pos.trailing_stop and price >= pos.trailing_stop:
                return "trailing_stop"
        return None

    def close_position(self, price: float, reason: str, ts: int) -> float:
        if not self.position:
            return 0.0
        pos = self.position
        direction = 1 if pos.side == "long" else -1
        gross_pnl = direction * (price - pos.entry_price) * pos.qty * self.leverage
        fee = self._exit_fee(pos.qty * price)
        pnl = gross_pnl - fee
        self.cash += pnl
        self.realized_pnl += pnl
        self.trade_log.append(
            TradeRecord(
                side=pos.side,
                qty=pos.qty,
                entry_price=pos.entry_price,
                exit_price=price,
                pnl=pnl,
                fee_paid=fee,
                reason=reason,
                ts=ts,
            )
        )
        self.position = None
        return pnl

    def process_signal(
        self,
        signal: Signal,
        price: float,
        ts: int,
        qty: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        trailing_stop_pct: float,
    ) -> float | None:
        if signal == "LONG_ENTRY":
            self.open_position("long", qty, price, stop_loss_pct, take_profit_pct, trailing_stop_pct)
            return None
        if signal == "SHORT_ENTRY" and self.market_type == "margin":
            self.open_position("short", qty, price, stop_loss_pct, take_profit_pct, trailing_stop_pct)
            return None
        if signal == "LONG_EXIT" and self.position and self.position.side == "long":
            return self.close_position(price, "signal_exit", ts)
        if signal == "SHORT_EXIT" and self.position and self.position.side == "short":
            return self.close_position(price, "signal_exit", ts)
        return None

    def save_state(self, path: str) -> None:
        data = {
            "market_type": self.market_type,
            "cash": self.cash,
            "initial_equity": self.initial_equity,
            "fee_rate": self.fee_rate,
            "leverage": self.leverage,
            "position": asdict(self.position) if self.position else None,
            "realized_pnl": self.realized_pnl,
            "trade_log": [asdict(t) for t in self.trade_log],
            "equity_curve": self.equity_curve[-5000:],
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_state(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        self.cash = float(data.get("cash", self.cash))
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.equity_curve = list(data.get("equity_curve", []))
        raw_pos = data.get("position")
        if raw_pos:
            self.position = Position(**raw_pos)
