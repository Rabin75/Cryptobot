import time
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from config import BotConfig, load_config
from exchange_client import BinanceMarketDataClient
from execution import PaperExecutor
from risk import RiskConfig, RiskManager
from strategy_breakout import BreakoutStrategy


def _build_engines(cfg: BotConfig) -> tuple[BreakoutStrategy, RiskManager, PaperExecutor]:
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
    risk = RiskManager(risk_cfg, cfg.initial_equity)
    exec_engine = PaperExecutor(cfg.market_type, cfg.initial_equity, cfg.fee_rate, cfg.leverage)
    return strategy, risk, exec_engine


def _run_one_cycle() -> None:
    cfg: BotConfig = st.session_state.cfg
    client: BinanceMarketDataClient = st.session_state.client
    strategy: BreakoutStrategy = st.session_state.strategy
    risk: RiskManager = st.session_state.risk
    exec_engine: PaperExecutor = st.session_state.exec_engine

    df = client.fetch_ohlcv_df(cfg.symbol, cfg.timeframe, cfg.limit)
    now = df.iloc[-1]
    price = float(now["close"])
    ts = int(now["time"])

    risk.on_new_bar()
    equity = exec_engine.mark_equity(price)
    signal = strategy.generate_signal(df, cfg.market_type, exec_engine.has_position(), exec_engine.position_side())
    event = "HOLD"

    if exec_engine.has_position():
        exec_engine.maybe_update_trailing_stop(price, cfg.trailing_stop_pct)
        stop_reason = exec_engine.stop_or_tp_hit(price)
        if stop_reason:
            pnl = exec_engine.close_position(price, stop_reason, ts)
            risk.on_trade_closed(pnl)
            event = f"CLOSE({stop_reason}) pnl={pnl:.2f}"
            exec_engine.save_state(cfg.persist_path)
            st.session_state.last_df = df
            st.session_state.last_event = event
            st.session_state.last_price = price
            st.session_state.last_signal = "CLOSE"
            st.session_state.last_ts = ts
            return

    if signal in {"LONG_ENTRY", "SHORT_ENTRY"}:
        can_open, reason = risk.can_open_position(equity, 1 if exec_engine.has_position() else 0)
        if not can_open:
            event = f"BLOCK({reason})"
        else:
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
            event = f"OPEN({signal}) qty={qty:.6f}"
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
            event = f"CLOSE(signal_exit) pnl={pnl:.2f}"

    exec_engine.save_state(cfg.persist_path)
    st.session_state.last_df = df
    st.session_state.last_event = event
    st.session_state.last_price = price
    st.session_state.last_signal = signal
    st.session_state.last_ts = ts


def _init_session() -> None:
    if "cfg" in st.session_state:
        return
    cfg = load_config()
    strategy, risk, exec_engine = _build_engines(cfg)
    exec_engine.load_state(cfg.persist_path)
    st.session_state.cfg = cfg
    st.session_state.client = BinanceMarketDataClient()
    st.session_state.strategy = strategy
    st.session_state.risk = risk
    st.session_state.exec_engine = exec_engine
    st.session_state.last_df = pd.DataFrame()
    st.session_state.last_event = "Not started"
    st.session_state.last_signal = "HOLD"
    st.session_state.last_price = 0.0
    st.session_state.last_ts = 0


def main() -> None:
    st.set_page_config(page_title="Binance Paper Bot Dashboard", layout="wide")
    st.title("Binance Breakout Paper Bot Dashboard")
    st.caption(f"UTC now: {datetime.now(UTC).isoformat()}")

    _init_session()
    cfg: BotConfig = st.session_state.cfg
    exec_engine: PaperExecutor = st.session_state.exec_engine

    st.sidebar.subheader("Controls")
    st.sidebar.write(f"Symbol: `{cfg.symbol}`")
    st.sidebar.write(f"Market: `{cfg.market_type}`")
    if cfg.market_type == "margin":
        st.sidebar.write(f"Leverage: `x{cfg.leverage:.2f}` (long + short)")
    else:
        st.sidebar.write("Leverage: `x1.00` (long only)")
    st.sidebar.write(f"Timeframe: `{cfg.timeframe}`")
    interval = st.sidebar.slider("Auto refresh (seconds)", min_value=0, max_value=120, value=0, step=5)
    run_once = st.sidebar.button("Run One Cycle")
    auto_run = st.sidebar.toggle("Auto Run", value=False)

    if run_once or auto_run:
        try:
            _run_one_cycle()
        except Exception as exc:
            st.error(f"Cycle error: {exc}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Last Price", f"{st.session_state.last_price:.4f}")
    equity = exec_engine.equity_curve[-1] if exec_engine.equity_curve else exec_engine.cash
    col2.metric("Equity", f"{equity:.2f}")
    col3.metric("Signal", st.session_state.last_signal)
    col4.metric("Last Event", st.session_state.last_event)

    st.subheader("Position")
    if exec_engine.position:
        st.json(exec_engine.position.__dict__)
    else:
        st.write("No open position")

    st.subheader("Equity Curve")
    if exec_engine.equity_curve:
        eq_df = pd.DataFrame({"equity": exec_engine.equity_curve[-500:]})
        st.line_chart(eq_df, x=None, y="equity")
    else:
        st.write("No equity points yet")

    st.subheader("Recent Trades")
    if exec_engine.trade_log:
        trades = pd.DataFrame([t.__dict__ for t in exec_engine.trade_log[-20:]])
        st.dataframe(trades, use_container_width=True)
    else:
        st.write("No trades yet")

    st.subheader("Latest Candles")
    if not st.session_state.last_df.empty:
        candles = st.session_state.last_df.tail(80).copy()
        st.line_chart(candles.set_index("time")["close"])
        st.dataframe(candles.tail(20), use_container_width=True)
    else:
        st.write("Run one cycle to load candles.")

    if auto_run and interval > 0:
        time.sleep(interval)
        st.rerun()


if __name__ == "__main__":
    main()
