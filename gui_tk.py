import threading
import time
import tkinter as tk
from datetime import UTC, datetime
from tkinter import ttk
from zoneinfo import ZoneInfo

from config import load_config
from exchange_client import BinanceMarketDataClient
from execution import PaperExecutor
from risk import RiskConfig, RiskManager
from strategy_breakout import BreakoutStrategy


class BotGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Binance Breakout Paper Bot")
        self.root.geometry("1220x760")

        self.cfg = load_config()
        self.client = BinanceMarketDataClient()
        self.symbols = self.cfg.symbols or [self.cfg.symbol]
        self.per_symbol_equity = self.cfg.initial_equity / max(len(self.symbols), 1)
        self.sydney_tz = ZoneInfo("Australia/Sydney")
        self.engines: dict[str, tuple[BreakoutStrategy, RiskManager, PaperExecutor]] = {}
        self._init_symbol_engines()

        self.is_running = False
        self.thread: threading.Thread | None = None
        self.demo_mode = tk.BooleanVar(value=True)

        self.status_var = tk.StringVar(value="Idle")
        self.invested_var = tk.StringVar(value=f"{self.cfg.initial_equity:.2f}")
        self.pnl_var = tk.StringVar(value="0.00")
        self.equity_var = tk.StringVar(value=f"{self.cfg.initial_equity:.2f}")
        self.event_var = tk.StringVar(value="Not started")
        self.updated_var = tk.StringVar(value="-")
        self.market_label_var = tk.StringVar(value=f"Market: {self.cfg.market_type}")
        if self.cfg.market_type == "margin":
            direction_label = f"Direction: long+short | Leverage x{self.cfg.leverage:.2f}"
        else:
            direction_label = "Direction: long-only | Leverage x1.00"
        self.leverage_label_var = tk.StringVar(value=direction_label)
        self.position_var = tk.StringVar(value="No open positions")
        self.event_log: list[tuple[str, str, str]] = []
        self.pnl_snapshots: list[tuple[str, float, float, float]] = []
        self.last_pnl_snapshot_ts: float = 0.0
        self.latest_by_symbol: dict[str, dict[str, str | float]] = {
            s: {"price": 0.0, "signal": "HOLD", "event": "Not started", "updated": "-"}
            for s in self.symbols
        }

        self._build_ui()

    def _risk_cfg(self) -> RiskConfig:
        return RiskConfig(
            risk_per_trade_pct=self.cfg.risk_per_trade_pct,
            stop_loss_pct=self.cfg.stop_loss_pct,
            take_profit_pct=self.cfg.take_profit_pct,
            trailing_stop_pct=self.cfg.trailing_stop_pct,
            max_positions=self.cfg.max_positions,
            daily_max_drawdown_pct=self.cfg.daily_max_drawdown_pct,
            cooldown_bars_after_loss=self.cfg.cooldown_bars_after_loss,
        )

    def _state_path_for_symbol(self, symbol: str) -> str:
        safe = symbol.replace("/", "_").replace(":", "_")
        if "." in self.cfg.persist_path:
            root, ext = self.cfg.persist_path.rsplit(".", 1)
            return f"{root}_{safe}.{ext}"
        return f"{self.cfg.persist_path}_{safe}"

    def _init_symbol_engines(self) -> None:
        self.engines = {}
        for symbol in self.symbols:
            strategy = BreakoutStrategy(
                lookback=self.cfg.breakout_lookback,
                breakout_buffer_pct=self.cfg.breakout_buffer_pct,
                volume_window=self.cfg.volume_window,
                min_volume_ratio=self.cfg.min_volume_ratio,
            )
            risk = RiskManager(self._risk_cfg(), self.per_symbol_equity)
            exec_engine = PaperExecutor(
                self.cfg.market_type,
                self.per_symbol_equity,
                self.cfg.fee_rate,
                self.cfg.leverage,
            )
            exec_engine.load_state(self._state_path_for_symbol(symbol))
            self.engines[symbol] = (strategy, risk, exec_engine)

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text=f"Symbols: {', '.join(self.symbols)}").pack(side=tk.LEFT, padx=6)
        ttk.Label(top, textvariable=self.market_label_var).pack(side=tk.LEFT, padx=6)
        ttk.Label(top, textvariable=self.leverage_label_var).pack(side=tk.LEFT, padx=6)
        ttk.Label(top, text=f"Timeframe: {self.cfg.timeframe}").pack(side=tk.LEFT, padx=6)
        ttk.Label(top, text=f"Poll: {self.cfg.poll_seconds}s").pack(side=tk.LEFT, padx=6)

        btn_row = ttk.Frame(self.root, padding=10)
        btn_row.pack(fill=tk.X)

        ttk.Button(btn_row, text="Run One Cycle", command=self.run_one_cycle).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="Start Auto", command=self.start_auto).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_row, text="Stop Auto", command=self.stop_auto).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(btn_row, text="Demo Mode (more trades)", variable=self.demo_mode).pack(side=tk.LEFT, padx=12)

        metrics = ttk.Frame(self.root, padding=10)
        metrics.pack(fill=tk.X)
        for label, var in [
            ("Status", self.status_var),
            ("Total Invested", self.invested_var),
            ("Total PnL", self.pnl_var),
            ("Total Equity", self.equity_var),
            ("Last Event", self.event_var),
            ("Updated Australia/Sydney", self.updated_var),
        ]:
            box = ttk.Frame(metrics)
            box.pack(side=tk.LEFT, padx=12)
            ttk.Label(box, text=label, font=("Arial", 10, "bold")).pack()
            ttk.Label(box, textvariable=var).pack()

        summary_frame = ttk.LabelFrame(self.root, text="Live Symbol Summary", padding=10)
        summary_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        summary_cols = ("symbol", "price", "signal", "equity", "event", "updated")
        self.summary = ttk.Treeview(summary_frame, columns=summary_cols, show="headings", height=6)
        for col in summary_cols:
            self.summary.heading(col, text=col.upper())
            self.summary.column(col, width=180, anchor=tk.CENTER)
        self.summary.pack(fill=tk.BOTH, expand=True)

        pos_frame = ttk.LabelFrame(self.root, text="Open Position", padding=10)
        pos_frame.pack(fill=tk.X, padx=10, pady=(0, 8))
        ttk.Label(pos_frame, textvariable=self.position_var).pack(anchor=tk.W)

        trades_frame = ttk.LabelFrame(self.root, text="Recent Trades", padding=10)
        trades_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        columns = ("symbol", "side", "qty", "entry", "exit", "pnl", "reason", "ts")
        self.trades = ttk.Treeview(trades_frame, columns=columns, show="headings", height=14)
        for col in columns:
            self.trades.heading(col, text=col.upper())
            self.trades.column(col, width=110, anchor=tk.CENTER)
        self.trades.pack(fill=tk.BOTH, expand=True)

        events_frame = ttk.LabelFrame(self.root, text="Event Log (OPEN/BLOCK/HOLD/CLOSE)", padding=10)
        events_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.events_list = tk.Listbox(events_frame, height=8)
        self.events_list.pack(fill=tk.BOTH, expand=True)

        pnl_frame = ttk.LabelFrame(self.root, text="Total PnL Snapshot (Every 5 Minutes)", padding=10)
        pnl_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        pnl_cols = ("time", "invested", "remaining", "pnl")
        self.pnl_table = ttk.Treeview(pnl_frame, columns=pnl_cols, show="headings", height=8)
        for col in pnl_cols:
            self.pnl_table.heading(col, text=col.upper())
            self.pnl_table.column(col, width=180, anchor=tk.CENTER)
        self.pnl_table.pack(fill=tk.BOTH, expand=True)

    def _set_strategy_params(self, strategy: BreakoutStrategy) -> None:
        if self.demo_mode.get():
            # Aggressive demo presets so entries/exits occur more frequently.
            strategy.lookback = 10
            strategy.breakout_buffer_pct = 0.0
            strategy.min_volume_ratio = 1.0
        else:
            strategy.lookback = self.cfg.breakout_lookback
            strategy.breakout_buffer_pct = 0.0
            strategy.min_volume_ratio = self.cfg.min_volume_ratio

    def _flatten_trades(self) -> list[tuple[str, object]]:
        rows: list[tuple[str, object]] = []
        for symbol, (_, _, exec_engine) in self.engines.items():
            for t in exec_engine.trade_log:
                rows.append((symbol, t))
        rows.sort(key=lambda r: r[1].ts, reverse=True)
        return rows

    def _refresh_summary(self) -> None:
        for row in self.summary.get_children():
            self.summary.delete(row)
        for symbol in self.symbols:
            latest = self.latest_by_symbol[symbol]
            _, _, exec_engine = self.engines[symbol]
            equity = exec_engine.equity_curve[-1] if exec_engine.equity_curve else exec_engine.cash
            self.summary.insert(
                "",
                tk.END,
                values=(
                    symbol,
                    f"{float(latest['price']):.4f}",
                    str(latest["signal"]),
                    f"{equity:.2f}",
                    str(latest["event"]),
                    str(latest["updated"]),
                ),
            )

    def _refresh_trades(self) -> None:
        for row in self.trades.get_children():
            self.trades.delete(row)
        for symbol, t in self._flatten_trades()[:40]:
            self.trades.insert(
                "",
                tk.END,
                values=(
                    symbol,
                    t.side,
                    f"{t.qty:.6f}",
                    f"{t.entry_price:.2f}",
                    f"{t.exit_price:.2f}",
                    f"{t.pnl:.2f}",
                    t.reason,
                    t.ts,
                ),
            )

    def _refresh_pnl_table(self) -> None:
        for row in self.pnl_table.get_children():
            self.pnl_table.delete(row)
        for ts, invested, remaining, pnl in self.pnl_snapshots[-72:]:
            self.pnl_table.insert(
                "",
                tk.END,
                values=(ts, f"{invested:.2f}", f"{remaining:.2f}", f"{pnl:.2f}"),
            )

    def _maybe_add_pnl_snapshot(self, total_equity: float) -> None:
        now = time.time()
        if self.last_pnl_snapshot_ts == 0.0 or (now - self.last_pnl_snapshot_ts) >= 300:
            ts = datetime.now(self.sydney_tz).isoformat()
            invested = float(self.cfg.initial_equity)
            pnl = total_equity - invested
            self.pnl_snapshots.append((ts, invested, total_equity, pnl))
            self.pnl_snapshots = self.pnl_snapshots[-500:]
            self.last_pnl_snapshot_ts = now
            self._refresh_pnl_table()

    def _update_status(self, symbol: str, price: float, signal: str, event: str) -> None:
        ts = datetime.now(self.sydney_tz).isoformat()
        self.latest_by_symbol[symbol] = {"price": price, "signal": signal, "event": event, "updated": ts}
        total_equity = 0.0
        open_rows: list[str] = []
        for sym in self.symbols:
            _, _, exec_engine = self.engines[sym]
            eq = exec_engine.equity_curve[-1] if exec_engine.equity_curve else exec_engine.cash
            total_equity += eq
            if exec_engine.position:
                p = exec_engine.position
                open_rows.append(
                    f"{sym}: side={p.side} qty={p.qty:.6f} entry={p.entry_price:.2f} "
                    f"SL={p.stop_loss:.2f} TP={p.take_profit:.2f}"
                )
        self.equity_var.set(f"{total_equity:.2f}")
        self.pnl_var.set(f"{(total_equity - self.cfg.initial_equity):.2f}")
        self.event_var.set(event)
        self.updated_var.set(ts)
        self.position_var.set(" | ".join(open_rows) if open_rows else "No open positions")
        self._maybe_add_pnl_snapshot(total_equity)

        self.event_log.append((ts, symbol, event))
        self.event_log = self.event_log[-200:]
        self.events_list.delete(0, tk.END)
        for row_ts, row_symbol, row_event in self.event_log[-80:]:
            self.events_list.insert(tk.END, f"{row_ts} | {row_symbol} | {row_event}")
        self._refresh_summary()
        self._refresh_trades()

    def run_one_cycle(self) -> None:
        try:
            for symbol in self.symbols:
                strategy, risk, exec_engine = self.engines[symbol]
                self._set_strategy_params(strategy)

                df = self.client.fetch_ohlcv_df(symbol, self.cfg.timeframe, self.cfg.limit)
                now = df.iloc[-1]
                price = float(now["close"])
                ts = int(now["time"])
                equity = exec_engine.mark_equity(price)
                support = df["low"].rolling(window=self.cfg.sr_window).min().iloc[-1]
                resistance = df["high"].rolling(window=self.cfg.sr_window).max().iloc[-1]
                event = "HOLD"

                if exec_engine.has_position():
                    pos = exec_engine.position
                    entry_price = pos.entry_price if pos else price
                    move_pct = ((price - entry_price) / max(entry_price, 1e-9)) * 100
                    if pos and pos.side == "long":
                        should_exit_sr = price >= resistance * self.cfg.sell_near_resistance_pct
                        should_take_profit = move_pct >= self.cfg.take_profit_pct * 100
                        should_stop_loss = move_pct <= -self.cfg.stop_loss_pct * 100
                    else:
                        should_exit_sr = price <= support * (1 + self.cfg.buy_near_support_pct)
                        should_take_profit = move_pct <= -self.cfg.take_profit_pct * 100
                        should_stop_loss = move_pct >= self.cfg.stop_loss_pct * 100
                    if should_exit_sr or should_take_profit or should_stop_loss:
                        reason = "sr_exit" if should_exit_sr else ("take_profit" if should_take_profit else "stop_loss")
                        pnl = exec_engine.close_position(price, reason, ts)
                        risk.on_trade_closed(pnl)
                        event = f"CLOSE({reason}) pnl={pnl:.2f}"
                        exec_engine.save_state(self._state_path_for_symbol(symbol))
                        self._update_status(symbol, price, "CLOSE", event)
                        continue

                if not exec_engine.has_position() and price <= support * (1 + self.cfg.buy_near_support_pct):
                    qty = max((self.per_symbol_equity * 0.2) / max(price, 1e-9), 0.0)
                    opened = exec_engine.open_position(
                        side="long",
                        qty=qty,
                        price=price,
                        stop_loss_pct=self.cfg.stop_loss_pct,
                        take_profit_pct=self.cfg.take_profit_pct,
                        trailing_stop_pct=0.0,
                    )
                    if opened:
                        event = f"BUY qty={qty:.6f} @support"
                        signal = "BUY"
                    else:
                        signal = "HOLD"
                elif (
                    not exec_engine.has_position()
                    and self.cfg.market_type == "margin"
                    and price >= resistance * self.cfg.sell_near_resistance_pct
                ):
                    qty = max((self.per_symbol_equity * 0.2) / max(price, 1e-9), 0.0)
                    opened = exec_engine.open_position(
                        side="short",
                        qty=qty,
                        price=price,
                        stop_loss_pct=self.cfg.stop_loss_pct,
                        take_profit_pct=self.cfg.take_profit_pct,
                        trailing_stop_pct=0.0,
                    )
                    if opened:
                        event = f"SHORT qty={qty:.6f} @resistance"
                        signal = "SHORT"
                    else:
                        signal = "HOLD"
                else:
                    signal = "HOLD"

                exec_engine.save_state(self._state_path_for_symbol(symbol))
                self._update_status(symbol, price, signal, event)
            self.status_var.set("Running")
        except Exception as exc:
            self.status_var.set("Error")
            self.event_var.set(str(exc))

    def _auto_loop(self) -> None:
        while self.is_running:
            self.root.after(0, self.run_one_cycle)
            time.sleep(max(self.cfg.poll_seconds, 1))

    def start_auto(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self.status_var.set("Auto mode")
        self.thread = threading.Thread(target=self._auto_loop, daemon=True)
        self.thread.start()

    def stop_auto(self) -> None:
        self.is_running = False
        self.status_var.set("Stopped")


def main() -> None:
    root = tk.Tk()
    app = BotGUI(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_auto(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
