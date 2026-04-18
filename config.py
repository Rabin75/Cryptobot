import os
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_MARKET_TYPES = {"spot", "margin"}


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        # Fallback parser so .env works even without python-dotenv.
        env_file = Path(".env")
        if not env_file.exists():
            return
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return
    load_dotenv()


@dataclass
class BotConfig:
    symbol: str = "BTC/USDT"
    symbols: list[str] | None = None
    data_sources: list[str] | None = None
    timeframe: str = "5m"
    limit: int = 200
    poll_seconds: int = 30
    market_type: str = "spot"
    initial_equity: float = 10000.0
    fee_rate: float = 0.001
    leverage: float = 3.0
    breakout_lookback: int = 20
    breakout_buffer_pct: float = 0.001
    volume_window: int = 20
    min_volume_ratio: float = 1.1
    sr_window: int = 20
    buy_near_support_pct: float = 0.01
    sell_near_resistance_pct: float = 0.99
    risk_per_trade_pct: float = 0.01
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.05
    trailing_stop_pct: float = 0.0
    max_positions: int = 1
    daily_max_drawdown_pct: float = 0.05
    cooldown_bars_after_loss: int = 3
    persist_path: str = "paper_state.json"


def _parse_market_type(raw_value: str | None) -> str:
    market_type = (raw_value or "spot").strip().lower()
    if market_type not in SUPPORTED_MARKET_TYPES:
        return "spot"
    return market_type


def load_config() -> BotConfig:
    _load_dotenv_if_available()
    symbols_raw = os.getenv("BOT_SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT")
    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
    sources_raw = os.getenv("BOT_DATA_SOURCES", "binance,coinmarketcap,bloomberg")
    requested_sources = [s.strip().lower() for s in sources_raw.split(",") if s.strip()]
    required_sources = {"binance", "coinmarketcap", "bloomberg"}
    data_sources = sorted(set(requested_sources) | required_sources)
    market_type = _parse_market_type(os.getenv("BOT_MARKET_TYPE", "spot"))
    leverage = float(os.getenv("BOT_LEVERAGE", "3"))
    if market_type == "spot":
        leverage = 1.0
    return BotConfig(
        symbol=os.getenv("BOT_SYMBOL", symbols[0] if symbols else "BTC/USDT"),
        symbols=symbols,
        data_sources=data_sources,
        timeframe=os.getenv("BOT_TIMEFRAME", "5m"),
        limit=int(os.getenv("BOT_LIMIT", "200")),
        poll_seconds=int(os.getenv("BOT_POLL_SECONDS", "30")),
        market_type=market_type,
        initial_equity=float(os.getenv("BOT_INITIAL_EQUITY", "10000")),
        fee_rate=float(os.getenv("BOT_FEE_RATE", "0.001")),
        leverage=leverage,
        breakout_lookback=int(os.getenv("BOT_BREAKOUT_LOOKBACK", "20")),
        breakout_buffer_pct=float(os.getenv("BOT_BREAKOUT_BUFFER_PCT", "0.001")),
        volume_window=int(os.getenv("BOT_VOLUME_WINDOW", "20")),
        min_volume_ratio=float(os.getenv("BOT_MIN_VOLUME_RATIO", "1.1")),
        sr_window=int(os.getenv("BOT_SR_WINDOW", "20")),
        buy_near_support_pct=float(os.getenv("BOT_BUY_NEAR_SUPPORT_PCT", "0.01")),
        sell_near_resistance_pct=float(os.getenv("BOT_SELL_NEAR_RESISTANCE_PCT", "0.99")),
        risk_per_trade_pct=float(os.getenv("BOT_RISK_PER_TRADE_PCT", "0.01")),
        stop_loss_pct=float(os.getenv("BOT_STOP_LOSS_PCT", "0.02")),
        take_profit_pct=float(os.getenv("BOT_TAKE_PROFIT_PCT", "0.04")),
        trailing_stop_pct=float(os.getenv("BOT_TRAILING_STOP_PCT", "0")),
        max_positions=int(os.getenv("BOT_MAX_POSITIONS", "1")),
        daily_max_drawdown_pct=float(os.getenv("BOT_DAILY_MAX_DRAWDOWN_PCT", "0.05")),
        cooldown_bars_after_loss=int(os.getenv("BOT_COOLDOWN_BARS_AFTER_LOSS", "3")),
        persist_path=os.getenv("BOT_PERSIST_PATH", "paper_state.json"),
    )
