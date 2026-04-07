from dataclasses import asdict

import pandas as pd
from config import BotConfig
from execution import PaperExecutor
from risk import RiskConfig, RiskManager
from strategy_breakout import BreakoutStrategy


def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / max(peak, 1e-9)
        mdd = max(mdd, dd)
    return mdd


def run_backtest(df: pd.DataFrame, cfg: BotConfig) -> dict:
    strategy = BreakoutStrategy(
        lookback=cfg.breakout_lookback,
        breakout_buffer_pct=cfg.breakout_buffer_pct,
        volume_window=cfg.volume_window,
        min_volume_ratio=cfg.min_volume_ratio,
    )
    risk_cfg = RiskConfig(
        risk_per_trade_pct=cfg.risk_per_trade_pct,
        stop_loss_pct=cfg.stop_loss_pct,
        take_profit_pct=cfg.take_profit_pct,
        trailing_stop_pct=cfg.trailing_stop_pct,
        max_positions=cfg.max_positions,
        daily_max_drawdown_pct=cfg.daily_max_drawdown_pct,
        cooldown_bars_after_loss=cfg.cooldown_bars_after_loss,
    )
    risk = RiskManager(risk_cfg, start_equity=cfg.initial_equity)
    exec_engine = PaperExecutor(cfg.market_type, cfg.initial_equity, cfg.fee_rate, cfg.leverage)

    for idx in range(max(cfg.breakout_lookback, cfg.volume_window) + 1, len(df)):
        chunk = df.iloc[: idx + 1]
        now = chunk.iloc[-1]
        price = float(now["close"])
        ts = int(now["time"])

        risk.on_new_bar()
        equity = exec_engine.mark_equity(price)
        signal = strategy.generate_signal(chunk, cfg.market_type, exec_engine.has_position(), exec_engine.position_side())

        if exec_engine.has_position():
            exec_engine.maybe_update_trailing_stop(price, cfg.trailing_stop_pct)
            stop_reason = exec_engine.stop_or_tp_hit(price)
            if stop_reason:
                pnl = exec_engine.close_position(price, stop_reason, ts)
                risk.on_trade_closed(pnl)
                continue

        if signal in {"LONG_ENTRY", "SHORT_ENTRY"}:
            ok, _ = risk.can_open_position(equity, 1 if exec_engine.has_position() else 0)
            if ok:
                qty = risk.position_size(equity, price)
                exec_engine.process_signal(
                    signal,
                    price,
                    ts,
                    qty,
                    cfg.stop_loss_pct,
                    cfg.take_profit_pct,
                    cfg.trailing_stop_pct,
                )
        else:
            pnl = exec_engine.process_signal(
                signal,
                price,
                ts,
                0.0,
                cfg.stop_loss_pct,
                cfg.take_profit_pct,
                cfg.trailing_stop_pct,
            )
            if pnl is not None:
                risk.on_trade_closed(pnl)

    final_equity = exec_engine.mark_equity(float(df.iloc[-1]["close"]))
    returns = (final_equity - cfg.initial_equity) / max(cfg.initial_equity, 1e-9)
    wins = [t for t in exec_engine.trade_log if t.pnl > 0]
    losses = [t for t in exec_engine.trade_log if t.pnl <= 0]
    win_rate = len(wins) / len(exec_engine.trade_log) if exec_engine.trade_log else 0.0
    fees = sum(t.fee_paid for t in exec_engine.trade_log)

    return {
        "initial_equity": cfg.initial_equity,
        "final_equity": final_equity,
        "total_return_pct": returns * 100,
        "max_drawdown_pct": _max_drawdown(exec_engine.equity_curve) * 100,
        "trade_count": len(exec_engine.trade_log),
        "win_rate_pct": win_rate * 100,
        "gross_wins": sum(t.pnl for t in wins),
        "gross_losses": sum(t.pnl for t in losses),
        "fee_paid": fees,
        "realized_pnl": exec_engine.realized_pnl,
        "sample_trades": [asdict(t) for t in exec_engine.trade_log[:10]],
    }
