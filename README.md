# Binance Support/Resistance Paper Bot (Python)

Paper-first Binance trading bot using a simple support/resistance strategy in spot-only mode.

## Modes

- `dry-run`: Fetches live Binance candles and simulates trades only.
- `backtest`: Runs strategy on fetched historical candles and prints metrics.
- `live`: Explicitly blocked unless risk-confirmation env flag is set, and still disabled by design.

## Files

- `crypto.py`: CLI entrypoint and runtime loop.
- `config.py`: Environment-based runtime config.
- `strategy_breakout.py`: Legacy breakout module (not required for S/R loop).
- `risk.py`: Position sizing and risk checks.
- `execution.py`: Paper execution engine + state persistence.
- `exchange_client.py`: Binance OHLCV data adapter.
- `backtest.py`: Historical replay and metrics.

## Setup

1. Install dependencies:
   - `pip install ccxt pandas python-dotenv requests`
2. Copy `.env.example` to `.env` and tune parameters.
3. Run one of:
   - Dry-run: `python3 crypto.py --mode dry-run --persist-state`
   - Backtest: `python3 crypto.py --mode backtest`
   - GUI dashboard: `streamlit run gui_app.py`
   - Desktop GUI (no extra package): `python3 gui_tk.py` (enable Demo Mode checkbox for faster trade activity)

## Safety

- This project is paper-first. Real order execution is intentionally not implemented.
- Do not hardcode API credentials in source files.

## Multi-Asset 24h Demo

- Default demo symbols: `BTC/USDT,ETH/USDT,SOL/USDT`
- Support/resistance is computed per symbol from rolling highs/lows (`BOT_SR_WINDOW`).
- Trade rules:
  - Buy when price is near support (`BOT_BUY_NEAR_SUPPORT_PCT`)
  - Sell near resistance (`BOT_SELL_NEAR_RESISTANCE_PCT`) or at TP/SL (`+5%/-3%` defaults)
- Spot-only long strategy (futures/short disabled).
- External quote feeds are optional and printed in logs when available:
  - Binance (public)
  - CoinMarketCap (`COINMARKETCAP_API_KEY`)
  - Bloomberg via user-supplied REST proxy (`BLOOMBERG_API_URL`, `BLOOMBERG_API_KEY`)
- Small-investment behavior is automatic: total equity is split equally per symbol in paper mode.

Example:

- `python3 -u crypto.py --mode dry-run --persist-state`
