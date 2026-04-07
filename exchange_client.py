import ccxt
import pandas as pd
import requests


class BinanceMarketDataClient:
    def __init__(self) -> None:
        self.exchange = ccxt.binance({"enableRateLimit": True})

    def fetch_ohlcv_df(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"],
        )

    def fetch_last_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])


class CoinMarketCapClient:
    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key
        self.url = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"

    def fetch_usd_quotes(self, tickers: list[str]) -> dict[str, float]:
        if not self.api_key:
            return {}
        headers = {"X-CMC_PRO_API_KEY": self.api_key}
        params = {"symbol": ",".join(tickers)}
        resp = requests.get(self.url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", {})
        quotes: dict[str, float] = {}
        for ticker in tickers:
            rows = data.get(ticker, [])
            if rows:
                quotes[ticker] = float(rows[0]["quote"]["USD"]["price"])
        return quotes


class BloombergClient:
    def __init__(self, api_url: str | None, api_key: str | None) -> None:
        self.api_url = api_url
        self.api_key = api_key

    def fetch_usd_quotes(self, tickers: list[str]) -> dict[str, float]:
        # Bloomberg has multiple products/APIs. This connector expects a user-provided REST proxy.
        if not self.api_url or not self.api_key:
            return {}
        resp = requests.get(
            self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            params={"symbols": ",".join(tickers)},
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        prices = payload.get("prices", {})
        return {k.upper(): float(v) for k, v in prices.items() if k and v is not None}
