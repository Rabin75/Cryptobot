import argparse
import json
import os
import time
from datetime import UTC, datetime

from backtest import run_backtest
from config import BotConfig, load_config
from exchange_client import BinanceMarketDataClient, BloombergClient, CoinMarketCapClient
from execution import PaperExecutor


def _build_engine(cfg: BotConfig, initial_equity: float) -> PaperExecutor:
    exec_engine = PaperExecutor(cfg.market_type, initial_equity, cfg.fee_rate, cfg.leverage)
    return exec_engine


def _run_backtest_mode(cfg: BotConfig, client: BinanceMarketDataClient) -> None:
    df = client.fetch_ohlcv_df(cfg.symbol, cfg.timeframe, max(cfg.limit, 500))
    metrics = run_backtest(df, cfg)
    print(json.dumps(metrics, indent=2))


def _assert_live_guard(enabled: bool) -> None:
    if not enabled:
        return
    if os.getenv("ENABLE_LIVE_TRADING") != "YES_I_UNDERSTAND_THE_RISK":
        raise RuntimeError(
            "Live mode blocked. Set ENABLE_LIVE_TRADING=YES_I_UNDERSTAND_THE_RISK "
            "to confirm risk acceptance."
        )
    raise RuntimeError("Live execution adapter is intentionally not implemented yet. Use paper mode.")


def _symbol_state_path(base_path: str, symbol: str) -> str:
    safe = symbol.replace("/", "_").replace(":", "_")
    if "." in base_path:
        root, ext = base_path.rsplit(".", 1)
        return f"{root}_{safe}.{ext}"
    return f"{base_path}_{safe}"


def _to_ticker(symbol: str) -> str:
    return symbol.split("/")[0].upper()


def _print_external_quotes(cfg: BotConfig, binance_client: BinanceMarketDataClient, symbols: list[str]) -> None:
    sources = set(cfg.data_sources or [])
    tickers = [_to_ticker(s) for s in symbols]
    rows: list[str] = []

    if "binance" in sources:
        for s in symbols:
            try:
                price = binance_client.fetch_last_price(s)
                rows.append(f"binance:{s}={price:.4f}")
            except Exception:
                rows.append(f"binance:{s}=n/a")

    if "coinmarketcap" in sources:
        cmc = CoinMarketCapClient(os.getenv("COINMARKETCAP_API_KEY"))
        try:
            quotes = cmc.fetch_usd_quotes(tickers)
            for t in tickers:
                v = quotes.get(t)
                rows.append(f"coinmarketcap:{t}={v:.4f}" if v else f"coinmarketcap:{t}=n/a")
        except Exception:
            for t in tickers:
                rows.append(f"coinmarketcap:{t}=n/a")

    if "bloomberg" in sources:
        bb = BloombergClient(os.getenv("BLOOMBERG_API_URL"), os.getenv("BLOOMBERG_API_KEY"))
        try:
            quotes = bb.fetch_usd_quotes(tickers)
            for t in tickers:
                v = quotes.get(t)
                rows.append(f"bloomberg:{t}={v:.4f}" if v else f"bloomberg:{t}=n/a")
        except Exception:
            for t in tickers:
                rows.append(f"bloomberg:{t}=n/a")

    if rows:
        print(f"[{datetime.now(UTC).isoformat()}] QUOTES {' | '.join(rows)}")


def _run_dry_mode(cfg: BotConfig, client: BinanceMarketDataClient, persist: bool) -> None:
    symbols = cfg.symbols or [cfg.symbol]
    per_symbol_equity = cfg.initial_equity / max(len(symbols), 1)
    engines: dict[str, PaperExecutor] = {}
    for symbol in symbols:
        exec_engine = _build_engine(cfg, per_symbol_equity)
        if persist:
            exec_engine.load_state(_symbol_state_path(cfg.persist_path, symbol))
        engines[symbol] = exec_engine

    print(
        f"[{datetime.now(UTC).isoformat()}] Paper bot started: symbols={symbols}, "
        f"market={cfg.market_type}, per_symbol_equity={per_symbol_equity:.2f}"
    )
    while True:
        try:
            _print_external_quotes(cfg, client, symbols)
            for symbol in symbols:
                exec_engine = engines[symbol]
                df = client.fetch_ohlcv_df(symbol, cfg.timeframe, cfg.limit)
                now = df.iloc[-1]
                price = float(now["close"])
                ts = int(now["time"])

                support = df["low"].rolling(window=cfg.sr_window).min().iloc[-1]
                resistance = df["high"].rolling(window=cfg.sr_window).max().iloc[-1]
                equity = exec_engine.mark_equity(price)
                event = "HOLD"

                if exec_engine.has_position():
                    pos = exec_engine.position
                    entry_price = pos.entry_price if pos else price
                    move_pct = ((price - entry_price) / max(entry_price, 1e-9)) * 100
                    if pos and pos.side == "long":
                        should_exit_res = price >= resistance * cfg.sell_near_resistance_pct
                        should_take_profit = move_pct >= cfg.take_profit_pct * 100
                        should_stop_loss = move_pct <= -cfg.stop_loss_pct * 100
                    else:
                        should_exit_res = price <= support * (1 + cfg.buy_near_support_pct)
                        should_take_profit = move_pct <= -cfg.take_profit_pct * 100
                        should_stop_loss = move_pct >= cfg.stop_loss_pct * 100

                    if should_exit_res or should_take_profit or should_stop_loss:
                        reason = "sr_exit" if should_exit_res else ("take_profit" if should_take_profit else "stop_loss")
                        pnl = exec_engine.close_position(price, reason, ts)
                        print(
                            f"[{ts}] {symbol} CLOSE reason={reason} price={price:.4f} "
                            f"pnl={pnl:.4f} equity={equity:.2f} S={support:.4f} R={resistance:.4f}"
                        )
                        if persist:
                            exec_engine.save_state(_symbol_state_path(cfg.persist_path, symbol))
                        continue
                else:
                    if price <= support * (1 + cfg.buy_near_support_pct):
                        qty = max((per_symbol_equity * 0.2) / max(price, 1e-9), 0.0)
                        opened = exec_engine.open_position(
                            side="long",
                            qty=qty,
                            price=price,
                            stop_loss_pct=cfg.stop_loss_pct,
                            take_profit_pct=cfg.take_profit_pct,
                            trailing_stop_pct=0.0,
                        )
                        if opened:
                            event = f"BUY qty={qty:.6f}"
                            print(
                                f"[{ts}] {symbol} OPEN signal=LONG_ENTRY qty={qty:.6f} "
                                f"price={price:.4f} equity={equity:.2f} S={support:.4f} R={resistance:.4f}"
                            )
                        else:
                            event = "HOLD"
                    elif cfg.market_type == "margin" and price >= resistance * cfg.sell_near_resistance_pct:
                        qty = max((per_symbol_equity * 0.2) / max(price, 1e-9), 0.0)
                        opened = exec_engine.open_position(
                            side="short",
                            qty=qty,
                            price=price,
                            stop_loss_pct=cfg.stop_loss_pct,
                            take_profit_pct=cfg.take_profit_pct,
                            trailing_stop_pct=0.0,
                        )
                        if opened:
                            event = f"SHORT qty={qty:.6f}"
                            print(
                                f"[{ts}] {symbol} OPEN signal=SHORT_ENTRY qty={qty:.6f} "
                                f"price={price:.4f} equity={equity:.2f} S={support:.4f} R={resistance:.4f}"
                            )
                        else:
                            event = "HOLD"
                    else:
                        print(
                            f"[{ts}] {symbol} HOLD price={price:.4f} "
                            f"equity={equity:.2f} S={support:.4f} R={resistance:.4f}"
                        )
                        event = "HOLD"

                if persist:
                    exec_engine.save_state(_symbol_state_path(cfg.persist_path, symbol))
            time.sleep(cfg.poll_seconds)
        except Exception as exc:
            print(f"[{datetime.now(UTC).isoformat()}] Error: {exc}")
            time.sleep(cfg.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance breakout paper bot (spot/margin simulation).")
    parser.add_argument("--mode", choices=["dry-run", "backtest", "live"], default="dry-run")
    parser.add_argument("--market-type", choices=["spot", "margin"], help="Override BOT_MARKET_TYPE for this run.")
    parser.add_argument("--persist-state", action="store_true", help="Persist paper state to JSON file.")
    args = parser.parse_args()

    cfg = load_config()
    if args.market_type:
        cfg.market_type = args.market_type
        if cfg.market_type == "spot":
            cfg.leverage = 1.0
    client = BinanceMarketDataClient()

    if args.mode == "backtest":
        _run_backtest_mode(cfg, client)
        return
    if args.mode == "live":
        _assert_live_guard(True)
        return
    _run_dry_mode(cfg, client, persist=args.persist_state)


if __name__ == "__main__":
    main()
