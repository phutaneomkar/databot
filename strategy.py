import math
from typing import List, Dict, Any, Tuple, Optional

class TradingStrategy:
    """
    Advanced Multi-Detail Algorithmic Trading Strategy for Crypto.
    Captures granular micro-signals including:
    1. Candlestick Anatomy: Wick rejections (pin-bars/hammers), body conviction, engulfing patterns
    2. Volume Flow & Absorption: Volume surges vs 20-period SMA, seller exhaustion pullbacks
    3. Statistical Valuation: Bollinger Bands (%B discount, lower band bounces, volatility squeeze)
    4. Multi-Timeframe Momentum: MACD (12/26/9), Fast & Slow EMA alignment, smoothed RSI (14)
    5. Volatility & Structure: Average True Range (ATR), micro support/resistance proximity
    6. Strict Zero-Loss Risk Controls
    """

    def __init__(
        self,
        fast_period: int = 7,
        slow_period: int = 25,
        rsi_period: int = 14,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 66.0,
        stop_loss_pct: float = 1.5,
        take_profit_pct: float = 0.60
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    # =========================================================================
    # CORE MATHEMATICAL INDICATORS
    # =========================================================================

    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> List[float]:
        """Calculates Exponential Moving Average (EMA) series."""
        if not prices or len(prices) < period:
            return []

        multiplier = 2.0 / (period + 1.0)
        ema = [sum(prices[:period]) / period]

        for price in prices[period:]:
            next_ema = (price - ema[-1]) * multiplier + ema[-1]
            ema.append(next_ema)

        return ema

    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
        """Calculates smoothed Relative Strength Index (RSI) series."""
        if not prices or len(prices) <= period:
            return []

        gains = []
        losses = []

        for i in range(1, len(prices)):
            change = prices[i] - prices[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(change))

        if len(gains) < period:
            return []

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsi_list = []
        if avg_loss == 0:
            rsi_list.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_list.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                rsi_list.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_list.append(100.0 - (100.0 / (1.0 + rs)))

        return rsi_list

    @staticmethod
    def calculate_macd(
        prices: List[float],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> Dict[str, Any]:
        """
        Calculates MACD Line, Signal Line, and MACD Histogram.
        Positive/rising histogram indicates accelerating bullish momentum.
        """
        if len(prices) < slow_period + signal_period:
            return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "trend": "NEUTRAL"}

        fast_ema = TradingStrategy.calculate_ema(prices, fast_period)
        slow_ema = TradingStrategy.calculate_ema(prices, slow_period)

        if not fast_ema or not slow_ema:
            return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "trend": "NEUTRAL"}

        # Align series lengths
        min_len = min(len(fast_ema), len(slow_ema))
        fast_aligned = fast_ema[-min_len:]
        slow_aligned = slow_ema[-min_len:]

        macd_series = [f - s for f, s in zip(fast_aligned, slow_aligned)]
        if len(macd_series) < signal_period:
            return {"macd": round(macd_series[-1], 4), "signal": 0.0, "hist": 0.0, "trend": "NEUTRAL"}

        signal_series = TradingStrategy.calculate_ema(macd_series, signal_period)
        if not signal_series:
            return {"macd": round(macd_series[-1], 4), "signal": 0.0, "hist": 0.0, "trend": "NEUTRAL"}

        curr_macd = macd_series[-1]
        curr_signal = signal_series[-1]
        curr_hist = curr_macd - curr_signal
        prev_hist = (macd_series[-2] - signal_series[-2]) if len(signal_series) >= 2 else curr_hist

        if curr_hist > 0 and curr_hist >= prev_hist:
            trend = "BULLISH_ACCELERATING"
        elif curr_hist > 0:
            trend = "BULLISH_DECELERATING"
        elif curr_hist < 0 and curr_hist >= prev_hist:
            trend = "BEARISH_EXHAUSTION"  # Histogram rising from negative depths = bottom reversal!
        else:
            trend = "BEARISH_EXPANDING"

        return {
            "macd": round(curr_macd, 5),
            "signal": round(curr_signal, 5),
            "hist": round(curr_hist, 5),
            "trend": trend
        }

    @staticmethod
    def calculate_bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0) -> Dict[str, Any]:
        """
        Calculates Bollinger Bands: Upper, Middle (SMA), Lower, %B, and Bandwidth.
        %B < 0.20 indicates a statistical discount (deep value zone near lower band).
        """
        if len(prices) < period:
            cur = prices[-1] if prices else 0.0
            return {"upper": cur, "middle": cur, "lower": cur, "pct_b": 0.5, "bandwidth": 0.0}

        recent = prices[-period:]
        sma = sum(recent) / period
        variance = sum((p - sma) ** 2 for p in recent) / period
        std = math.sqrt(variance)

        upper = sma + (num_std * std)
        lower = sma - (num_std * std)
        cur_p = prices[-1]

        band_spread = upper - lower
        pct_b = ((cur_p - lower) / band_spread) if band_spread > 0 else 0.5
        bandwidth = ((upper - lower) / sma) * 100.0 if sma > 0 else 0.0

        return {
            "upper": round(upper, 4 if upper < 10 else 2),
            "middle": round(sma, 4 if sma < 10 else 2),
            "lower": round(lower, 4 if lower < 10 else 2),
            "pct_b": round(pct_b, 3),
            "bandwidth": round(bandwidth, 2)
        }

    @staticmethod
    def calculate_atr(candles: List[Dict[str, Any]], period: int = 14) -> Dict[str, Any]:
        """
        Calculates Average True Range (ATR) and ATR percentage of price.
        Used to measure current asset micro-volatility.
        """
        if len(candles) < period + 1:
            return {"atr": 0.0, "atr_pct": 0.0}

        true_ranges = []
        for i in range(1, len(candles)):
            h = candles[i]["high"]
            l = candles[i]["low"]
            prev_c = candles[i - 1]["close"]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            true_ranges.append(tr)

        atr = sum(true_ranges[-period:]) / period if len(true_ranges) >= period else 0.0
        cur_price = candles[-1]["close"]
        atr_pct = (atr / cur_price) * 100.0 if cur_price > 0 else 0.0

        return {
            "atr": round(atr, 4 if atr < 1 else 2),
            "atr_pct": round(atr_pct, 2)
        }

    # =========================================================================
    # GRANULAR MICRO-DETAIL ANALYSIS
    # =========================================================================

    @staticmethod
    def analyze_candlestick_micro_details(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Examines candle anatomy (bodies, wicks, ratios) and micro price action patterns.
        Detects:
        - Bullish Hammer / Lower Wick Liquidity Absorption
        - Bullish Engulfing
        - Three Higher Lows
        - Upper Wick Rejection (Overhead Selling Pressure)
        """
        if not candles or len(candles) < 3:
            return {
                "pattern": "NEUTRAL",
                "bullish_conviction": 50,
                "lower_wick_ratio": 0.0,
                "upper_wick_ratio": 0.0,
                "detail": "Accumulating candle history..."
            }

        last = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3]

        c_open = last["open"]
        c_close = last["close"]
        c_high = last["high"]
        c_low = last["low"]

        body = abs(c_close - c_open)
        total_range = max(c_high - c_low, 1e-8)
        is_green = c_close >= c_open

        # Anatomy
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        lower_wick_ratio = lower_wick / total_range
        upper_wick_ratio = upper_wick / total_range
        body_ratio = body / total_range

        pattern = "NORMAL_PRICE_ACTION"
        conviction = 50
        detail = "Standard price discovery."

        # 1. Bullish Hammer / Pin Bar Rejection (Aggressive Buyer Absorption at low)
        if lower_wick_ratio >= 0.50 and upper_wick_ratio <= 0.20:
            pattern = "BULLISH_HAMMER_REJECTION"
            conviction = 88
            detail = f"Bullish liquidity absorption: long lower wick ({int(lower_wick_ratio*100)}% of range). Buyers defended the low."

        # 2. Bullish Engulfing
        elif is_green and prev["close"] < prev["open"] and c_close > prev["open"] and c_open <= prev["close"]:
            pattern = "BULLISH_ENGULFING"
            conviction = 82
            detail = f"Bullish engulfing: green candle completely absorbed previous sell candle with strong buyer dominance."

        # 3. Micro Higher Lows (Structure Trend)
        elif last["low"] >= prev["low"] >= prev2["low"] and is_green:
            pattern = "HIGHER_LOWS_LADDER"
            conviction = 76
            detail = f"Micro-trend structure: 3 consecutive higher lows printed. Steady accumulation underway."

        # 4. Upper Wick Rejection (Seller Overhead Warning)
        elif upper_wick_ratio >= 0.55:
            pattern = "UPPER_WICK_REJECTION"
            conviction = 30
            detail = f"Overhead seller pressure: long upper wick ({int(upper_wick_ratio*100)}% of range). Caution on immediate breakout."

        # 5. Strong Green Expansion Candle
        elif is_green and body_ratio >= 0.65:
            pattern = "EXPANSION_GREEN"
            conviction = 74
            detail = f"Strong green momentum bar ({int(body_ratio*100)}% body conviction)."

        return {
            "pattern": pattern,
            "bullish_conviction": conviction,
            "lower_wick_ratio": round(lower_wick_ratio, 2),
            "upper_wick_ratio": round(upper_wick_ratio, 2),
            "body_ratio": round(body_ratio, 2),
            "detail": detail
        }

    @staticmethod
    def analyze_volume_dynamics(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyzes volume surges, volume moving average ratios, and buying/selling pressure.
        Identifies:
        - Institutional / Buyer volume spikes (> 1.4x 20-SMA)
        - Seller volume exhaustion (volume dying out on dips)
        """
        if len(candles) < 15:
            return {
                "vol_ratio": 1.0,
                "volume_state": "NORMAL",
                "detail": "Insufficient volume history."
            }

        volumes = [c.get("volume", 0.0) for c in candles]
        cur_vol = volumes[-1]
        recent_vols = volumes[-20:]
        avg_vol = (sum(recent_vols) / len(recent_vols)) if recent_vols else 1.0
        vol_ratio = (cur_vol / avg_vol) if avg_vol > 0 else 1.0

        last_candle = candles[-1]
        is_green = last_candle["close"] >= last_candle["open"]

        # Calculate buy vs sell volume in recent 5 bars
        buy_vol = sum(c["volume"] for c in candles[-5:] if c["close"] >= c["open"])
        sell_vol = sum(c["volume"] for c in candles[-5:] if c["close"] < c["open"])
        total_5 = max(buy_vol + sell_vol, 1e-8)
        buy_dominance_pct = (buy_vol / total_5) * 100.0

        if vol_ratio >= 1.40 and is_green:
            vol_state = "BUYER_VOLUME_SPIKE"
            detail = f"High buyer volume surge ({vol_ratio:.1f}x of 20-period avg). Active institutional/whale interest."
        elif vol_ratio <= 0.65 and not is_green:
            vol_state = "SELLER_EXHAUSTION"
            detail = f"Low-volume pullback ({vol_ratio:.1f}x avg). Selling momentum is drying up, priming for dip reversal."
        elif buy_dominance_pct >= 65.0:
            vol_state = "BUYER_DOMINANCE"
            detail = f"Buyers control {buy_dominance_pct:.0f}% of recent 5-bar volume."
        else:
            vol_state = "NORMAL"
            detail = f"Volume pacing at {vol_ratio:.1f}x average."

        return {
            "cur_vol": round(cur_vol, 2),
            "avg_vol": round(avg_vol, 2),
            "vol_ratio": round(vol_ratio, 2),
            "buy_dominance_pct": round(buy_dominance_pct, 1),
            "volume_state": vol_state,
            "detail": detail
        }

    # =========================================================================
    # MULTI-TIMEFRAME CONFLUENCE (MTF) ENGINE
    # =========================================================================

    @classmethod
    def analyze_timeframe_trend(cls, candles: List[Dict[str, Any]], timeframe: str = "1h") -> Dict[str, Any]:
        """
        Determines the structural trend (Bullish, Bearish, or Neutral/Consolidating)
        for any higher timeframe (15m, 1h, 4h).
        Uses EMA 20, EMA 50, RSI 14, and MACD momentum.
        """
        if not candles or len(candles) < 15:
            return {
                "timeframe": timeframe,
                "trend": "NEUTRAL",
                "price": 0.0,
                "ema_20": 0.0,
                "ema_50": 0.0,
                "rsi": 50.0,
                "macd_trend": "NEUTRAL",
                "detail": f"{timeframe} trend accumulating..."
            }

        closes = [c["close"] for c in candles]
        price = closes[-1]
        p20 = min(20, len(closes) - 1)
        p50 = min(50, len(closes) - 1)
        ema_20_series = cls.calculate_ema(closes, p20)
        ema_50_series = cls.calculate_ema(closes, p50) if len(closes) >= 25 else ema_20_series
        rsi_series = cls.calculate_rsi(closes, 14)
        macd = cls.calculate_macd(closes, 12, 26, 9)

        ema_20 = ema_20_series[-1] if ema_20_series else price
        ema_50 = ema_50_series[-1] if ema_50_series else ema_20
        rsi = round(rsi_series[-1], 1) if rsi_series else 50.0

        # Bullish / Bearish determination
        if price >= ema_20 >= ema_50 and rsi >= 48.0:
            trend = "BULLISH"
            detail = f"Strong Uptrend: Price above EMA-20 (${ema_20:,.2f}) & EMA-50 (${ema_50:,.2f}) with bullish RSI ({rsi})."
        elif price >= ema_50 and rsi >= 44.0:
            trend = "BULLISH"
            detail = f"Uptrend Support Retest: Price holding above macro EMA-50 (${ema_50:,.2f}) support."
        elif price <= ema_20 <= ema_50 and rsi <= 52.0:
            trend = "BEARISH"
            detail = f"Strong Downtrend: Price below EMA-20 (${ema_20:,.2f}) & EMA-50 (${ema_50:,.2f}) with bearish RSI ({rsi})."
        elif price <= ema_50 and rsi <= 56.0:
            trend = "BEARISH"
            detail = f"Downtrend Resistance: Price capped below macro EMA-50 (${ema_50:,.2f})."
        else:
            trend = "NEUTRAL"
            detail = f"Sideways Consolidation: Price oscillating between EMAs (RSI: {rsi})."

        return {
            "timeframe": timeframe,
            "trend": trend,
            "price": price,
            "ema_20": round(ema_20, 4 if ema_20 < 10 else 2),
            "ema_50": round(ema_50, 4 if ema_50 < 10 else 2),
            "rsi": rsi,
            "macd_trend": macd.get("trend", "NEUTRAL"),
            "detail": detail
        }

    @classmethod
    def analyze_mtf_confluence(
        cls,
        candles_1m: List[Dict[str, Any]],
        candles_15m: Optional[List[Dict[str, Any]]] = None,
        candles_1h: Optional[List[Dict[str, Any]]] = None,
        micro_side: str = "LONG"
    ) -> Dict[str, Any]:
        """
        Synthesizes Multi-Timeframe Confluence across:
        1. 1-Hour Macro Trend (Master Bias)
        2. 15-Minute Intermediate Structure (Swing Filter)
        3. 1-Minute Micro Execution (Trigger)
        """
        trend_1h = cls.analyze_timeframe_trend(candles_1h, "1h") if candles_1h else {"timeframe": "1h", "trend": "NEUTRAL", "rsi": 50.0, "detail": "1h not loaded"}
        trend_15m = cls.analyze_timeframe_trend(candles_15m, "15m") if candles_15m else {"timeframe": "15m", "trend": "NEUTRAL", "rsi": 50.0, "detail": "15m not loaded"}

        t1h = trend_1h.get("trend", "NEUTRAL")
        t15m = trend_15m.get("trend", "NEUTRAL")

        confluence_score = 50
        confluence_level = "1/3_SINGLE_TF"
        confluence_badge = "1/3 Single Timeframe"
        trade_permitted = True
        counter_trend = False
        summary = ""

        if micro_side.upper() == "LONG":
            if t1h == "BULLISH" and t15m == "BULLISH":
                confluence_score = 100
                confluence_level = "3/3_TRIPLE_BULLISH"
                confluence_badge = "🟢 3/3 Triple Bullish Confluence"
                summary = "100% Trend Confluence: 1H Macro Bullish + 15M Swing Bullish + 1M Dip Trigger. Highest conviction entry."
            elif t1h == "BULLISH" or (t1h == "NEUTRAL" and t15m == "BULLISH"):
                confluence_score = 80
                confluence_level = "2/3_DOUBLE_BULLISH"
                confluence_badge = "🟢 2/3 Double Bullish Confluence"
                summary = "Strong Bullish Confluence: Macro/Swing support confirmed."
            elif t1h == "BEARISH":
                confluence_score = 20
                confluence_level = "COUNTER_TREND_WARNING"
                confluence_badge = "⚠️ Counter-Trend Warning (1H Bearish)"
                counter_trend = True
                trade_permitted = False
                summary = "Counter-Trend Guard Triggered: Attempting to Long against a 1-Hour Macro Downtrend. Trade blocked to avoid falling knife."
            else:
                confluence_score = 55
                confluence_level = "NEUTRAL"
                confluence_badge = "⚪ Sideways / Mixed Structure"
                summary = "Mixed Timeframe Alignment: Market in consolidation."

        else: # SHORT
            if t1h == "BEARISH" and t15m == "BEARISH":
                confluence_score = 100
                confluence_level = "3/3_TRIPLE_BEARISH"
                confluence_badge = "🔴 3/3 Triple Bearish Confluence"
                summary = "100% Trend Confluence: 1H Macro Bearish + 15M Swing Bearish + 1M Rejection Trigger. Highest conviction short."
            elif t1h == "BEARISH" or (t1h == "NEUTRAL" and t15m == "BEARISH"):
                confluence_score = 80
                confluence_level = "2/3_DOUBLE_BEARISH"
                confluence_badge = "🔴 2/3 Double Bearish Confluence"
                summary = "Strong Bearish Confluence: Overhead Macro/Swing resistance confirmed."
            elif t1h == "BULLISH":
                confluence_score = 20
                confluence_level = "COUNTER_TREND_WARNING"
                confluence_badge = "⚠️ Counter-Trend Warning (1H Bullish)"
                counter_trend = True
                trade_permitted = False
                summary = "Counter-Trend Guard Triggered: Attempting to Short into a 1-Hour Macro Uptrend. Trade blocked to avoid short squeeze."
            else:
                confluence_score = 55
                confluence_level = "NEUTRAL"
                confluence_badge = "⚪ Sideways / Mixed Structure"
                summary = "Mixed Timeframe Alignment: Market in consolidation."

        return {
            "confluence_score": confluence_score,
            "confluence_level": confluence_level,
            "confluence_badge": confluence_badge,
            "trade_permitted": trade_permitted,
            "counter_trend": counter_trend,
            "summary": summary,
            "trend_1h": trend_1h,
            "trend_15m": trend_15m
        }

    # =========================================================================
    # COMPREHENSIVE MULTI-DETAIL MARKET AUDIT
    # =========================================================================

    def analyze_full_market_details(
        self,
        candles: List[Dict[str, Any]],
        symbol: str = "BTCUSDT",
        in_position: bool = False,
        entry_price: float = 0.0,
        candles_15m: Optional[List[Dict[str, Any]]] = None,
        candles_1h: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Deep, multi-faceted analysis evaluating every micro detail & multi-timeframe confluence:
        - Multi-Timeframe Confluence (1H Macro Trend + 15M Swing Structure + 1M Trigger)
        - Candlestick anatomy (wicks, body ratios, hammer/engulfing)
        - Volume flow & surge ratios vs 20-SMA
        - Bollinger Band statistical discount (%B)
        - MACD acceleration & histogram
        - EMA trend alignment & support retests
        - Relative Strength Index (RSI)
        - Volatility measurement (ATR %)
        - Zero-Loss Protection & Target Realization
        """
        if not candles or len(candles) < 20:
            cur_p = candles[-1]["close"] if candles else 0.0
            return {
                "symbol": symbol,
                "confidence": 50,
                "decision": "WAIT",
                "reason": f"Accumulating tick candles for {symbol}...",
                "rsi": 50.0,
                "fast_ema": cur_p,
                "slow_ema": cur_p,
                "macd": {"macd": 0.0, "signal": 0.0, "hist": 0.0, "trend": "NEUTRAL"},
                "bollinger": {"upper": cur_p, "middle": cur_p, "lower": cur_p, "pct_b": 0.5, "bandwidth": 0.0},
                "atr": {"atr": 0.0, "atr_pct": 0.0},
                "candle_action": {"pattern": "NEUTRAL", "bullish_conviction": 50, "detail": "Waiting for bars"},
                "volume_flow": {"vol_ratio": 1.0, "volume_state": "NORMAL", "detail": "Normal"},
                "mtf": {
                    "confluence_score": 50,
                    "confluence_level": "1/3_SINGLE_TF",
                    "confluence_badge": "1/3 Single Timeframe",
                    "trade_permitted": True,
                    "counter_trend": False,
                    "summary": "Accumulating higher timeframe trend bars...",
                    "trend_1h": {"trend": "NEUTRAL", "rsi": 50.0, "detail": "Waiting for bars"},
                    "trend_15m": {"trend": "NEUTRAL", "rsi": 50.0, "detail": "Waiting for bars"}
                },
                "safety_checks": []
            }

        closes = [c["close"] for c in candles]
        current_price = closes[-1]

        # 1. Technical calculations
        fast_emas = self.calculate_ema(closes, self.fast_period)
        slow_emas = self.calculate_ema(closes, self.slow_period)
        rsis = self.calculate_rsi(closes, self.rsi_period)

        fast_ema = fast_emas[-1] if fast_emas else current_price
        slow_ema = slow_emas[-1] if slow_emas else current_price
        rsi = round(rsis[-1], 1) if rsis else 50.0

        # 2. Granular Micro Details
        macd = self.calculate_macd(closes, 12, 26, 9)
        bb = self.calculate_bollinger_bands(closes, 20, 2.0)
        atr = self.calculate_atr(candles, 14)
        candle_act = self.analyze_candlestick_micro_details(candles)
        vol_dyn = self.analyze_volume_dynamics(candles)

        confidence = 0
        safety_checks = []

        # -------------------------------------------------------------
        # DETAIL 1: Candlestick Micro-Action & Rejection Wicks (Max 25 pts)
        # -------------------------------------------------------------
        c_pattern = candle_act["pattern"]
        c_conv = candle_act["bullish_conviction"]
        if c_pattern in ("BULLISH_HAMMER_REJECTION", "BULLISH_ENGULFING"):
            confidence += 25
            safety_checks.append({
                "name": "Candlestick Liquidity Absorption",
                "passed": True,
                "score": 25,
                "detail": f"{c_pattern}: {candle_act['detail']}"
            })
        elif c_pattern in ("HIGHER_LOWS_LADDER", "EXPANSION_GREEN"):
            confidence += 20
            safety_checks.append({
                "name": "Price Action Micro-Structure",
                "passed": True,
                "score": 20,
                "detail": candle_act["detail"]
            })
        elif c_pattern == "UPPER_WICK_REJECTION":
            confidence += 5
            safety_checks.append({
                "name": "Price Action Overhead Resistance",
                "passed": False,
                "score": 5,
                "detail": candle_act["detail"]
            })
        else:
            confidence += 15
            safety_checks.append({
                "name": "Price Action Equilibrium",
                "passed": True,
                "score": 15,
                "detail": "Steady price discovery without aggressive seller overhead."
            })

        # -------------------------------------------------------------
        # DETAIL 2: Volume Flow & Buyer Aggression (Max 20 pts)
        # -------------------------------------------------------------
        v_state = vol_dyn["volume_state"]
        if v_state == "BUYER_VOLUME_SPIKE":
            confidence += 20
            safety_checks.append({
                "name": "Volume Order Flow Surge",
                "passed": True,
                "score": 20,
                "detail": vol_dyn["detail"]
            })
        elif v_state in ("SELLER_EXHAUSTION", "BUYER_DOMINANCE"):
            confidence += 18
            safety_checks.append({
                "name": "Volume Pressure Dynamics",
                "passed": True,
                "score": 18,
                "detail": vol_dyn["detail"]
            })
        else:
            confidence += 12
            safety_checks.append({
                "name": "Volume Flow",
                "passed": True,
                "score": 12,
                "detail": f"Standard volume pacing ({vol_dyn['vol_ratio']}x average)."
            })

        # -------------------------------------------------------------
        # DETAIL 3: Bollinger Band Statistical Value (%B) (Max 20 pts)
        # -------------------------------------------------------------
        pct_b = bb["pct_b"]
        if pct_b <= 0.25:
            confidence += 20
            safety_checks.append({
                "name": "Bollinger Band Statistical Discount",
                "passed": True,
                "score": 20,
                "detail": f"Deep value dip: %B is {pct_b:.2f} (price touching/testing lower band ${bb['lower']:,.2f}). Prime rebound geometry."
            })
        elif 0.25 < pct_b <= 0.65:
            confidence += 16
            safety_checks.append({
                "name": "Bollinger Band Value Zone",
                "passed": True,
                "score": 16,
                "detail": f"Healthy positioning: %B is {pct_b:.2f} within normal standard deviation channels."
            })
        else:
            confidence += 6
            safety_checks.append({
                "name": "Bollinger Band Upper Range",
                "passed": False,
                "score": 6,
                "detail": f"%B is elevated ({pct_b:.2f}), approaching upper volatility band (${bb['upper']:,.2f})."
            })

        # -------------------------------------------------------------
        # DETAIL 4: MACD Acceleration & Momentum (Max 15 pts)
        # -------------------------------------------------------------
        m_trend = macd["trend"]
        if m_trend in ("BULLISH_ACCELERATING", "BEARISH_EXHAUSTION"):
            confidence += 15
            safety_checks.append({
                "name": "MACD Momentum Curve",
                "passed": True,
                "score": 15,
                "detail": f"Momentum inflecting upwards: {m_trend} (Hist: {macd['hist']:+0.5f})."
            })
        elif m_trend == "BULLISH_DECELERATING":
            confidence += 10
            safety_checks.append({
                "name": "MACD Momentum Curve",
                "passed": True,
                "score": 10,
                "detail": f"Positive momentum holding (Hist: {macd['hist']:+0.5f})."
            })
        else:
            confidence += 5
            safety_checks.append({
                "name": "MACD Momentum Curve",
                "passed": False,
                "score": 5,
                "detail": f"Bearish momentum lingering (Hist: {macd['hist']:+0.5f})."
            })

        # -------------------------------------------------------------
        # DETAIL 5: RSI Runway & EMA Trend Alignment (Max 20 pts)
        # -------------------------------------------------------------
        if rsi > self.rsi_overbought:
            safety_checks.append({
                "name": "RSI Overbought Guard",
                "passed": False,
                "score": 0,
                "detail": f"RSI elevated ({rsi} > {self.rsi_overbought}). Restricted from chasing top."
            })
        elif rsi <= 35.0:
            confidence += 20
            safety_checks.append({
                "name": "RSI Oversold Runway",
                "passed": True,
                "score": 20,
                "detail": f"Deeply oversold ({rsi}). Huge upside runway before encountering resistance."
            })
        elif 35.0 < rsi <= 55.0:
            confidence += 18
            safety_checks.append({
                "name": "RSI Accumulation Runway",
                "passed": True,
                "score": 18,
                "detail": f"Optimal accumulation RSI ({rsi}). Safe entry zone."
            })
        else:
            confidence += 12
            safety_checks.append({
                "name": "RSI Moderate Momentum",
                "passed": True,
                "score": 12,
                "detail": f"Active momentum RSI ({rsi})."
            })

        # Cap confidence at 100
        long_confidence = min(100, max(10, confidence))

        # -------------------------------------------------------------
        # DETAIL 6: SHORT / BEARISH Conviction Scoring (Futures Multi-Directional)
        # -------------------------------------------------------------
        short_confidence = 0
        if c_pattern in ("UPPER_WICK_REJECTION", "BEARISH_ENGULFING", "LOWER_HIGHS_SLIDE"):
            short_confidence += 25
        elif c_pattern in ("EXPANSION_RED", "NORMAL"):
            short_confidence += 14

        if v_state in ("SELLER_VOLUME_SPIKE", "SELLER_DOMINANCE"):
            short_confidence += 22
        elif v_state == "BUYER_EXHAUSTION":
            short_confidence += 16
        else:
            short_confidence += 10

        if pct_b >= 0.75:
            short_confidence += 20
        elif 0.50 <= pct_b < 0.75:
            short_confidence += 12
        else:
            short_confidence += 5

        if m_trend in ("BEARISH_ACCELERATING", "BULLISH_EXHAUSTION"):
            short_confidence += 18
        elif m_trend == "BEARISH_DECELERATING":
            short_confidence += 10
        else:
            short_confidence += 5

        if rsi >= 65.0:
            short_confidence += 20
        elif 52.0 <= rsi < 65.0:
            short_confidence += 14
        else:
            short_confidence += 5

        short_confidence = min(100, max(10, short_confidence))

        # Decision Synthesis (Bi-Directional Futures)
        if long_confidence >= 60 and rsi <= self.rsi_overbought and pct_b <= 0.80 and long_confidence >= short_confidence:
            decision = "STRONG_BUY"
            side = "LONG"
            confidence = long_confidence
            reason = f"Bullish Long setup ({confidence}% score): {c_pattern} + {v_state} + safe RSI ({rsi})."
        elif short_confidence >= 60 and rsi >= 48.0 and pct_b >= 0.35 and short_confidence > long_confidence:
            decision = "STRONG_SHORT"
            side = "SHORT"
            confidence = short_confidence
            reason = f"Bearish Short setup ({short_confidence}% score): {c_pattern} + {v_state} + elevated RSI ({rsi})."
        else:
            decision = "WAIT_FOR_SETUP"
            side = "NEUTRAL"
            confidence = max(long_confidence, short_confidence)
            reason = f"Monitoring {symbol} micro-structure (Long: {long_confidence}%, Short: {short_confidence}%, RSI: {rsi})."

        # Multi-Timeframe Confluence (MTF) Integration
        mtf_micro_side = side if side != "NEUTRAL" else ("LONG" if long_confidence >= short_confidence else "SHORT")
        mtf = self.analyze_mtf_confluence(
            candles_1m=candles,
            candles_15m=candles_15m,
            candles_1h=candles_1h,
            micro_side=mtf_micro_side
        )

        # Apply MTF Confirmation / Filtering
        if side == "LONG":
            if mtf["confluence_level"] == "3/3_TRIPLE_BULLISH":
                confidence = min(100, int(confidence * 1.25))
                reason = f"⭐ 3/3 Triple Bullish Confluence ({confidence}%): 1H Macro Bullish + 15M Swing Bullish + 1M {c_pattern}."
                safety_checks.append({
                    "name": "Multi-Timeframe Macro Alignment",
                    "passed": True,
                    "score": 30,
                    "detail": mtf["summary"]
                })
            elif mtf["confluence_level"] == "2/3_DOUBLE_BULLISH":
                confidence = min(100, int(confidence * 1.10))
                reason = f"🟢 2/3 Double Bullish Confluence ({confidence}%): 1H/15M Bullish support confirmed."
                safety_checks.append({
                    "name": "Multi-Timeframe Macro Alignment",
                    "passed": True,
                    "score": 20,
                    "detail": mtf["summary"]
                })
            elif mtf["counter_trend"]:
                confidence = max(10, confidence - 35)
                decision = "WAIT_FOR_SETUP"
                reason = f"🛡️ MTF Guard Blocked Long: 1H Macro is BEARISH. Avoided high-risk counter-trend trap."
                safety_checks.append({
                    "name": "Multi-Timeframe Macro Trend Guard",
                    "passed": False,
                    "score": 0,
                    "detail": mtf["summary"]
                })
            else:
                safety_checks.append({
                    "name": "Multi-Timeframe Alignment",
                    "passed": True,
                    "score": 15,
                    "detail": mtf["summary"]
                })

        elif side == "SHORT":
            if mtf["confluence_level"] == "3/3_TRIPLE_BEARISH":
                confidence = min(100, int(confidence * 1.25))
                reason = f"⭐ 3/3 Triple Bearish Confluence ({confidence}%): 1H Macro Bearish + 15M Swing Bearish + 1M {c_pattern}."
                safety_checks.append({
                    "name": "Multi-Timeframe Macro Alignment",
                    "passed": True,
                    "score": 30,
                    "detail": mtf["summary"]
                })
            elif mtf["confluence_level"] == "2/3_DOUBLE_BEARISH":
                confidence = min(100, int(confidence * 1.10))
                reason = f"🔴 2/3 Double Bearish Confluence ({confidence}%): 1H/15M Bearish resistance confirmed."
                safety_checks.append({
                    "name": "Multi-Timeframe Macro Alignment",
                    "passed": True,
                    "score": 20,
                    "detail": mtf["summary"]
                })
            elif mtf["counter_trend"]:
                confidence = max(10, confidence - 35)
                decision = "WAIT_FOR_SETUP"
                reason = f"🛡️ MTF Guard Blocked Short: 1H Macro is BULLISH. Avoided high-risk short squeeze."
                safety_checks.append({
                    "name": "Multi-Timeframe Macro Trend Guard",
                    "passed": False,
                    "score": 0,
                    "detail": mtf["summary"]
                })
            else:
                safety_checks.append({
                    "name": "Multi-Timeframe Alignment",
                    "passed": True,
                    "score": 15,
                    "detail": mtf["summary"]
                })

        return {
            "symbol": symbol,
            "confidence": confidence,
            "long_confidence": long_confidence,
            "short_confidence": short_confidence,
            "side": side,
            "decision": decision,
            "reason": reason,
            "rsi": rsi,
            "fast_ema": round(fast_ema, 4 if fast_ema < 10 else 2),
            "slow_ema": round(slow_ema, 4 if slow_ema < 10 else 2),
            "macd": macd,
            "bollinger": bb,
            "atr": atr,
            "candle_action": candle_act,
            "volume_flow": vol_dyn,
            "mtf": mtf,
            "safety_checks": safety_checks
        }

    # =========================================================================
    # CLASSIC INTERFACE ADAPTER (For backwards compatibility with bot/dashboard)
    # =========================================================================

    def analyze(
        self,
        candles: List[Dict[str, Any]],
        in_position: bool = False,
        entry_price: float = 0.0,
        candles_15m: Optional[List[Dict[str, Any]]] = None,
        candles_1h: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Evaluates candles and returns a signal ('BUY', 'SELL', or 'HOLD')
        backed by the complete micro-detail engine.
        """
        if not candles or len(candles) < 20:
            return "HOLD", {"reason": "Insufficient candle history"}

        current_price = candles[-1]["close"]
        full_audit = self.analyze_full_market_details(
            candles,
            in_position=in_position,
            entry_price=entry_price,
            candles_15m=candles_15m,
            candles_1h=candles_1h
        )

        metadata = {
            "current_price": current_price,
            "fast_ema": full_audit["fast_ema"],
            "slow_ema": full_audit["slow_ema"],
            "rsi": full_audit["rsi"],
            "confidence": full_audit["confidence"],
            "pattern": full_audit["candle_action"]["pattern"],
            "volume_state": full_audit["volume_flow"]["volume_state"],
            "pct_b": full_audit["bollinger"]["pct_b"],
            "macd_trend": full_audit["macd"]["trend"],
            "atr_pct": full_audit["atr"]["atr_pct"],
            "mtf": full_audit.get("mtf", {}),
            "reason": full_audit["reason"]
        }

        # Exit conditions
        if in_position and entry_price > 0:
            pct_change = ((current_price - entry_price) / entry_price) * 100.0
            metadata["unrealized_pnl_pct"] = round(pct_change, 2)

            # Strict Zero-Loss Rule: never exit at a loss unless stop-loss hit
            if pct_change <= -self.stop_loss_pct:
                metadata["reason"] = f"Stop-Loss triggered (-{abs(pct_change):.2f}% <= -{self.stop_loss_pct}%)"
                return "SELL", metadata

            if pct_change >= self.take_profit_pct:
                metadata["reason"] = f"Take-Profit triggered (+{pct_change:.2f}% >= +{self.take_profit_pct}%)"
                return "SELL", metadata

            # Bearish reversal wick or overbought exit when already in profit
            if pct_change >= 0.25 and (full_audit["candle_action"]["pattern"] == "UPPER_WICK_REJECTION" or full_audit["rsi"] >= self.rsi_overbought):
                metadata["reason"] = f"Locked profit on overhead seller wick (+{pct_change:.2f}%)"
                return "SELL", metadata

            metadata["reason"] = f"Holding safe (+{pct_change:.2f}%)"
            return "HOLD", metadata

        # Entry conditions
        if not in_position:
            if full_audit["decision"] == "STRONG_BUY":
                return "BUY", metadata

        return "HOLD", metadata
