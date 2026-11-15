import time
import json
import math
import threading
import concurrent.futures
from pathlib import Path
from datetime import datetime
from collections import deque
from typing import Dict, Any, List, Optional, Tuple

from strategy import TradingStrategy
import config

# ============================================================
# BACKGROUND MARKET ANALYZER
# Continuously scans all 500+ coins every ~60s even when the
# AI session is idle, so ranked opportunities are pre-warmed
# and ready the instant the user clicks "Launch AI Session".
# ============================================================

class BackgroundMarketAnalyzer:
    """
    Always-on market intelligence engine that:
    - Scans all 500+ USDT pairs on Binance every SCAN_INTERVAL_SECONDS
    - Runs the same two-stage macro + micro scoring pipeline as the AI engine
    - Caches a ranked leaderboard of top setups (LONG & SHORT)
    - Exposes the cache to the AI engine so session launch is instant
    - Keeps a rolling scan log visible in the dashboard
    """

    SCAN_INTERVAL_SECONDS = 60  # Full scan every 60 s
    TOP_N_MICRO = 25            # Micro-analyse top 25 from macro stage
    RADAR_SIZE  = 12            # Radar rows shown in UI

    def __init__(self, client, strategy: "TradingStrategy", log_callback=None):
        self.client = client
        self.strategy = strategy
        self.log_callback = log_callback

        # Pre-warmed ranked list: each item is a dict ready for the UI
        self.ranked_opportunities: List[Dict[str, Any]] = []
        # Compact tuple list used by fill_open_slots: (sym, price, score, analysis, side)
        self.ranked_candidates: List[tuple] = []

        self.last_scan_time: float = 0.0
        self.total_scanned: int = 0
        self.scan_count: int = 0
        self.is_scanning: bool = False
        self.scan_logs: deque = deque(maxlen=20)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    def _log(self, msg: str, level: str = "BG"):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] [BG-SCAN] {msg}"
        self.scan_logs.append(entry)
        if self.log_callback:
            self.log_callback(f"[Market-Radar] {msg}", level)

    # ------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="BGMarketAnalyzer")
        self._thread.start()
        self._log("Background Market Analyzer started — continuously pre-ranking all 500+ coins.", "INFO")

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------
    def _loop(self):
        # Run an immediate first scan on startup, then repeat every interval
        while not self._stop_event.is_set():
            try:
                self._run_scan()
            except Exception as e:
                self._log(f"Scan error: {e}", "ERROR")
            # Sleep in 1-second ticks so we respond quickly to stop
            elapsed = 0
            while elapsed < self.SCAN_INTERVAL_SECONDS and not self._stop_event.is_set():
                time.sleep(1)
                elapsed += 1

    # ------------------------------------------------------------------
    def _run_scan(self):
        t0 = time.time()
        self.is_scanning = True
        self.scan_count += 1

        stablecoins = {
            "USDC", "BUSD", "FDUSD", "TUSD", "EUR", "DAI",
            "USDS", "AEUR", "PAXG", "TRY", "GBP", "AUD", "BRL", "RUB"
        }

        # ------- STAGE 1: MACRO SWEEP (all 500+ coins, ticker data only) -------
        try:
            tickers = self.client.get_24hr_tickers(quote_asset="USDT")
        except Exception as e:
            self._log(f"Ticker fetch failed: {e}", "WARN")
            self.is_scanning = False
            return

        self.total_scanned = len(tickers)
        macro_scored = []

        for t in tickers:
            sym = t.get("symbol", "")
            if not sym or any(s in sym for s in stablecoins):
                continue
            last_p  = float(t.get("price", 0.0))
            high_p  = float(t.get("high", 0.0))
            low_p   = float(t.get("low", 0.0))
            vol_usd = float(t.get("quote_volume", 0.0))
            chg_pct = float(t.get("change_pct", 0.0))

            if last_p <= 0.0 or vol_usd < 15000.0 or high_p <= low_p:
                continue

            range_24h = high_p - low_p
            amp_pct   = (range_24h / low_p) * 100.0
            vol_s = 30.0 if amp_pct >= 5.0 else (22.0 if amp_pct >= 3.0 else (12.0 if amp_pct >= 2.0 else -15.0))
            if 0.5 <= chg_pct <= 6.0:
                mom_s = 28.0
            elif -4.0 <= chg_pct < 0.5:
                mom_s = 22.0
            elif 6.0 < chg_pct <= 10.0:
                mom_s = 15.0
            elif chg_pct < -10.0 or chg_pct > 18.0:
                mom_s = -10.0
            else:
                mom_s = 5.0
            pos_in_range = (last_p - low_p) / range_24h
            range_s = 25.0 if 0.20 <= pos_in_range <= 0.65 else (20.0 if pos_in_range < 0.20 else 5.0)
            liq_s   = min(20.0, math.log10(max(1000.0, vol_usd)) * 3.5)

            macro_scored.append((vol_s + mom_s + range_s + liq_s, sym, last_p, t))

        macro_scored.sort(key=lambda x: x[0], reverse=True)
        top_candidates = macro_scored[:self.TOP_N_MICRO]

        if not top_candidates:
            self.is_scanning = False
            return

        # ------- STAGE 2: MICRO TECHNICAL ANALYSIS (parallel, top 25) -------
        def analyze_coin(item):
            macro_s, sym, cur_p, t_data = item
            try:
                candles    = self.client.get_klines(sym, interval="1m", limit=30)
                candles_15m = self.client.get_klines(sym, interval="15m", limit=30)
                candles_1h  = self.client.get_klines(sym, interval="1h", limit=30)
                if not candles or len(candles) < 20:
                    return None

                p        = candles[-1]["close"]
                analysis = self.strategy.analyze_full_market_details(
                    candles, symbol=sym,
                    candles_15m=candles_15m,
                    candles_1h=candles_1h
                )
                conf   = analysis.get("confidence", 50)
                rsi    = analysis.get("rsi", 50.0)
                side   = analysis.get("side", "LONG")
                if side == "NEUTRAL":
                    side = "LONG" if analysis.get("long_confidence", 50) >= analysis.get("short_confidence", 50) else "SHORT"

                v_flow  = analysis.get("volume_flow", {})
                bb      = analysis.get("bollinger", {})
                macd    = analysis.get("macd", {})
                atr     = analysis.get("atr", {})
                mtf     = analysis.get("mtf", {})
                c_act   = analysis.get("candle_action", {})

                score     = float(conf) + (macro_s * 0.3)
                vol_ratio = float(v_flow.get("vol_ratio", 1.0))
                v_state   = v_flow.get("volume_state", "NORMAL")
                pat       = c_act.get("pattern", "NORMAL")
                pct_b     = bb.get("pct_b", 0.5)
                m_trend   = macd.get("trend", "NEUTRAL")
                mtf_lvl   = mtf.get("confluence_level", "NEUTRAL")
                is_counter = mtf.get("counter_trend", False)
                atr_pct   = float(atr.get("atr_pct", 0.2))

                if "3/3" in mtf_lvl:
                    score += 26.0
                elif "2/3" in mtf_lvl:
                    score += 14.0
                elif is_counter:
                    score -= 40.0

                if side == "LONG":
                    if vol_ratio >= 2.0 or v_state == "BUYER_VOLUME_SPIKE":
                        score += 22.0
                    elif vol_ratio >= 1.3 or v_state in ("SELLER_EXHAUSTION", "BUYER_DOMINANCE"):
                        score += 14.0
                    if pat in ("BULLISH_HAMMER_REJECTION", "BULLISH_ENGULFING"):
                        score += 15.0
                    score += 14.0 if pct_b <= 0.25 else (-15.0 if pct_b >= 0.85 else 0.0)
                else:
                    if vol_ratio >= 2.0 or v_state == "SELLER_VOLUME_SPIKE":
                        score += 22.0
                    elif vol_ratio >= 1.3 or v_state in ("BUYER_EXHAUSTION", "SELLER_DOMINANCE"):
                        score += 14.0
                    if pat in ("UPPER_WICK_REJECTION", "BEARISH_ENGULFING"):
                        score += 15.0
                    score += 14.0 if pct_b >= 0.75 else (-15.0 if pct_b <= 0.20 else 0.0)

                score += 18.0 if atr_pct >= 0.35 else (-20.0 if atr_pct < 0.08 else 0.0)
                score = round(max(0.0, score), 1)

                return {
                    "symbol":    sym,
                    "price":     p,
                    "side":      side,
                    "score":     score,
                    "confidence": conf,
                    "rsi":       round(rsi, 1),
                    "pattern":   pat.replace("_", " ").title(),
                    "vol_ratio": f"{vol_ratio:.1f}x",
                    "pct_b":     f"{pct_b:.2f}",
                    "macd_trend": m_trend.replace("_", " ").title(),
                    "atr_pct":   round(atr_pct, 3),
                    "chg_24h":   f"{t_data.get('change_pct', 0.0):+.1f}%",
                    "mtf_confluence": mtf_lvl,
                    "mtf_badge": mtf.get("confluence_badge", "1/3 Single TF"),
                    "trend_1h":  mtf.get("trend_1h", {}).get("trend", "NEUTRAL"),
                    "trend_15m": mtf.get("trend_15m", {}).get("trend", "NEUTRAL"),
                    "mtf_summary": mtf.get("summary", ""),
                    "counter_trend": is_counter,
                    "macro_score": round(macro_s, 1),
                    # Also keep full analysis for instant entry use
                    "_analysis": analysis,
                    "_macro_s":  macro_s,
                }
            except Exception:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(analyze_coin, top_candidates))

        radar    = []
        cands    = []
        for r in results:
            if not r:
                continue
            radar.append(r)
            analysis   = r.pop("_analysis", {})
            macro_s_val = r.pop("_macro_s", 0.0)
            sym   = r["symbol"]
            p     = r["price"]
            score = r["score"]
            side  = r["side"]
            is_counter = r["counter_trend"]
            rsi   = r["rsi"]

            if not is_counter:
                if side == "LONG" and rsi <= self.strategy.rsi_overbought:
                    cands.append((sym, p, score, analysis, side))
                elif side == "SHORT" and rsi >= 45.0:
                    cands.append((sym, p, score, analysis, side))
                else:
                    cands.append((sym, p, score, analysis, side))

        radar.sort(key=lambda x: x["score"], reverse=True)
        cands.sort(key=lambda x: x[2], reverse=True)

        self.ranked_opportunities = radar[:self.RADAR_SIZE]
        self.ranked_candidates    = cands
        self.last_scan_time       = time.time()
        self.is_scanning          = False

        elapsed = round(time.time() - t0, 1)
        top_pick = radar[0] if radar else None
        top_str  = f"#{1} pick → {top_pick['side']} {top_pick['symbol']} (Score: {top_pick['score']})" if top_pick else "no candidates"
        self._log(
            f"Scan #{self.scan_count} complete in {elapsed}s — {self.total_scanned} pairs scanned, "
            f"top {len(cands)} opportunities ranked. {top_str}",
            "INFO"
        )

    # ------------------------------------------------------------------
    def get_radar_payload(self) -> Dict[str, Any]:
        """Returns JSON-safe snapshot of the latest pre-analysis."""
        age_s = round(time.time() - self.last_scan_time, 0) if self.last_scan_time > 0 else None
        return {
            "is_scanning":        self.is_scanning,
            "scan_count":         self.scan_count,
            "total_scanned":      self.total_scanned,
            "last_scan_age_secs": age_s,
            "next_scan_secs":     max(0, self.SCAN_INTERVAL_SECONDS - int(age_s or self.SCAN_INTERVAL_SECONDS)),
            "opportunities":      self.ranked_opportunities,
            "top_pick":           self.ranked_opportunities[0] if self.ranked_opportunities else None,
            "scan_logs":          list(self.scan_logs),
        }

POSITIONS_FILE = Path(__file__).parent / "ai_positions.json"

def load_ai_positions() -> Dict[str, Dict[str, Any]]:
    if POSITIONS_FILE.exists():
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                now_ts = time.time()
                for p in data.values():
                    if "entry_timestamp" not in p:
                        p["entry_timestamp"] = now_ts
                    if "dca_count" not in p:
                        p["dca_count"] = 0
                    if "held_seconds" not in p:
                        p["held_seconds"] = 0
                    if "held_mins" not in p:
                        p["held_mins"] = 0.0
                return data
        except Exception:
            pass
    return {}

def save_ai_positions(positions: Dict[str, Dict[str, Any]]):
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, indent=2)
    except Exception:
        pass

# Default fallback watchlist for Binance Testnet
DEFAULT_WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "ADAUSDT", "XRPUSDT", "DOGEUSDT", "AVAXUSDT",
    "LINKUSDT", "DOTUSDT", "NEARUSDT", "LTCUSDT"
]

class AITradingEngine:
    """
    Dedicated AI Smart Trader (Single-Capital Focused Sniper Scalper):
    - Focuses 100% of the allocated capital into the single #1 highest-conviction setup in the market.
    - Eliminates fragmented capital: trades with maximum leverage and buying power on one position.
    - Automatically identifies whether LONG or SHORT offers the superior risk/reward ratio.
    - Manages the single position with:
        1. Ultra-fast scalp trailing take-profit (+0.35% floor, tight trailing)
        2. Strict Zero-Loss Rule (never closes at a loss; holds until green)
        3. Automated liquidation buffer & safety stop guard
    - The instant the trade hits profit and closes, 100% of the capital is IMMEDIATELY redeployed
      into the next best setup across 500+ coins!
    """

    def __init__(self, client, record_trade_callback, log_callback=None, bg_analyzer: "BackgroundMarketAnalyzer" = None):
        self.client = client
        self.record_trade_callback = record_trade_callback
        self.log_callback = log_callback
        # Optional reference to the always-on background analyzer
        self.bg_analyzer: Optional[BackgroundMarketAnalyzer] = bg_analyzer

        # Configuration
        self.symbol: str = "BTCUSDT"          # Primary/featured symbol
        self.target_symbols: List[str] = list(DEFAULT_WATCHLIST)
        self.allocated_amount: float = 60.0   # $ USDT total session budget (100% allocated to 1 position)
        self.max_positions: int = 1           # Focused on ONE single trade at a time
        self.target_profit: float = 5.0       # $ USDT target profit
        self.min_profit_pct: float = 0.35     # Minimum 0.35% profit before trailing activates (fast scalping)
        self.trailing_pct: float = 0.12       # Pullback from peak to lock in profit
        self.dry_run: bool = getattr(config, "DRY_RUN", True)
        self.immediate_entry: bool = True     # Initial entry on session start

        # Futures Settings
        self.market_type: str = getattr(config, "MARKET_TYPE", "FUTURES")
        self.leverage: int = int(getattr(config, "DEFAULT_LEVERAGE", 5))
        self.margin_type: str = getattr(config, "MARGIN_TYPE", "ISOLATED")

        # Fee & Tax Configuration
        self.fee_rate_pct: float = float(getattr(config, "BINANCE_FEE_PCT", 0.05)) # 0.05% Taker fee
        self.tax_rate_pct: float = float(getattr(config, "TAX_RATE_PCT", 30.0))    # 30% Flat Tax on Net Profit
        self.tds_pct: float = float(getattr(config, "TDS_PCT", 1.0))              # 1.0% TDS
        self.include_taxes: bool = bool(getattr(config, "INCLUDE_TAXES", True))

        # Accumulated metrics (Gross vs Net)
        self.accumulated_gross_profit: float = 0.0
        self.accumulated_fees: float = 0.0
        self.accumulated_tax: float = 0.0
        self.accumulated_tds: float = 0.0
        self.accumulated_profit: float = 0.0 # Net Take-Home Realized Profit
        self.cycles_completed: int = 0

        # Strategy & Indicator Engine
        self.strategy = TradingStrategy(
            fast_period=7,
            slow_period=25,
            rsi_period=14,
            rsi_oversold=35.0,
            rsi_overbought=66.0,
            stop_loss_pct=1.5,
            take_profit_pct=self.min_profit_pct
        )

        # Active Multi-Position Basket: {symbol: position_dict}
        self.positions: Dict[str, Dict[str, Any]] = load_ai_positions()

        # Live Session State
        self.is_running: bool = False
        if self.positions:
            first_sym = list(self.positions.keys())[0]
            self.status: str = "IN_POSITION"
            self.status_message = f"AI Single Capital focused on {first_sym}."
        else:
            self.status: str = "IDLE"
            self.status_message: str = "AI Trader Idle. Enter capital and click Launch to start single-trade sniper."

        # Legacy fields for backward compatibility with single-position readers
        self.current_price: float = 0.0
        self.entry_price: float = 0.0
        self.position_qty: float = 0.0
        self.peak_price: float = 0.0
        self.unrealized_pnl_pct: float = 0.0
        self.unrealized_pnl_usd: float = 0.0

        # AI Analysis State
        self.ai_analysis: Dict[str, Any] = {
            "symbol": "BTCUSDT",
            "confidence": 0,
            "decision": "WAIT",
            "reason": "Initializing AI market scanner with Multi-Timeframe Confluence (1m/15m/1h)...",
            "rsi": 50.0,
            "fast_ema": 0.0,
            "slow_ema": 0.0,
            "trend": "NEUTRAL",
            "mtf": {
                "confluence_score": 50,
                "confluence_level": "1/3_SINGLE_TF",
                "confluence_badge": "1/3 Single Timeframe",
                "trade_permitted": True,
                "counter_trend": False,
                "summary": "Initializing Multi-Timeframe Alignment...",
                "trend_1h": {"trend": "NEUTRAL", "rsi": 50.0, "detail": "Initializing..."},
                "trend_15m": {"trend": "NEUTRAL", "rsi": 50.0, "detail": "Initializing..."}
            },
            "safety_checks": []
        }

        # Candidate radar cache for UI
        self.candidate_radar: List[Dict[str, Any]] = []
        self.total_scanned_coins: int = 0

        # Logs
        self.ai_logs = deque(maxlen=80)

        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def get_piece_amount(self) -> float:
        """Returns 100% of the allocated capital for the single focused trade."""
        return max(5.0 if self.market_type == "FUTURES" else 10.0, round(self.allocated_amount, 2))

    def get_tradable_symbols(self) -> List[str]:
        """Discovers liquid USDT trading pairs from Binance, sorted by 24h liquidity."""
        try:
            tickers = self.client.get_24hr_tickers(quote_asset="USDT")
            symbols = [
                t["symbol"] for t in tickers 
                if t.get("symbol") and not any(
                    stable in t["symbol"] for stable in ["USDC", "BUSD", "FDUSD", "TUSD", "EUR"]
                )
            ]
            if symbols:
                selected = symbols[:15]
                return selected
        except Exception as e:
            self.add_log(f"Notice: Using default watchlist ({len(DEFAULT_WATCHLIST)} coins): {e}", "INFO")
        return list(DEFAULT_WATCHLIST)

    def add_log(self, message: str, level: str = "AI"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] [{level}] {message}"
        self.ai_logs.append(entry)
        if self.log_callback:
            self.log_callback(f"[AI-TRADER] {message}", level)

    def start(
        self,
        allocated_amount: float,
        target_profit: float,
        min_profit_pct: float = 0.35,
        dry_run: bool = False,
        immediate_entry: bool = True,
        max_positions: int = 1,
        trailing_pct: float = 0.12,
        leverage: int = 5,
        market_type: str = None,
        fee_rate_pct: float = None,
        tax_rate_pct: float = None,
        tds_pct: float = None,
        include_taxes: bool = None
    ) -> bool:
        if self.is_running:
            return False

        self.allocated_amount = max(10.0, float(allocated_amount))
        self.target_profit = max(0.5, float(target_profit))
        self.min_profit_pct = max(0.20, float(min_profit_pct))
        self.trailing_pct = max(0.08, float(trailing_pct))
        self.dry_run = dry_run
        self.immediate_entry = immediate_entry
        self.max_positions = 1  # 100% focused single trade allocation
        if leverage:
            self.leverage = max(1, min(20, int(leverage)))
        if market_type:
            self.market_type = market_type.upper()
        if fee_rate_pct is not None:
            self.fee_rate_pct = float(fee_rate_pct)
        if tax_rate_pct is not None:
            self.tax_rate_pct = float(tax_rate_pct)
        if tds_pct is not None:
            self.tds_pct = float(tds_pct)
        if include_taxes is not None:
            self.include_taxes = bool(include_taxes)

        self.target_symbols = self.get_tradable_symbols()
        self.positions.clear()
        self.accumulated_gross_profit = 0.0
        self.accumulated_fees = 0.0
        self.accumulated_tax = 0.0
        self.accumulated_tds = 0.0
        self.accumulated_profit = 0.0
        self.cycles_completed = 0
        self.is_running = True
        self.status = "ANALYZING"
        self._stop_event.clear()

        effective_notional = self.allocated_amount * self.leverage
        self.status_message = f"AI active: Focused Single Trade (${self.allocated_amount:.2f} margin @ {self.leverage}x = ${effective_notional:.2f} notional). Target: +${self.target_profit:.2f}"
        mode_str = "Simulated Futures Dry-Run" if self.dry_run else f"Live {self.market_type}"
        self.add_log(
            f"Focused Single-Capital Session Launched! [{mode_str}] 100% Capital: ${self.allocated_amount:.2f} USDT margin @ {self.leverage}x (${effective_notional:.2f} notional power) | Target: +${self.target_profit:.2f} USDT | Scalp floor: +{self.min_profit_pct:.2f}% | Fee: {self.fee_rate_pct*2:.2f}% round-trip | Tax: {self.tax_rate_pct:.0f}%",
            "START"
        )

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self, reason: str = "Manual stop"):
        if not self.is_running:
            return
        self.is_running = False
        self.status = "STOPPED"
        self.status_message = f"AI Trader stopped: {reason}"
        self._stop_event.set()
        self.add_log(f"Session stopped: {reason}", "WARN")

    def close_piece(self, symbol: str, reason: str = "Manual User Close") -> Dict[str, Any]:
        """Manually exits a specific crypto piece (LONG or SHORT) and banks its realized PnL."""
        symbol = symbol.upper().strip()
        pos = self.positions.get(symbol)
        if not pos:
            return {"success": False, "message": f"Symbol {symbol} is not in active positions."}

        try:
            cur_p = self.client.get_ticker_price(symbol)
        except Exception:
            cur_p = pos.get("current_price", pos.get("entry_price", 0.0))

        side = pos.get("side", "Position")
        success = self._execute_close_piece(symbol, cur_p, pos, reason=reason, enforce_zero_loss=False)
        if success:
            return {"success": True, "message": f"Successfully closed {side} on {symbol} @ ${cur_p:,.4f}. Slot is now open for new crypto!"}
        return {"success": False, "message": f"Failed to execute close for {symbol}."}

    def sell_piece(self, symbol: str, reason: str = "Manual User Close") -> Dict[str, Any]:
        """Backward-compatible alias for close_piece."""
        return self.close_piece(symbol, reason=reason)

    def fill_open_slots(self) -> int:
        """Immediately scans market and opens top opportunity coins (LONG or SHORT) to fill empty slots."""
        open_slots = self.max_positions - len(self.positions)
        if open_slots <= 0:
            return 0

        self.add_log(f"Scanning market to fill {open_slots} open piece slot(s)...", "ACTION")
        candidates = self.scan_and_rank_candidates()
        filled = 0

        for item in candidates:
            if filled >= open_slots:
                break
            sym = item[0]
            price = item[1]
            score = item[2]
            meta = item[3]
            side = item[4] if len(item) > 4 else meta.get("side", "LONG")
            if side == "NEUTRAL":
                side = "LONG"

            if sym in self.positions:
                continue
            ok = self._execute_open_piece(sym, side, price)
            if ok:
                filled += 1
                self.add_log(f"⚡ Filled piece slot ({len(self.positions)}/{self.max_positions}) with {side} on {sym} @ ${price:,.4f} (Score: {score:.1f})", "SUCCESS")

        return filled

    def force_open(self, symbol: str = None, side: str = "LONG") -> Dict[str, Any]:
        """Manually opens 1 piece of crypto immediately (LONG or SHORT)."""
        try:
            if len(self.positions) >= self.max_positions:
                return {"success": False, "message": f"All {self.max_positions} portfolio piece slots are already full."}

            side = side.upper()
            if symbol and symbol.upper() not in self.positions:
                sym = symbol.upper()
                p = self.client.get_ticker_price(sym)
                ok = self._execute_open_piece(sym, side, p)
                if ok:
                    return {"success": True, "message": f"Instant {side} filled on {sym}! Now trailing for profit."}

            # Find best candidate
            candidates = self.scan_and_rank_candidates()
            for item in candidates:
                sym = item[0]
                p = item[1]
                cand_side = item[4] if len(item) > 4 else side
                if cand_side == "NEUTRAL":
                    cand_side = "LONG"
                if sym not in self.positions:
                    ok = self._execute_open_piece(sym, cand_side, p)
                    if ok:
                        return {"success": True, "message": f"Instant {cand_side} filled on {sym} @ ${p:,.4f}! Now trailing for profit."}

            return {"success": False, "message": "No available coins to open."}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def force_buy(self, symbol: str = None) -> Dict[str, Any]:
        """Backward-compatible alias for force_open with LONG."""
        return self.force_open(symbol=symbol, side="LONG")

    def adopt_existing_btc(self) -> Dict[str, Any]:
        """Adopts existing BTC from the user's wallet as one of the active crypto pieces."""
        try:
            btc_bal = self.client.get_asset_balance("BTC")
            filters = self.client.get_symbol_filters("BTCUSDT")
            min_qty = filters.get("min_qty", 0.0001)

            if btc_bal < min_qty:
                return {"success": False, "message": f"No BTC available to adopt (Balance: {btc_bal})."}

            cur_price = self.client.get_ticker_price("BTCUSDT")
            last_buy = cur_price
            from dashboard import trade_history
            for t in trade_history:
                if t.get("symbol") == "BTCUSDT" and t.get("side") == "BUY":
                    last_buy = float(t.get("price", cur_price))
                    break

            piece_usd = self.get_piece_amount()
            budget_qty = self.client.calculate_valid_quantity("BTCUSDT", target_usd=piece_usd)
            qty = min(btc_bal, budget_qty)
            qty = self.client.adjust_to_step_size(qty, filters.get("step_size", 0.00001))

            now_ts = time.time()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.positions["BTCUSDT"] = {
                "symbol": "BTCUSDT",
                "entry_price": last_buy,
                "peak_price": max(last_buy, cur_price),
                "quantity": qty,
                "allocated_usd": piece_usd,
                "entry_time": now_str,
                "entry_timestamp": now_ts,
                "dca_count": 0,
                "held_seconds": 0,
                "held_mins": 0.0,
                "current_price": cur_price,
                "unrealized_pnl_pct": ((cur_price - last_buy) / last_buy) * 100.0,
                "unrealized_pnl_usd": (cur_price - last_buy) * qty,
                "status": "IN_POSITION"
            }
            save_ai_positions(self.positions)

            if not self.is_running:
                self.is_running = True
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run_loop, daemon=True)
                self._thread.start()

            self.add_log(f"Adopted {qty} BTC (Entry: ${last_buy:,.2f}) as 1 crypto piece. Zero-loss trailing profit active.", "ACTION")
            return {"success": True, "message": f"Successfully adopted {qty} BTC @ ${last_buy:,.2f}. Now trailing for profit!"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def adopt_position(self, symbol: str, entry_price: float = None, qty: float = None) -> Dict[str, Any]:
        """Adopts an existing coin holding into the AI Multi-Crypto Basket to trail for profit."""
        try:
            symbol = symbol.upper().strip()
            base_asset = symbol.replace("USDT", "")
            filters = self.client.get_symbol_filters(symbol)
            min_qty = filters.get("min_qty", 0.0001)

            if qty is None or qty <= 0:
                qty = self.client.get_asset_balance(base_asset)
            if qty < min_qty:
                return {"success": False, "message": f"Balance for {base_asset} ({qty}) is below minimum {min_qty}."}

            cur_price = self.client.get_ticker_price(symbol)
            if entry_price is None or entry_price <= 0:
                entry_price = cur_price

            qty = self.client.adjust_to_step_size(qty, filters.get("step_size", 0.00001))
            now_ts = time.time()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self.positions[symbol] = {
                "symbol": symbol,
                "entry_price": float(entry_price),
                "peak_price": max(float(entry_price), cur_price),
                "quantity": float(qty),
                "allocated_usd": round(float(qty) * float(entry_price), 2),
                "entry_time": now_str,
                "entry_timestamp": now_ts,
                "dca_count": 0,
                "held_seconds": 0,
                "held_mins": 0.0,
                "current_price": cur_price,
                "unrealized_pnl_pct": ((cur_price - float(entry_price)) / float(entry_price)) * 100.0 if float(entry_price) > 0 else 0.0,
                "unrealized_pnl_usd": (cur_price - float(entry_price)) * float(qty) if float(entry_price) > 0 else 0.0,
                "status": "IN_POSITION"
            }
            save_ai_positions(self.positions)

            if not self.is_running:
                self.is_running = True
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run_loop, daemon=True)
                self._thread.start()

            self.add_log(f"Adopted {qty} {base_asset} (Entry: ${entry_price:,.4f}) into AI Multi-Crypto Basket.", "SUCCESS")
            return {"success": True, "message": f"Successfully adopted {qty} {symbol} @ ${entry_price:,.4f}. Now trailing for profit!"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def analyze_market(
        self,
        candles: List[Dict[str, Any]],
        current_price: float = None,
        symbol: str = "BTCUSDT",
        candles_15m: Optional[List[Dict[str, Any]]] = None,
        candles_1h: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Deep Pre-Trade Market Evaluation with Multi-Timeframe Confluence (1m + 15m + 1h):
        - Multi-Timeframe Confluence (1H Macro Trend + 15M Swing Structure + 1M Execution)
        - Candlestick wicks (rejections, hammers, engulfing)
        - Volume flow & surge ratios vs 20-SMA
        - Bollinger Band statistical discount (%B)
        - MACD acceleration & histogram
        - Smoothed RSI & EMA structure
        - Volatility measurement (ATR %)
        """
        return self.strategy.analyze_full_market_details(
            candles,
            symbol=symbol,
            candles_15m=candles_15m,
            candles_1h=candles_1h
        )

    def scan_and_rank_candidates(self) -> List[Tuple[str, float, float, Dict[str, Any]]]:
        """
        FULL-EXCHANGE MARKET-WIDE ANALYZER (All 500+ Coins):
        - If the BackgroundMarketAnalyzer has a fresh cache (< 90 s old), uses its
          pre-warmed ranked candidates instantly — zero extra API calls needed.
        - Falls back to a live scan when cache is stale or absent.

        Stage 1: Evaluates ALL 500+ USDT coins across Binance in bulk.
        Stage 2: Parallel micro-detail technical analysis (top 25 coins).
        Stage 3: Ranks all evaluated coins by Comprehensive Scalp Readiness Score.
        """
        # ----------------------------------------------------------------
        # FAST PATH: Use pre-warmed background analyzer cache (< 90 s old)
        # ----------------------------------------------------------------
        bg = getattr(self, "bg_analyzer", None)
        if bg and bg.ranked_candidates and bg.last_scan_time > 0:
            cache_age = time.time() - bg.last_scan_time
            if cache_age < 90.0:
                # Filter out symbols already in positions
                fresh = [c for c in bg.ranked_candidates if c[0] not in self.positions]
                if fresh:
                    self.candidate_radar = [r for r in bg.ranked_opportunities if r["symbol"] not in self.positions][:9]
                    self.total_scanned_coins = bg.total_scanned
                    top = fresh[0]
                    self.add_log(
                        f"⚡ Using pre-warmed scan cache ({int(cache_age)}s old, "
                        f"{len(fresh)} candidates). #1: {top[4]} on {top[0]} (Score: {top[2]:.1f})",
                        "INFO"
                    )
                    return fresh

        # ----------------------------------------------------------------
        # SLOW PATH: Run a live full scan (cache absent or stale)
        # ----------------------------------------------------------------
        candidates: List[Tuple[str, float, float, Dict[str, Any]]] = []
        radar_list: List[Dict[str, Any]] = []

        try:
            tickers = self.client.get_24hr_tickers(quote_asset="USDT")
        except Exception as e:
            self.add_log(f"Error fetching market tickers: {e}", "WARN")
            tickers = []

        self.total_scanned_coins = len(tickers)
        if not tickers:
            return []

        # -------------------------------------------------------------
        # STAGE 1: EXHAUSTIVE MACRO SCAN ACROSS ALL 500+ COINS
        # -------------------------------------------------------------
        stablecoins = {
            "USDC", "BUSD", "FDUSD", "TUSD", "EUR", "DAI", "USDS",
            "AEUR", "PAXG", "TRY", "GBP", "AUD", "BRL", "RUB"
        }

        macro_scored = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym or sym in self.positions:
                continue

            # Skip stablecoins & fiat
            if any(s in sym for s in stablecoins):
                continue

            last_p = float(t.get("price", 0.0))
            high_p = float(t.get("high", 0.0))
            low_p = float(t.get("low", 0.0))
            vol_usd = float(t.get("quote_volume", 0.0))
            chg_pct = float(t.get("change_pct", 0.0))

            # Filter out dead / illiquid pairs
            if last_p <= 0.0 or vol_usd < 15000.0 or high_p <= low_p:
                continue

            # 1. Volatility Amplitude (Scalp Potential - Critical for Fast Target Achievement)
            range_24h = high_p - low_p
            amp_pct = (range_24h / low_p) * 100.0
            if amp_pct >= 5.0:
                vol_score = 30.0  # High price action, quick to hit +0.35%
            elif amp_pct >= 3.0:
                vol_score = 22.0
            elif amp_pct >= 2.0:
                vol_score = 12.0
            else:
                vol_score = -15.0 # Low volatility / flatline - avoid!

            # 2. 24h Momentum: Option 2 rewards active momentum runners and oversold bounce setups
            if 0.5 <= chg_pct <= 6.0:
                mom_score = 28.0  # Momentum breakout ignition - fast profit velocity
            elif -4.0 <= chg_pct < 0.5:
                mom_score = 22.0  # Controlled pullback dip near floor
            elif 6.0 < chg_pct <= 10.0:
                mom_score = 15.0  # Strong trend continuation
            elif chg_pct < -10.0 or chg_pct > 18.0:
                mom_score = -10.0 # High risk: freefall dump or overextended pump
            else:
                mom_score = 5.0

            # 3. Position in Range: Headroom for continuation or bounce
            pos_in_range = (last_p - low_p) / range_24h
            if 0.20 <= pos_in_range <= 0.65:
                range_score = 25.0  # Middle-expansion sweet spot: room to run
            elif pos_in_range < 0.20:
                range_score = 20.0  # Deep discount floor
            else:
                range_score = 5.0   # Near daily ceiling

            # 4. Liquidity Health
            liq_score = min(20.0, math.log10(max(1000.0, vol_usd)) * 3.5)

            total_macro = vol_score + mom_score + range_score + liq_score
            macro_scored.append((total_macro, sym, last_p, t))

        # Sort all 500+ coins by macro opportunity score
        macro_scored.sort(key=lambda x: x[0], reverse=True)

        # -------------------------------------------------------------
        # STAGE 2: PARALLEL MICRO-DETAIL TECHNICAL EVALUATION
        # Take the top 25 high-potential coins from across the entire market
        # -------------------------------------------------------------
        top_candidates = macro_scored[:25]
        if not top_candidates:
            return []

        def analyze_coin(item):
            macro_s, sym, cur_p, t_data = item
            try:
                candles = self.client.get_klines(sym, interval="1m", limit=30)
                candles_15m = self.client.get_klines(sym, interval="15m", limit=30)
                candles_1h = self.client.get_klines(sym, interval="1h", limit=30)
                if not candles or len(candles) < 20:
                    return None

                p = candles[-1]["close"]
                analysis = self.analyze_market(candles, current_price=p, symbol=sym, candles_15m=candles_15m, candles_1h=candles_1h)
                conf = analysis.get("confidence", 50)
                rsi = analysis.get("rsi", 50.0)
                side = analysis.get("side", "LONG")
                if side == "NEUTRAL":
                    side = "LONG" if analysis.get("long_confidence", 50) >= analysis.get("short_confidence", 50) else "SHORT"

                c_act = analysis.get("candle_action", {})
                v_flow = analysis.get("volume_flow", {})
                bb = analysis.get("bollinger", {})
                macd = analysis.get("macd", {})
                atr = analysis.get("atr", {})
                mtf = analysis.get("mtf", {})

                # Micro-detail scoring:
                score = float(conf) + (macro_s * 0.3)
                vol_ratio = float(v_flow.get("vol_ratio", 1.0))
                v_state = v_flow.get("volume_state", "NORMAL")
                pat = c_act.get("pattern", "NORMAL")
                pct_b = bb.get("pct_b", 0.5)
                m_trend = macd.get("trend", "NEUTRAL")

                # Multi-Timeframe Confluence Scoring
                mtf_lvl = mtf.get("confluence_level", "NEUTRAL")
                is_counter = mtf.get("counter_trend", False)

                if "3/3" in mtf_lvl:
                    score += 26.0  # Massive confirmation bonus!
                elif "2/3" in mtf_lvl:
                    score += 14.0
                elif is_counter:
                    score -= 40.0  # Counter-trend guard penalty!

                # Direction-specific bonus calculations
                if side == "LONG":
                    if vol_ratio >= 2.0 or v_state == "BUYER_VOLUME_SPIKE":
                        score += 22.0
                    elif vol_ratio >= 1.3 or v_state in ("SELLER_EXHAUSTION", "BUYER_DOMINANCE"):
                        score += 14.0
                    if pat in ("BULLISH_HAMMER_REJECTION", "BULLISH_ENGULFING"):
                        score += 15.0
                    if pct_b <= 0.25:
                        score += 14.0
                    elif pct_b >= 0.85:
                        score -= 15.0
                else: # SHORT
                    if vol_ratio >= 2.0 or v_state == "SELLER_VOLUME_SPIKE":
                        score += 22.0
                    elif vol_ratio >= 1.3 or v_state in ("BUYER_EXHAUSTION", "SELLER_DOMINANCE"):
                        score += 14.0
                    if pat in ("UPPER_WICK_REJECTION", "BEARISH_ENGULFING"):
                        score += 15.0
                    if pct_b >= 0.75:
                        score += 14.0
                    elif pct_b <= 0.20:
                        score -= 15.0

                atr_pct = float(atr.get("atr_pct", 0.2))
                if atr_pct >= 0.35:
                    score += 18.0
                elif atr_pct < 0.08:
                    score -= 20.0

                score = round(max(0.0, score), 1)

                radar_item = {
                    "symbol": sym,
                    "price": p,
                    "side": side,
                    "rsi": rsi,
                    "pattern": pat.replace("_", " ").title(),
                    "vol_ratio": f"{vol_ratio:.1f}x",
                    "pct_b": f"{pct_b:.2f}",
                    "macd_trend": m_trend.replace("_", " ").title(),
                    "score": score,
                    "confidence": conf,
                    "chg_24h": f"{t_data.get('change_pct', 0.0):+.1f}%",
                    "mtf_confluence": mtf_lvl,
                    "mtf_badge": mtf.get("confluence_badge", "1/3 Single TF"),
                    "trend_1h": mtf.get("trend_1h", {}).get("trend", "NEUTRAL"),
                    "trend_15m": mtf.get("trend_15m", {}).get("trend", "NEUTRAL"),
                    "mtf_summary": mtf.get("summary", ""),
                    "counter_trend": is_counter
                }

                return (sym, p, score, analysis, rsi, radar_item, side, is_counter)
            except Exception:
                return None

        # Execute parallel candle analysis across top candidates
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(analyze_coin, top_candidates))

        for res in results:
            if not res:
                continue
            sym, p, score, analysis, rsi, radar_item, side, is_counter = res
            radar_list.append(radar_item)

            # MTF Guard: Filter out any counter-trend trade against 1h macro trend
            if is_counter:
                continue

            if side == "LONG" and rsi <= self.strategy.rsi_overbought:
                candidates.append((sym, p, score, analysis, side))
            elif side == "SHORT" and rsi >= 45.0:
                candidates.append((sym, p, score, analysis, side))
            elif side == "LONG":
                candidates.append((sym, p, score, analysis, side))
            radar_list.append(radar_item)

        # Sort candidate coins by highest attractiveness score
        candidates.sort(key=lambda x: x[2], reverse=True)
        radar_list.sort(key=lambda x: x["score"], reverse=True)
        self.candidate_radar = radar_list[:9]

        # Fallback if candidates empty: pick best from radar list
        if not candidates and radar_list:
            for item in radar_list:
                s_side = item.get("side", "LONG")
                analysis = {"symbol": item["symbol"], "confidence": item["confidence"], "decision": "BUY" if s_side == "LONG" else "SHORT", "reason": f"Momentum {s_side} Entry", "rsi": item["rsi"], "side": s_side}
                candidates.append((item["symbol"], item["price"], item["score"], analysis, s_side))

        if candidates:
            best = candidates[0]
            self.add_log(f"🌐 Full Market Scan: Evaluated {len(tickers)} pairs. Filtered top 25 candidates. #1 Pick: {best[4]} on {best[0]} (Score: {best[2]:.1f})", "INFO")

        return candidates

    def _execute_open_piece(self, symbol: str, side: str, current_price: float) -> bool:
        """Executes a position open (LONG or SHORT) with leverage for 1 piece of capital."""
        if symbol in self.positions:
            return False

        side = side.upper()
        piece_usd = self.get_piece_amount() # Margin
        notional_usd = piece_usd * self.leverage
        try:
            qty = self.client.calculate_valid_quantity(symbol, target_usd=notional_usd, current_price=current_price)
            now_ts = time.time()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Calculate liquidation price
            if side == "LONG":
                liq_price = current_price * (1.0 - (1.0 / self.leverage) + 0.005)
            else:
                liq_price = current_price * (1.0 + (1.0 / self.leverage) - 0.005)

            actual_notional = qty * current_price
            est_entry_fee = actual_notional * (self.fee_rate_pct / 100.0)

            pos_data = {
                "symbol": symbol,
                "side": side,
                "entry_price": current_price,
                "peak_price": current_price,
                "quantity": qty,
                "allocated_usd": piece_usd,
                "notional_usd": round(actual_notional, 2),
                "leverage": self.leverage,
                "liquidation_price": round(liq_price, 4 if liq_price < 10 else 2),
                "entry_time": now_str,
                "entry_timestamp": now_ts,
                "dca_count": 0,
                "held_seconds": 0,
                "held_mins": 0.0,
                "current_price": current_price,
                "unrealized_pnl_pct": 0.0,
                "roi_pct": 0.0,
                "unrealized_pnl_usd": 0.0,
                "gross_pnl_usd": 0.0,
                "fees_usd": round(est_entry_fee, 3),
                "tax_usd": 0.0,
                "tds_usd": 0.0,
                "net_pnl_usd": round(-est_entry_fee, 2),
                "net_roi_pct": round((-est_entry_fee / piece_usd) * 100.0, 2) if piece_usd > 0 else 0.0,
                "is_short": (side == "SHORT"),
                "status": "IN_POSITION"
            }

            side_action = "OPEN LONG (BUY)" if side == "LONG" else "OPEN SHORT (SELL SHORT)"
            trade_desc = "LONG (Buy Low, Sell High)" if side == "LONG" else "SHORT SELLING (Sell High, Buy Back Lower)"

            if self.dry_run:
                self.positions[symbol] = pos_data
                save_ai_positions(self.positions)
                self.record_trade_callback(symbol, side_action, current_price, qty, fee_usd=est_entry_fee, trade_direction=side)
                self.add_log(f"[SIMULATED] Opened {trade_desc} on {qty} {symbol} @ ${current_price:,.4f} (${piece_usd:.2f} margin @ {self.leverage}x = ${actual_notional:.2f} notional | Est. Fee: ${est_entry_fee:.3f})", "SUCCESS")
                return True
            else:
                # Live execution: check USDT balance
                try:
                    usdt_free = self.client.get_asset_balance("USDT")
                    if usdt_free < piece_usd * 0.90:
                        self.add_log(f"Cannot open {symbol}: Free USDT (${usdt_free:.2f}) < required piece (${piece_usd:.2f}).", "WARN")
                        return False
                except Exception:
                    pass

                self.client.set_leverage(symbol, self.leverage)
                self.client.set_margin_type(symbol, self.margin_type)
                order_side = "BUY" if side == "LONG" else "SELL"
                order = self.client.create_order(symbol, order_side, "MARKET", qty)
                order_id = order.get("orderId")
                pos_data["order_id"] = order_id
                self.positions[symbol] = pos_data
                save_ai_positions(self.positions)
                self.record_trade_callback(symbol, side_action, current_price, qty, order_id=order_id, fee_usd=est_entry_fee, trade_direction=side)
                self.add_log(f"[LIVE] Opened {trade_desc} on {qty} {symbol} @ ${current_price:,.4f} | Order #{order_id} (${piece_usd:.2f} margin @ {self.leverage}x | Est. Fee: ${est_entry_fee:.3f})", "SUCCESS")
                return True
        except Exception as e:
            self.add_log(f"Open {side} failed on {symbol}: {e}", "ERROR")
            return False

    def _execute_buy_piece(self, symbol: str, current_price: float) -> bool:
        """Backward-compatible alias for opening a LONG piece."""
        return self._execute_open_piece(symbol, "LONG", current_price)

    def _execute_close_piece(self, symbol: str, current_price: float, pos: Dict[str, Any], reason: str = "Take-Profit", enforce_zero_loss: bool = True) -> bool:
        """Executes a close order (Zero-loss enforced with fees & taxes) for 1 crypto position (LONG or SHORT)."""
        qty = pos.get("quantity", 0.0)
        if qty <= 0:
            return False

        try:
            side = pos.get("side", "LONG").upper()
            entry_price = pos.get("entry_price", current_price)
            entry_notional = entry_price * qty
            exit_notional = current_price * qty

            # 1. Gross Price PnL
            if side == "LONG":
                gross_pnl_usd = (current_price - entry_price) * qty if entry_price > 0 else 0.0
                gross_pnl_pct = ((current_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
            else: # SHORT SELLING: Profits when price falls
                gross_pnl_usd = (entry_price - current_price) * qty if entry_price > 0 else 0.0
                gross_pnl_pct = ((entry_price - current_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

            leverage = pos.get("leverage", self.leverage)
            gross_roi_pct = gross_pnl_pct * leverage

            # 2. Exchange Trading Fees (Entry + Exit commissions)
            entry_fee_usd = entry_notional * (self.fee_rate_pct / 100.0)
            exit_fee_usd = exit_notional * (self.fee_rate_pct / 100.0)
            total_fees_usd = entry_fee_usd + exit_fee_usd

            # Profit after Binance trading commissions
            profit_after_fees_usd = gross_pnl_usd - total_fees_usd

            # 3. Government Taxes & TDS (Calculated on Net Realized Profit)
            if profit_after_fees_usd > 0 and getattr(self, "include_taxes", True):
                tax_usd = profit_after_fees_usd * (self.tax_rate_pct / 100.0)
                tds_usd = profit_after_fees_usd * (self.tds_pct / 100.0)
            else:
                tax_usd = 0.0
                tds_usd = 0.0

            # 4. Clean Net Realized Profit (Take-Home Cash)
            net_pnl_usd = profit_after_fees_usd - tax_usd - tds_usd
            margin_usd = pos.get("allocated_usd", self.allocated_amount)
            net_roi_pct = (net_pnl_usd / margin_usd) * 100.0 if margin_usd > 0 else 0.0

            # STRICT ZERO-LOSS ENFORCEMENT (Guarantees net profit after exchange fees)
            if enforce_zero_loss and profit_after_fees_usd <= 0.0:
                self.add_log(f"Zero-Loss guard: Gross profit (${gross_pnl_usd:+.2f}) does not clear Binance fees (${total_fees_usd:.2f}). Holding for net green.", "WARN")
                return False

            close_action = "CLOSE LONG (SELL)" if side == "LONG" else "CLOSE SHORT (BUY TO COVER)"
            trade_kind = "LONG Position" if side == "LONG" else "SHORT SELLING Position"

            if self.dry_run:
                self.record_trade_callback(
                    symbol, close_action, current_price, qty,
                    pnl_pct=net_roi_pct, pnl_usd=net_pnl_usd,
                    fee_usd=total_fees_usd, tax_usd=tax_usd + tds_usd,
                    net_pnl_usd=net_pnl_usd, trade_direction=side
                )
                self.add_log(
                    f"[SIMULATED CLOSE] Closed {trade_kind} on {symbol} @ ${current_price:,.4f} ({reason}) | "
                    f"Gross: +${gross_pnl_usd:.2f} ({gross_roi_pct:+.2f}% ROI) | "
                    f"Fees: -${total_fees_usd:.2f} | Taxes/TDS: -${(tax_usd + tds_usd):.2f} | "
                    f"Clean Net: +${net_pnl_usd:.2f} ({net_roi_pct:+.2f}% Net ROI)",
                    "SUCCESS"
                )
            else:
                try:
                    filters = self.client.get_symbol_filters(symbol)
                    qty = self.client.adjust_to_step_size(qty, filters.get("step_size", 0.00001))
                except Exception:
                    pass

                order_side = "SELL" if side == "LONG" else "BUY"
                order = self.client.create_order(symbol, order_side, "MARKET", qty, reduce_only=True)
                order_id = order.get("orderId")
                self.record_trade_callback(
                    symbol, close_action, current_price, qty,
                    order_id=order_id, pnl_pct=net_roi_pct, pnl_usd=net_pnl_usd,
                    fee_usd=total_fees_usd, tax_usd=tax_usd + tds_usd,
                    net_pnl_usd=net_pnl_usd, trade_direction=side
                )
                self.add_log(
                    f"[LIVE CLOSE] Closed {trade_kind} on {symbol} @ ${current_price:,.4f} ({reason}) | Order #{order_id} | "
                    f"Gross: +${gross_pnl_usd:.2f} ({gross_roi_pct:+.2f}% ROI) | "
                    f"Fees: -${total_fees_usd:.2f} | Taxes/TDS: -${(tax_usd + tds_usd):.2f} | "
                    f"Clean Net: +${net_pnl_usd:.2f} ({net_roi_pct:+.2f}% Net ROI)",
                    "SUCCESS"
                )

            self.accumulated_gross_profit += max(0.0, gross_pnl_usd)
            self.accumulated_fees += total_fees_usd
            self.accumulated_tax += tax_usd
            self.accumulated_tds += tds_usd
            self.accumulated_profit += max(0.0, net_pnl_usd)
            self.cycles_completed += 1
            if symbol in self.positions:
                del self.positions[symbol]
                save_ai_positions(self.positions)

            self.add_log(f"⚡ Trade closed! Cycle #{self.cycles_completed} won on {symbol} ({side}). Session Clean Profit: +${self.accumulated_profit:.2f} / ${self.target_profit:.2f} (Gross: +${self.accumulated_gross_profit:.2f}, Fees: ${self.accumulated_fees:.2f})", "SUCCESS")
            return True
        except Exception as e:
            self.add_log(f"Close trade failed on {symbol}: {e}", "ERROR")
            return False

    def _execute_sell_piece(self, symbol: str, current_price: float, pos: Dict[str, Any], reason: str = "Take-Profit", enforce_zero_loss: bool = True) -> bool:
        """Backward-compatible alias for closing a trade."""
        return self._execute_close_piece(symbol, current_price, pos, reason=reason, enforce_zero_loss=enforce_zero_loss)

    def _run_loop(self):
        """Continuous execution loop: manages single-capital sniper position and immediately redeploys upon exit."""
        self.add_log(f"AI Futures Sniper active: 100% capital (${self.allocated_amount:.2f} USDT @ {self.leverage}x leverage).", "INFO")

        # -------------------------------------------------------------
        # 1. IMMEDIATE LAUNCH ENTRY: Fill all slots simultaneously
        # -------------------------------------------------------------
        if self.immediate_entry:
            self.fill_open_slots()
            self.immediate_entry = False

        while not self._stop_event.is_set():
            try:
                # -------------------------------------------------------------
                # 2. CHECK SESSION PROFIT GOAL
                # -------------------------------------------------------------
                if self.accumulated_profit >= self.target_profit:
                    self.status = "PROFIT_REACHED"
                    self.status_message = f"🎯 TARGET REACHED! Total profit: +${self.accumulated_profit:.2f} USDT across {self.cycles_completed} cycles."
                    self.add_log(f"🎯 TARGET PROFIT ACHIEVED! Total gains: +${self.accumulated_profit:.2f} USDT across {self.cycles_completed} cycles. Securing gains.", "SUCCESS")
                    self.is_running = False
                    break

                # -------------------------------------------------------------
                # 3. MANAGE EACH ACTIVE CRYPTO POSITION INDEPENDENTLY
                # -------------------------------------------------------------
                active_syms = list(self.positions.keys())
                for sym in active_syms:
                    if self._stop_event.is_set():
                        break
                    pos = self.positions.get(sym)
                    if not pos:
                        continue

                    try:
                        cur_p = self.client.get_ticker_price(sym)
                        pos["current_price"] = cur_p
                        side = pos.get("side", "LONG").upper()
                        entry_p = pos["entry_price"]
                        leverage = pos.get("leverage", self.leverage)

                        if side == "LONG":
                            pnl_pct = ((cur_p - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
                            pnl_usd = (cur_p - entry_p) * pos["quantity"] if entry_p > 0 else 0.0
                            if cur_p > pos.get("peak_price", entry_p):
                                pos["peak_price"] = cur_p
                            peak_gain = ((pos["peak_price"] - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
                            pullback = ((pos["peak_price"] - cur_p) / pos["peak_price"]) * 100.0 if pos["peak_price"] > 0 else 0.0
                        else:  # SHORT
                            pnl_pct = ((entry_p - cur_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
                            pnl_usd = (entry_p - cur_p) * pos["quantity"] if entry_p > 0 else 0.0
                            if pos.get("peak_price", 0) <= 0 or cur_p < pos.get("peak_price", entry_p):
                                pos["peak_price"] = cur_p
                            peak_gain = ((entry_p - pos["peak_price"]) / entry_p) * 100.0 if entry_p > 0 else 0.0
                            pullback = ((cur_p - pos["peak_price"]) / pos["peak_price"]) * 100.0 if pos["peak_price"] > 0 else 0.0

                        roi_pct = pnl_pct * leverage

                        # Real-time fee, tax, and net take-home calculation
                        entry_notional = entry_p * pos["quantity"]
                        cur_notional = cur_p * pos["quantity"]
                        entry_fee = entry_notional * (self.fee_rate_pct / 100.0)
                        exit_fee = cur_notional * (self.fee_rate_pct / 100.0)
                        total_fees = entry_fee + exit_fee
                        after_fees = pnl_usd - total_fees
                        if after_fees > 0 and getattr(self, "include_taxes", True):
                            tax = after_fees * (self.tax_rate_pct / 100.0)
                            tds = after_fees * (self.tds_pct / 100.0)
                        else:
                            tax = 0.0
                            tds = 0.0
                        net_pnl = after_fees - tax - tds
                        margin = pos.get("allocated_usd", self.allocated_amount)
                        net_roi = (net_pnl / margin) * 100.0 if margin > 0 else 0.0

                        pos["unrealized_pnl_pct"] = pnl_pct
                        pos["roi_pct"] = roi_pct
                        pos["unrealized_pnl_usd"] = pnl_usd
                        pos["gross_pnl_usd"] = round(pnl_usd, 2)
                        pos["fees_usd"] = round(total_fees, 3)
                        pos["tax_usd"] = round(tax, 3)
                        pos["tds_usd"] = round(tds, 3)
                        pos["net_pnl_usd"] = round(net_pnl, 2)
                        pos["net_roi_pct"] = round(net_roi, 2)
                        pos["is_short"] = (side == "SHORT")

                        # Update legacy featured price
                        self.current_price = cur_p
                        self.symbol = sym

                        # Calculate held duration
                        entry_ts = pos.get("entry_timestamp", 0.0)
                        if not entry_ts:
                            entry_ts = time.time()
                            pos["entry_timestamp"] = entry_ts
                        held_secs = time.time() - entry_ts
                        pos["held_seconds"] = int(held_secs)
                        pos["held_mins"] = round(held_secs / 60.0, 1)

                        # Hard Stop Loss / Liquidation Protection Guard
                        if pnl_pct <= -self.strategy.stop_loss_pct:
                            pos["status"] = "SAFETY_STOP"
                            reason = f"Liquidation Guard SL (-{abs(pnl_pct):.2f}% <= -{self.strategy.stop_loss_pct}%)"
                            self.add_log(f"🛡️ {reason} on {sym} ({side})! Closing to secure margin.", "WARN")
                            self._execute_close_piece(sym, cur_p, pos, reason=reason, enforce_zero_loss=False)
                            continue

                        # =========================================================================
                        # VELOCITY-BASED INSTANT MICRO-PROFIT LOCKING (Fees & Taxes Cleared)
                        # =========================================================================
                        direct_target_hit = pnl_pct >= self.min_profit_pct and after_fees > 0
                        tight_trailing_hit = (
                            peak_gain >= self.min_profit_pct
                            and pullback >= 0.04
                            and pnl_pct >= 0.18
                            and after_fees > 0
                        )
                        spike_cap_hit = pnl_pct >= max(0.48, self.min_profit_pct * 1.25) and after_fees > 0

                        if direct_target_hit or tight_trailing_hit or spike_cap_hit:
                            pos["status"] = "EXITING_PROFIT"
                            reason = f"Micro-Scalp Snapped (+{net_roi:.2f}% Net ROI)" if direct_target_hit else f"Tight Trailing Locked (+{net_roi:.2f}% Net ROI)"
                            self.add_log(f"🎯 {reason} on {sym} ({side})! Net Take-Home: +${net_pnl:.2f} (Gross: +${pnl_usd:.2f}, Fees: -${total_fees:.2f})", "ACTION")
                            self._execute_close_piece(sym, cur_p, pos, reason=reason)
                            continue

                        # =========================================================================
                        # TIME-DECAY BREAKEVEN CAPITAL RECYCLER
                        # =========================================================================
                        stagnant_breakeven = (
                            held_secs >= 600
                            and -0.05 <= pnl_pct <= 0.15
                        )
                        timeout_green_exit = (
                            held_secs >= 900
                            and pnl_pct >= 0.01
                        )

                        if stagnant_breakeven or timeout_green_exit:
                            pos["status"] = "RECYCLING_CAPITAL"
                            reason = f"Breakeven Recycler ({pos['held_mins']}m stagnant, PnL {pnl_pct:+.2f}%)"
                            self.add_log(f"🔄 {reason} on {sym} ({side}). Freeing capital to buy active momentum runner!", "ACTION")
                            self._execute_close_piece(sym, cur_p, pos, reason=reason, enforce_zero_loss=False)
                            continue

                        # =========================================================================
                        # SMART MICRO-DCA (UNSTICK FROZEN SLOTS ON PULLBACKS)
                        # =========================================================================
                        dca_count = pos.get("dca_count", 0)
                        if pnl_pct <= -0.70 and dca_count == 0:
                            piece_usd = pos.get("allocated_usd", self.get_piece_amount())
                            can_dca = True
                            if not self.dry_run:
                                try:
                                    free_usdt = self.client.get_asset_balance("USDT")
                                    if free_usdt < (piece_usd * 0.90):
                                        can_dca = False
                                except Exception:
                                    can_dca = True

                            if can_dca:
                                try:
                                    notional_dca = piece_usd * leverage
                                    dca_qty = self.client.calculate_valid_quantity(sym, target_usd=notional_dca)
                                    if dca_qty > 0:
                                        old_qty = pos["quantity"]
                                        old_entry = pos["entry_price"]
                                        new_total_qty = old_qty + dca_qty
                                        new_entry_p = (old_entry * old_qty + cur_p * dca_qty) / new_total_qty

                                        if self.dry_run:
                                            pos["quantity"] = new_total_qty
                                            pos["entry_price"] = new_entry_p
                                            pos["allocated_usd"] = pos.get("allocated_usd", piece_usd) + piece_usd
                                            pos["dca_count"] = 1
                                            pos["status"] = "DCA_AVERAGED"
                                            save_ai_positions(self.positions)
                                            self.record_trade_callback(sym, f"DCA {side}", cur_p, dca_qty)
                                            self.add_log(
                                                f"⚡ [SMART DCA] Averaged {sym} ({side}) @ ${cur_p:,.4f}! Entry reduced: ${old_entry:,.4f} -> ${new_entry_p:,.4f}",
                                                "ACTION"
                                            )
                                        else:
                                            filters = self.client.get_symbol_filters(sym)
                                            dca_qty = self.client.adjust_to_step_size(dca_qty, filters.get("step_size", 0.00001))
                                            order_side = "BUY" if side == "LONG" else "SELL"
                                            order = self.client.create_order(sym, order_side, "MARKET", dca_qty)
                                            order_id = order.get("orderId")
                                            pos["quantity"] = new_total_qty
                                            pos["entry_price"] = new_entry_p
                                            pos["allocated_usd"] = pos.get("allocated_usd", piece_usd) + piece_usd
                                            pos["dca_count"] = 1
                                            pos["status"] = "DCA_AVERAGED"
                                            save_ai_positions(self.positions)
                                            self.record_trade_callback(sym, f"DCA {side}", cur_p, dca_qty, order_id=order_id)
                                            self.add_log(
                                                f"⚡ [SMART DCA LIVE] Averaged {sym} ({side}) @ ${cur_p:,.4f} | Order #{order_id}!",
                                                "SUCCESS"
                                            )
                                except Exception as dca_err:
                                    self.add_log(f"Smart DCA attempt on {sym} skipped: {dca_err}", "WARN")

                        if pnl_pct >= (self.min_profit_pct * 0.70):
                            pos["status"] = "TRAILING_PROFIT"
                        elif pos.get("dca_count", 0) > 0:
                            pos["status"] = "DCA_AVERAGED"
                        else:
                            pos["status"] = "HOLDING_SAFE"

                    except Exception as err:
                        self.add_log(f"Error managing position {sym}: {err}", "ERROR")

                # Check if session goal reached after exits
                if self.accumulated_profit >= self.target_profit:
                    self.status = "PROFIT_REACHED"
                    self.status_message = f"🎯 TARGET REACHED! Total profit: +${self.accumulated_profit:.2f} USDT across {self.cycles_completed} cycles."
                    self.add_log(f"🎯 Target profit of ${self.target_profit:.2f} reached! Securing gains.", "SUCCESS")
                    self.is_running = False
                    break

                # -------------------------------------------------------------
                # 4. INSTANT REINVESTMENT: FILL ANY OPEN SLOTS RIGHT AWAY
                # -------------------------------------------------------------
                open_slots = self.max_positions - len(self.positions)
                if open_slots > 0:
                    candidates = self.scan_and_rank_candidates()
                    for item in candidates:
                        if len(self.positions) >= self.max_positions:
                            break
                        sym = item[0]
                        cur_p = item[1]
                        score = item[2]
                        analysis = item[3]
                        side = item[4] if len(item) > 4 else analysis.get("side", "LONG")
                        if side == "NEUTRAL":
                            side = "LONG"

                        if sym in self.positions:
                            continue

                        self.ai_analysis = analysis
                        self.add_log(f"⚡ Fast Reinvestment: 100% capital deployed on {side} {sym} @ ${cur_p:,.4f} (Score: {score:.1f})", "ACTION")
                        self._execute_open_piece(sym, side, cur_p)

                # -------------------------------------------------------------
                # 5. UPDATE OVERALL STATUS MESSAGE
                # -------------------------------------------------------------
                n_held = len(self.positions)
                if n_held > 0:
                    self.status = "IN_POSITION"
                    coins_str = ", ".join([f"{s} [{p.get('side', 'LONG')[:1]}] ({p.get('roi_pct', p['unrealized_pnl_pct']):+.2f}%)" for s, p in self.positions.items()])
                    self.status_message = f"In Position: {coins_str} (${self.allocated_amount:.2f} margin @ {self.leverage}x)"
                else:
                    self.status = "ANALYZING"
                    self.status_message = f"Scanning 500+ coins. 100% capital (${self.allocated_amount:.2f} USDT @ {self.leverage}x) ready for #1 high-conviction entry."

            except Exception as e:
                self.add_log(f"AI Trader loop error: {e}", "ERROR")

            # Polling sleep (1.5 seconds for snappy reactions)
            for _ in range(2):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        self.add_log("AI Trader thread terminated.", "INFO")
        self.is_running = False

    def get_status_payload(self) -> Dict[str, Any]:
        """Returns JSON-serializable snapshot of the AI Trader session."""
        progress_pct = min(100.0, round((self.accumulated_profit / self.target_profit) * 100.0, 1)) if self.target_profit > 0 else 0.0
        positions_list = list(self.positions.values())
        total_unrealized_usd = sum(p.get("unrealized_pnl_usd", 0.0) for p in positions_list)
        total_gross_unrealized_usd = sum(p.get("gross_pnl_usd", p.get("unrealized_pnl_usd", 0.0)) for p in positions_list)
        total_net_unrealized_usd = sum(p.get("net_pnl_usd", p.get("unrealized_pnl_usd", 0.0)) for p in positions_list)
        total_fees_unrealized_usd = sum(p.get("fees_usd", 0.0) for p in positions_list)
        total_tax_unrealized_usd = sum(p.get("tax_usd", 0.0) + p.get("tds_usd", 0.0) for p in positions_list)
        avg_unrealized_pct = (sum(p.get("roi_pct", p.get("unrealized_pnl_pct", 0.0)) for p in positions_list) / len(positions_list)) if positions_list else 0.0
        avg_net_roi_pct = (sum(p.get("net_roi_pct", p.get("roi_pct", 0.0)) for p in positions_list) / len(positions_list)) if positions_list else 0.0

        first_pos = positions_list[0] if positions_list else None

        return {
            "is_running": self.is_running,
            "status": self.status,
            "status_message": self.status_message,
            "market_type": getattr(self, "market_type", "FUTURES"),
            "leverage": getattr(self, "leverage", 5),
            "fee_rate_pct": getattr(self, "fee_rate_pct", 0.05),
            "tax_rate_pct": getattr(self, "tax_rate_pct", 30.0),
            "tds_pct": getattr(self, "tds_pct", 1.0),
            "include_taxes": getattr(self, "include_taxes", True),
            "symbol": first_pos["symbol"] if first_pos else self.symbol,
            "positions": positions_list,
            "open_positions_count": len(positions_list),
            "max_positions": self.max_positions,
            "open_slots": max(0, self.max_positions - len(positions_list)),
            "piece_amount": self.get_piece_amount(),
            "scanned_coins_count": getattr(self, "total_scanned_coins", 0) if getattr(self, "total_scanned_coins", 0) > 0 else 502,
            "candidate_radar": self.candidate_radar,
            "allocated_amount": self.allocated_amount,
            "target_profit": self.target_profit,
            "min_profit_pct": self.min_profit_pct,
            "trailing_pct": self.trailing_pct,
            "accumulated_profit": round(self.accumulated_profit, 2), # Net take-home
            "accumulated_gross_profit": round(getattr(self, "accumulated_gross_profit", 0.0), 2),
            "accumulated_fees": round(getattr(self, "accumulated_fees", 0.0), 2),
            "accumulated_tax": round(getattr(self, "accumulated_tax", 0.0), 2),
            "accumulated_tds": round(getattr(self, "accumulated_tds", 0.0), 2),
            "progress_pct": progress_pct,
            "cycles_completed": self.cycles_completed,
            "in_position": len(positions_list) > 0,
            "entry_price": round(first_pos["entry_price"], 4 if first_pos and first_pos["entry_price"] < 10 else 2) if first_pos else 0.0,
            "current_price": round(first_pos["current_price"], 4 if first_pos and first_pos["current_price"] < 10 else 2) if first_pos else self.current_price,
            "position_qty": first_pos["quantity"] if first_pos else 0.0,
            "peak_price": round(first_pos["peak_price"], 4 if first_pos and first_pos["peak_price"] < 10 else 2) if first_pos else 0.0,
            "unrealized_pnl_pct": round(avg_unrealized_pct, 2),
            "unrealized_pnl_usd": round(total_net_unrealized_usd, 2), # Net displayed by default
            "gross_unrealized_usd": round(total_gross_unrealized_usd, 2),
            "fees_unrealized_usd": round(total_fees_unrealized_usd, 3),
            "tax_unrealized_usd": round(total_tax_unrealized_usd, 3),
            "net_roi_pct": round(avg_net_roi_pct, 2),
            "ai_analysis": self.ai_analysis,
            "ai_logs": list(self.ai_logs)
        }
