import hmac
import hashlib
import time
import math
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlencode
import requests

import config

DEFAULT_TICKERS = [
    {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "price": 67450.0, "change_pct": 2.85, "high": 68200.0, "low": 65800.0, "volume": 32450.0, "quote_volume": 2150000000.0},
    {"symbol": "ETHUSDT", "base_asset": "ETH", "quote_asset": "USDT", "price": 3280.0, "change_pct": 3.42, "high": 3340.0, "low": 3190.0, "volume": 185000.0, "quote_volume": 605000000.0},
    {"symbol": "SOLUSDT", "base_asset": "SOL", "quote_asset": "USDT", "price": 182.5, "change_pct": 5.65, "high": 188.0, "low": 172.0, "volume": 850000.0, "quote_volume": 155000000.0},
    {"symbol": "BNBUSDT", "base_asset": "BNB", "quote_asset": "USDT", "price": 595.0, "change_pct": 1.25, "high": 605.0, "low": 588.0, "volume": 120000.0, "quote_volume": 71400000.0},
    {"symbol": "DOGEUSDT", "base_asset": "DOGE", "quote_asset": "USDT", "price": 0.1625, "change_pct": 7.82, "high": 0.170, "low": 0.150, "volume": 520000000.0, "quote_volume": 84000000.0},
    {"symbol": "XRPUSDT", "base_asset": "XRP", "quote_asset": "USDT", "price": 0.6250, "change_pct": -1.15, "high": 0.640, "low": 0.615, "volume": 110000000.0, "quote_volume": 68750000.0},
    {"symbol": "ADAUSDT", "base_asset": "ADA", "quote_asset": "USDT", "price": 0.4450, "change_pct": 2.10, "high": 0.460, "low": 0.435, "volume": 65000000.0, "quote_volume": 28925000.0},
    {"symbol": "AVAXUSDT", "base_asset": "AVAX", "quote_asset": "USDT", "price": 28.40, "change_pct": 4.25, "high": 29.50, "low": 27.10, "volume": 1800000.0, "quote_volume": 51120000.0},
    {"symbol": "LINKUSDT", "base_asset": "LINK", "quote_asset": "USDT", "price": 14.80, "change_pct": 3.15, "high": 15.30, "low": 14.20, "volume": 2400000.0, "quote_volume": 35520000.0},
    {"symbol": "SUIUSDT", "base_asset": "SUI", "quote_asset": "USDT", "price": 2.155, "change_pct": 8.50, "high": 2.280, "low": 1.980, "volume": 42000000.0, "quote_volume": 90300000.0},
    {"symbol": "NEARUSDT", "base_asset": "NEAR", "quote_asset": "USDT", "price": 5.45, "change_pct": 4.80, "high": 5.70, "low": 5.15, "volume": 8500000.0, "quote_volume": 46325000.0},
    {"symbol": "PEPEUSDT", "base_asset": "PEPE", "quote_asset": "USDT", "price": 0.0000105, "change_pct": 11.20, "high": 0.0000115, "low": 0.0000094, "volume": 8500000000000.0, "quote_volume": 89250000.0},
    {"symbol": "WIFUSDT", "base_asset": "WIF", "quote_asset": "USDT", "price": 2.65, "change_pct": 6.30, "high": 2.80, "low": 2.45, "volume": 18000000.0, "quote_volume": 47700000.0},
    {"symbol": "APTUSDT", "base_asset": "APT", "quote_asset": "USDT", "price": 9.80, "change_pct": -2.40, "high": 10.30, "low": 9.60, "volume": 3200000.0, "quote_volume": 31360000.0},
    {"symbol": "ARBUSDT", "base_asset": "ARB", "quote_asset": "USDT", "price": 0.585, "change_pct": 1.55, "high": 0.610, "low": 0.560, "volume": 35000000.0, "quote_volume": 20300000.0},
    {"symbol": "OPUSDT", "base_asset": "OP", "quote_asset": "USDT", "price": 1.75, "change_pct": 3.85, "high": 1.85, "low": 1.68, "volume": 12000000.0, "quote_volume": 21000000.0},
    {"symbol": "INJUSDT", "base_asset": "INJ", "quote_asset": "USDT", "price": 22.40, "change_pct": 5.10, "high": 23.50, "low": 21.20, "volume": 1100000.0, "quote_volume": 24640000.0},
    {"symbol": "RENDERUSDT", "base_asset": "RENDER", "quote_asset": "USDT", "price": 6.80, "change_pct": 4.60, "high": 7.15, "low": 6.45, "volume": 3800000.0, "quote_volume": 25840000.0},
    {"symbol": "FETUSDT", "base_asset": "FET", "quote_asset": "USDT", "price": 1.42, "change_pct": 6.80, "high": 1.52, "low": 1.33, "volume": 16000000.0, "quote_volume": 22720000.0},
    {"symbol": "DOTUSDT", "base_asset": "DOT", "quote_asset": "USDT", "price": 4.50, "change_pct": 1.90, "high": 4.65, "low": 4.38, "volume": 4500000.0, "quote_volume": 20250000.0},
    {"symbol": "MATICUSDT", "base_asset": "MATIC", "quote_asset": "USDT", "price": 0.420, "change_pct": 2.20, "high": 0.440, "low": 0.400, "volume": 32000000.0, "quote_volume": 13440000.0}
]

class BinanceTestnetClient:
    """
    Lightweight, robust client for Binance Spot and USDT-M Futures.
    Supports both live signed execution and simulated dry-run paper trading.
    Uses standard requests and native HMAC-SHA256 request signing.
    """

    def __init__(self, api_key: str = None, api_secret: str = None, base_url: str = None, market_type: str = None):
        self.market_type = (market_type or getattr(config, "MARKET_TYPE", "FUTURES")).upper()
        self.api_key = api_key or config.API_KEY
        self.api_secret = api_secret or config.API_SECRET
        
        default_base = getattr(config, "FUTURES_BASE_URL", "https://fapi.binance.com") if self.market_type == "FUTURES" else getattr(config, "SPOT_BASE_URL", "https://testnet.binance.vision")
        self.base_url = (base_url or getattr(config, "BASE_URL", default_base)).rstrip("/")
        
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "BinanceFuturesBot/2.0"
        })
        # In-memory TTL caches to eliminate latency and redundant network requests
        self._price_cache: Dict[str, Tuple[float, float]] = {}  # symbol -> (price, timestamp)
        self._all_tickers_cache: Optional[Tuple[Dict[str, float], float]] = None  # (price_map, timestamp)
        self._24hr_tickers_cache: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}  # quote -> (tickers, timestamp)
        self._klines_cache: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}  # f"{symbol}_{interval}" -> (candles, timestamp)
        self._bals_cache: Optional[Tuple[Dict[str, Dict[str, float]], float]] = None  # (balances, timestamp)
        self._time_offset = 0

    def _endpoint(self, endpoint: str) -> str:
        """Translates standard endpoint paths to Futures (/fapi/) or Spot (/api/v3/)."""
        if self.market_type == "FUTURES":
            if endpoint.startswith("/api/v3/account"):
                return "/fapi/v2/account"
            if endpoint.startswith("/api/v3/positionRisk"):
                return "/fapi/v2/positionRisk"
            if endpoint.startswith("/api/v3/"):
                return endpoint.replace("/api/v3/", "/fapi/v1/")
        return endpoint

    def sync_server_time(self):
        """Synchronizes local time offset with Binance server time."""
        try:
            res = self.session.get(f"{self.base_url}{self._endpoint('/api/v3/time')}", timeout=5).json()
            server_time = res.get("serverTime")
            if server_time:
                local_time = int(time.time() * 1000)
                self._time_offset = server_time - local_time
        except Exception:
            pass

    def _get_timestamp(self) -> int:
        """Returns synchronized epoch timestamp in milliseconds."""
        return int(time.time() * 1000) + getattr(self, "_time_offset", 0)

    def _sign_payload(self, params: Dict[str, Any]) -> str:
        """Computes HMAC-SHA256 signature for parameters."""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _request(self, method: str, endpoint: str, signed: bool = False, params: Dict[str, Any] = None) -> Any:
        """
        Executes HTTP request against Binance API.
        Handles endpoint translation, signature generation, headers, and error handling.
        """
        endpoint = self._endpoint(endpoint)
        url = f"{self.base_url}{endpoint}"
        params = params.copy() if params else {}

        headers = {}
        if signed or self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        if signed:
            if not self.api_key or not self.api_secret:
                raise ValueError("API Key and Secret must be provided for signed requests.")
            params["timestamp"] = self._get_timestamp()
            params["recvWindow"] = 15000
            params["signature"] = self._sign_payload(params)

        try:
            if method.upper() == "GET":
                response = self.session.get(url, params=params, headers=headers, timeout=10)
            elif method.upper() == "POST":
                response = self.session.post(url, params=params, headers=headers, timeout=10)
            elif method.upper() == "DELETE":
                response = self.session.delete(url, params=params, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            data = response.json()
            if response.status_code >= 400 or (isinstance(data, dict) and "code" in data and data["code"] < 0):
                error_msg = data.get("msg", "Unknown error")
                error_code = data.get("code", response.status_code)
                # Auto-sync time drift on error -1021 and retry once
                if error_code == -1021 and signed:
                    self.sync_server_time()
                    params["timestamp"] = self._get_timestamp()
                    params["signature"] = self._sign_payload(params)
                    if method.upper() == "GET":
                        response = self.session.get(url, params=params, headers=headers, timeout=10)
                    elif method.upper() == "POST":
                        response = self.session.post(url, params=params, headers=headers, timeout=10)
                    elif method.upper() == "DELETE":
                        response = self.session.delete(url, params=params, headers=headers, timeout=10)
                    data = response.json()
                    if not (response.status_code >= 400 or (isinstance(data, dict) and "code" in data and data["code"] < 0)):
                        return data
                raise RuntimeError(f"Binance API Error [{error_code}]: {error_msg}")

            return data

        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Network error contacting Binance: {e}")

    # ==========================
    # Public Market Data Endpoints
    # ==========================

    def ping(self) -> bool:
        """Tests connectivity to the testnet."""
        res = self._request("GET", "/api/v3/ping")
        return res == {}

    def get_server_time(self) -> int:
        """Returns Binance server time in milliseconds."""
        res = self._request("GET", "/api/v3/time")
        return res.get("serverTime", 0)

    def get_all_ticker_prices(self, max_age_seconds: float = 4.0) -> Dict[str, float]:
        """
        Fetches all current ticker prices in a single batch request and caches them.
        Returns a dict of symbol -> price.
        """
        now = time.time()
        if self._all_tickers_cache is not None:
            price_map, ts = self._all_tickers_cache
            if now - ts < max_age_seconds:
                return price_map

        try:
            raw_list = self._request("GET", "/api/v3/ticker/price")
            if isinstance(raw_list, list):
                price_map = {}
                for item in raw_list:
                    s = item.get("symbol")
                    p_str = item.get("price")
                    if s and p_str is not None:
                        try:
                            p_float = float(p_str)
                            price_map[s] = p_float
                            self._price_cache[s] = (p_float, now)
                        except (ValueError, TypeError):
                            continue
                self._all_tickers_cache = (price_map, now)
                return price_map
        except Exception as e:
            if self._all_tickers_cache is not None:
                return self._all_tickers_cache[0]
            if getattr(config, "DRY_RUN", True):
                try:
                    tickers = self.get_24hr_tickers()
                    price_map = {t["symbol"]: t["price"] for t in tickers}
                    self._all_tickers_cache = (price_map, now)
                    return price_map
                except Exception:
                    pass
            raise e

        return {}

    def get_ticker_price(self, symbol: str, max_age_seconds: float = 3.0) -> float:
        """
        Returns the current market price for a symbol.
        Uses in-memory cache to prevent redundant HTTP requests.
        """
        sym = symbol.upper()
        now = time.time()

        # Check individual price cache
        if sym in self._price_cache:
            price, ts = self._price_cache[sym]
            if now - ts < max_age_seconds:
                return price

        # Check all_tickers_cache if available
        if self._all_tickers_cache is not None:
            price_map, ts = self._all_tickers_cache
            if now - ts < max_age_seconds and sym in price_map:
                p = price_map[sym]
                self._price_cache[sym] = (p, now)
                return p

        # Fallback to direct single API call
        try:
            res = self._request("GET", "/api/v3/ticker/price", params={"symbol": sym})
            p = float(res["price"])
            self._price_cache[sym] = (p, now)
            return p
        except Exception as e:
            if sym in self._price_cache:
                return self._price_cache[sym][0]
            if getattr(config, "DRY_RUN", True):
                for t in DEFAULT_TICKERS:
                    if t["symbol"] == sym:
                        self._price_cache[sym] = (t["price"], now)
                        return t["price"]
                default_prices = {
                    "BTCUSDT": 67450.0, "ETHUSDT": 3280.0, "SOLUSDT": 182.5,
                    "BNBUSDT": 595.0, "DOGEUSDT": 0.162, "XRPUSDT": 0.625
                }
                return default_prices.get(sym, 10.0)
            raise e

    def generate_simulated_klines(self, symbol: str, interval: str = "1m", limit: int = 50) -> List[Dict[str, Any]]:
        """Generates realistic candlestick bars for offline dry-run charts & indicators across any timeframe."""
        current_p = self.get_ticker_price(symbol)
        now_ms = int(time.time() * 1000)
        interval_map = {
            "1m": 60000,
            "3m": 180000,
            "5m": 300000,
            "15m": 900000,
            "30m": 1800000,
            "1h": 3600000,
            "2h": 7200000,
            "4h": 14400000,
            "1d": 86400000
        }
        interval_ms = interval_map.get(interval.lower(), 60000)

        # Scale volatility based on timeframe (1h has larger price waves than 1m)
        tf_scale = 1.0
        if "15m" in interval.lower():
            tf_scale = 2.2
        elif "1h" in interval.lower():
            tf_scale = 4.0
        elif "4h" in interval.lower() or "1d" in interval.lower():
            tf_scale = 7.5

        candles = []
        p = current_p * (0.988 if "1m" in interval else (0.975 if "15m" in interval else 0.950))
        t = now_ms - (limit * interval_ms)
        for i in range(limit):
            open_p = p
            delta_pct = ((math.sin(i * 0.35 + len(symbol)) * 0.003) + (0.0006 if i % 4 == 0 else -0.0003)) * tf_scale
            close_p = round(open_p * (1.0 + delta_pct), 4 if open_p < 10 else 2)
            wick_spread = 0.0018 * tf_scale
            high_p = round(max(open_p, close_p) * (1.0 + wick_spread), 4 if open_p < 10 else 2)
            low_p = round(min(open_p, close_p) * (1.0 - wick_spread), 4 if open_p < 10 else 2)
            vol = round((abs(math.cos(i)) * 45.0 + 12.0) * tf_scale, 2)
            candles.append({
                "open_time": t,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": vol,
                "close_time": t + interval_ms - 1
            })
            p = close_p
            t += interval_ms

        if candles:
            candles[-1]["close"] = current_p
            candles[-1]["high"] = max(candles[-1]["high"], current_p)
            candles[-1]["low"] = min(candles[-1]["low"], current_p)
        return candles

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 50, max_age_seconds: float = None) -> List[Dict[str, Any]]:
        """
        Fetches historical candlestick (OHLCV) bars with in-memory TTL caching.
        Returns a list of dicts with open, high, low, close, volume, timestamp.
        """
        cache_key = f"{symbol.upper()}_{interval.lower()}"
        now = time.time()

        # Dynamic cache duration by timeframe (higher timeframes change slower)
        if max_age_seconds is None:
            if interval.lower() in ("1m", "3m"):
                ttl = 4.0
            elif interval.lower() in ("5m", "15m"):
                ttl = 30.0
            elif interval.lower() in ("1h", "2h", "4h"):
                ttl = 90.0
            else:
                ttl = 180.0
        else:
            ttl = max_age_seconds

        if cache_key in self._klines_cache:
            cached_candles, ts = self._klines_cache[cache_key]
            if now - ts < ttl:
                return cached_candles

        try:
            raw_klines = self._request("GET", "/api/v3/klines", params={
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": limit
            })

            candles = []
            for k in raw_klines:
                candles.append({
                    "open_time": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": int(k[6])
                })
            self._klines_cache[cache_key] = (candles, now)
            return candles
        except Exception as e:
            if getattr(config, "DRY_RUN", True):
                sim_candles = self.generate_simulated_klines(symbol.upper(), interval=interval, limit=limit)
                self._klines_cache[cache_key] = (sim_candles, now)
                return sim_candles
            if cache_key in self._klines_cache:
                return self._klines_cache[cache_key][0]
            raise e

    def get_symbol_filters(self, symbol: str) -> Dict[str, float]:
        """
        Extracts stepSize, minQty, tickSize, and minNotional from exchange information.
        Used to sanitize order sizes and avoid filter rejections.
        """
        sym_clean = symbol.upper()
        params = {"symbol": sym_clean} if self.market_type != "FUTURES" else {}
        try:
            res = self._request("GET", "/api/v3/exchangeInfo", params=params)
        except Exception:
            # Fallback for Futures in dry-run if network is blocked
            return {
                "min_qty": 0.001,
                "max_qty": 100000.0,
                "step_size": 0.001,
                "min_price": 0.01,
                "tick_size": 0.01,
                "min_notional": 5.0
            }

        symbols_info = res.get("symbols", [])
        matched = [s for s in symbols_info if s.get("symbol") == sym_clean]
        if not matched:
            # If exchangeInfo returns a list without the symbol, provide safe fallback
            return {
                "min_qty": 0.001,
                "max_qty": 100000.0,
                "step_size": 0.001,
                "min_price": 0.01,
                "tick_size": 0.01,
                "min_notional": 5.0 if self.market_type == "FUTURES" else 10.0
            }

        symbol_info = matched[0]
        filters = {
            "min_qty": 0.001,
            "max_qty": 100000.0,
            "step_size": 0.001,
            "min_price": 0.01,
            "tick_size": 0.01,
            "min_notional": 5.0 if self.market_type == "FUTURES" else 10.0
        }

        for f in symbol_info.get("filters", []):
            filter_type = f.get("filterType")
            if filter_type == "LOT_SIZE":
                filters["min_qty"] = float(f.get("minQty", 0.001))
                filters["max_qty"] = float(f.get("maxQty", 100000.0))
                filters["step_size"] = float(f.get("stepSize", 0.001))
            elif filter_type == "PRICE_FILTER":
                filters["min_price"] = float(f.get("minPrice", 0.01))
                filters["tick_size"] = float(f.get("tickSize", 0.01))
            elif filter_type in ("MIN_NOTIONAL", "NOTIONAL"):
                filters["min_notional"] = float(f.get("minNotional", f.get("notional", 5.0)))

        return filters

    @staticmethod
    def adjust_to_step_size(quantity: float, step_size: float) -> float:
        """Rounds quantity down to the nearest valid step size."""
        if step_size <= 0:
            return quantity
        precision = max(0, int(round(-math.log10(step_size)))) if step_size < 1 else 0
        factor = 10 ** precision
        return math.floor(quantity * factor) / factor

    def calculate_valid_quantity(self, symbol: str, target_usd: float = 20.0, current_price: float = None) -> float:
        """
        Calculates a strictly valid order quantity for ANY symbol based on target USD value.
        Guarantees that LOT_SIZE, stepSize, minQty, and MIN_NOTIONAL are satisfied.
        """
        filters = self.get_symbol_filters(symbol)
        if current_price is None or current_price <= 0:
            price = self.get_ticker_price(symbol)
        else:
            price = current_price
        step_size = filters.get("step_size", 0.001)
        min_qty = filters.get("min_qty", 0.001)
        min_notional = filters.get("min_notional", 5.0 if self.market_type == "FUTURES" else 10.0)

        raw_qty = max(target_usd / price, min_qty)
        if raw_qty * price < min_notional:
            raw_qty = (min_notional * 1.15) / price

        precision = max(0, int(round(-math.log10(step_size)))) if step_size < 1 else 0
        qty = math.floor(raw_qty / step_size) * step_size
        qty = round(qty, precision)
        if qty < min_qty:
            qty = min_qty
        return qty

    # ==========================
    # Private Account & Orders
    # ==========================

    def get_account_balances(self, max_age_seconds: float = 4.0) -> Dict[str, Dict[str, float]]:
        """Returns available free and locked balances for all non-zero assets."""
        if getattr(config, "DRY_RUN", True):
            return {"USDT": {"free": 1000.0, "locked": 0.0}}

        now = time.time()
        if self._bals_cache is not None:
            cached_bals, ts = self._bals_cache
            if now - ts < max_age_seconds:
                return cached_bals

        try:
            if self.market_type == "FUTURES":
                res = self._request("GET", "/api/v3/account", signed=True)
                balances = {}
                for b in res.get("assets", []):
                    free = float(b.get("availableBalance", 0.0))
                    wallet = float(b.get("walletBalance", 0.0))
                    locked = max(0.0, wallet - free)
                    if wallet > 0 or free > 0:
                        balances[b.get("asset")] = {"free": free, "locked": locked}
                self._bals_cache = (balances, now)
                return balances
            else:
                res = self._request("GET", "/api/v3/account", signed=True)
                balances = {}
                for b in res.get("balances", []):
                    free = float(b["free"])
                    locked = float(b["locked"])
                    if free > 0 or locked > 0:
                        balances[b["asset"]] = {"free": free, "locked": locked}
                self._bals_cache = (balances, now)
                return balances
        except Exception as e:
            if self._bals_cache is not None:
                return self._bals_cache[0]
            if getattr(config, "DRY_RUN", True):
                return {"USDT": {"free": 1000.0, "locked": 0.0}}
            raise e

    def get_asset_balance(self, asset: str) -> float:
        """Returns free available balance for a specific asset (e.g. USDT, BTC)."""
        balances = self.get_account_balances()
        return balances.get(asset.upper(), {}).get("free", 0.0)

    def set_leverage(self, symbol: str, leverage: int = 5) -> Dict[str, Any]:
        """Sets leverage for a symbol (e.g. 5x)."""
        if getattr(config, "DRY_RUN", True):
            return {"symbol": symbol.upper(), "leverage": int(leverage), "maxNotionalValue": "1000000"}
        return self._request("POST", "/fapi/v1/leverage", signed=True, params={
            "symbol": symbol.upper(),
            "leverage": int(leverage)
        })

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        """Sets margin type (ISOLATED or CROSSED)."""
        if getattr(config, "DRY_RUN", True):
            return {"code": 200, "msg": "success"}
        try:
            return self._request("POST", "/fapi/v1/marginType", signed=True, params={
                "symbol": symbol.upper(),
                "marginType": margin_type.upper()
            })
        except Exception:
            return {"code": 200, "msg": "margin type already set"}

    def get_position_risk(self, symbol: str = None) -> List[Dict[str, Any]]:
        """Returns active position risk (open contracts, leverage, liquidation price)."""
        if getattr(config, "DRY_RUN", True):
            return []
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/api/v3/positionRisk", signed=True, params=params)

    def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "MARKET",
        quantity: float = None,
        price: float = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False
    ) -> Dict[str, Any]:
        """
        Places a new order on Binance (or simulates in DRY_RUN mode).
        side: 'BUY' or 'SELL'
        order_type: 'MARKET' or 'LIMIT'
        reduce_only: True when closing a futures position
        """
        self._bals_cache = None

        if getattr(config, "DRY_RUN", True):
            order_id = int(time.time() * 1000)
            return {
                "orderId": order_id,
                "symbol": symbol.upper(),
                "status": "FILLED",
                "clientOrderId": f"dry_run_{order_id}",
                "price": str(price or 0.0),
                "origQty": str(quantity),
                "executedQty": str(quantity),
                "side": side.upper(),
                "type": order_type.upper(),
                "reduceOnly": reduce_only
            }

        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": f"{quantity:.8f}".rstrip("0").rstrip(".")
        }

        if self.market_type == "FUTURES" and reduce_only:
            params["reduceOnly"] = "true"

        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("Limit orders require a price.")
            params["price"] = f"{price:.8f}".rstrip("0").rstrip(".")
            params["timeInForce"] = time_in_force

        return self._request("POST", "/api/v3/order", signed=True, params=params)

    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancels an active open order."""
        self._bals_cache = None
        return self._request("DELETE", "/api/v3/order", signed=True, params={
            "symbol": symbol.upper(),
            "orderId": order_id
        })

    def get_open_orders(self, symbol: str = None) -> List[Dict[str, Any]]:
        """Returns all open orders."""
        params = {}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/api/v3/openOrders", signed=True, params=params)

    def get_all_orders(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns all historical orders for a symbol from Binance Testnet."""
        return self._request("GET", "/api/v3/allOrders", signed=True, params={
            "symbol": symbol.upper(),
            "limit": limit
        })

    def get_my_trades(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns trade executions for an account and symbol."""
        return self._request("GET", "/api/v3/myTrades", signed=True, params={
            "symbol": symbol.upper(),
            "limit": limit
        })

    def get_24hr_tickers(self, quote_asset: str = "USDT", max_age_seconds: float = 45.0) -> List[Dict[str, Any]]:
        """
        Fetches 24hr statistics for all trading pairs, filtered by quote asset.
        Returns symbol, lastPrice, priceChangePercent, volume, highPrice, lowPrice.
        Caches results to prevent multi-megabyte download bottlenecks.
        """
        quote = quote_asset.upper()
        now = time.time()

        if quote in self._24hr_tickers_cache:
            cached_data, ts = self._24hr_tickers_cache[quote]
            if now - ts < max_age_seconds:
                return cached_data

        try:
            raw_tickers = self._request("GET", "/api/v3/ticker/24hr")
            tickers = []
            for t in raw_tickers:
                sym = t.get("symbol", "")
                if sym.endswith(quote):
                    try:
                        tickers.append({
                            "symbol": sym,
                            "base_asset": sym[:-len(quote)],
                            "quote_asset": quote,
                            "price": float(t.get("lastPrice", 0.0)),
                            "change_pct": float(t.get("priceChangePercent", 0.0)),
                            "high": float(t.get("highPrice", 0.0)),
                            "low": float(t.get("lowPrice", 0.0)),
                            "volume": float(t.get("volume", 0.0)),
                            "quote_volume": float(t.get("quoteVolume", 0.0))
                        })
                    except (ValueError, TypeError):
                        continue
            # Sort by quote volume descending
            tickers.sort(key=lambda x: x["quote_volume"], reverse=True)
            self._24hr_tickers_cache[quote] = (tickers, now)
            return tickers
        except Exception as e:
            if quote in self._24hr_tickers_cache:
                return self._24hr_tickers_cache[quote][0]
            if getattr(config, "DRY_RUN", True):
                sim_tickers = [dict(t) for t in DEFAULT_TICKERS]
                self._24hr_tickers_cache[quote] = (sim_tickers, now)
                return sim_tickers
            raise e

    def get_detailed_portfolio(self) -> Dict[str, Any]:
        """
        Fetches all wallet balances and computes their USD/USDT value and portfolio allocation.
        Uses single-batch price map to execute in milliseconds.
        """
        balances = self.get_account_balances()
        if not balances:
            return {"total_value_usdt": 0.0, "assets": []}

        # Fetch all ticker prices via single batch call
        try:
            price_map = self.get_all_ticker_prices(max_age_seconds=5.0)
        except Exception:
            price_map = {}

        assets_list = []
        total_portfolio_usdt = 0.0

        for asset, data in balances.items():
            free = data["free"]
            locked = data["locked"]
            total_qty = free + locked
            if total_qty <= 0:
                continue

            # Determine price in USDT
            if asset.upper() in ("USDT", "BUSD", "USDC", "FDUSD"):
                price_usdt = 1.0
            else:
                pair = f"{asset.upper()}USDT"
                price_usdt = price_map.get(pair, 0.0)
                if price_usdt == 0.0:
                    try:
                        price_usdt = self.get_ticker_price(pair)
                    except Exception:
                        price_usdt = 0.0

            value_usdt = total_qty * price_usdt
            total_portfolio_usdt += value_usdt

            # Filter dust assets (< $0.50) from cluttering the wallet breakdown table
            if value_usdt < 0.50 and asset.upper() != "USDT":
                continue

            assets_list.append({
                "asset": asset,
                "free": free,
                "locked": locked,
                "total": total_qty,
                "price_usdt": price_usdt,
                "value_usdt": value_usdt
            })

        # Calculate allocation percentage
        for a in assets_list:
            if total_portfolio_usdt > 0:
                a["allocation_pct"] = round((a["value_usdt"] / total_portfolio_usdt) * 100.0, 2)
            else:
                a["allocation_pct"] = 0.0

        # Sort assets by total value descending
        assets_list.sort(key=lambda x: x["value_usdt"], reverse=True)

        return {
            "total_value_usdt": round(total_portfolio_usdt, 2),
            "assets": assets_list
        }

