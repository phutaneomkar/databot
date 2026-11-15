import time
import sys
import argparse
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import config
from binance_client import BinanceTestnetClient
from strategy import TradingStrategy

# Configure clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("MultiCryptoBot")

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "ADAUSDT", "XRPUSDT", "DOGEUSDT", "AVAXUSDT",
    "LINKUSDT", "DOTUSDT", "NEARUSDT", "LTCUSDT"
]

class BinanceBuySellBot:
    """
    Automated Multi-Coin & Divided Capital Trading Bot for Binance Spot Testnet.
    Divides trading budget into multiple pieces/slots and trades multiple cryptos
    simultaneously to eliminate waiting time and multiply profit realization speed.
    """

    def __init__(
        self,
        total_budget: float = 60.0,
        max_positions: int = 1,
        target_profit: float = 5.0,
        min_profit_pct: float = 0.35,
        trailing_pct: float = 0.12,
        dry_run: bool = False,
        poll_interval: int = 3
    ):
        self.client = BinanceTestnetClient()
        self.total_budget = max(15.0, float(total_budget))
        self.target_profit = max(0.5, float(target_profit))
        self.min_profit_pct = max(0.20, float(min_profit_pct))
        self.trailing_pct = max(0.08, float(trailing_pct))
        self.dry_run = dry_run
        self.poll_interval = max(1, int(poll_interval))

        # Ensure piece amount >= $10 (Binance minimum notional)
        requested_positions = max(1, min(6, int(max_positions)))
        max_allowed = max(1, int(self.total_budget // 10.0))
        self.max_positions = min(requested_positions, max_allowed)

        self.strategy = TradingStrategy(
            fast_period=7,
            slow_period=25,
            rsi_period=14,
            rsi_oversold=35.0,
            rsi_overbought=66.0,
            stop_loss_pct=1.5,
            take_profit_pct=self.min_profit_pct
        )

        self.piece_amount_usd = round(self.total_budget / self.max_positions, 2)
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.accumulated_profit: float = 0.0
        self.cycles_completed: int = 0
        self.symbols: List[str] = list(DEFAULT_SYMBOLS)

    def get_tradable_symbols(self) -> List[str]:
        """Discovers liquid USDT trading pairs from Binance Testnet."""
        try:
            tickers = self.client.get_24hr_tickers(quote_asset="USDT")
            symbols = [
                t["symbol"] for t in tickers 
                if t.get("symbol") and not any(
                    stable in t["symbol"] for stable in ["USDC", "BUSD", "FDUSD", "TUSD", "EUR"]
                )
            ]
            if symbols:
                return symbols[:15]
        except Exception as e:
            logger.warning(f"Could not load dynamic market list: {e}. Using fallback pairs.")
        return DEFAULT_SYMBOLS

    def initialize(self):
        """Validates connection, API credentials, and loads tradable coins."""
        print("\n" + "=" * 70)
        print("   BINANCE MULTI-CRYPTO DIVIDED PIECES TRADING BOT")
        print("   Eliminates idle wait time by trading multiple cryptos simultaneously")
        print("=" * 70)
        print(f" • Total Budget:          ${self.total_budget:.2f} USDT")
        print(f" • Crypto Pieces:         {self.max_positions} slots (${self.piece_amount_usd:.2f} per crypto)")
        print(f" • Target Profit Goal:    +${self.target_profit:.2f} USDT")
        print(f" • Scalp Take-Profit:     +{self.min_profit_pct:.2f}% floor (Trailing: {self.trailing_pct:.2f}%)")
        print(f" • Strict Zero-Loss:      ACTIVE (Never sells at a loss)")
        print(f" • Execution Mode:        {'DRY RUN (Simulated)' if self.dry_run else 'LIVE TESTNET'}")
        print("=" * 70 + "\n")

        # 1. Test Connectivity
        try:
            self.client.ping()
            server_time = self.client.get_server_time()
            dt = datetime.fromtimestamp(server_time / 1000.0)
            logger.info(f"Connected to Binance Testnet. Server Time: {dt}")
        except Exception as e:
            logger.error(f"Failed to connect to Binance Testnet: {e}")
            sys.exit(1)

        # 2. Load Tradable Symbols
        self.symbols = self.get_tradable_symbols()
        logger.info(f"Loaded {len(self.symbols)} liquid trading pairs for scanning.")

        # 3. Check Account Balances if not in dry-run
        if not self.dry_run:
            if not self.client.api_key or not self.client.api_secret:
                logger.error("API Key and Secret must be configured in .env for live testnet execution.")
                logger.info("Hint: Create a .env file from .env.example with credentials from https://testnet.binance.vision/")
                sys.exit(1)
            try:
                balances = self.client.get_account_balances()
                usdt_free = balances.get("USDT", {}).get("free", 0.0)
                logger.info(f"Account USDT Balance: ${usdt_free:.2f} USDT")
                if usdt_free < self.piece_amount_usd:
                    logger.warning(f"Free USDT balance (${usdt_free:.2f}) is lower than 1 piece (${self.piece_amount_usd:.2f}). Consider claiming test USDT or using dry-run.")
            except Exception as e:
                logger.error(f"Error checking account balance: {e}")
                sys.exit(1)

    def scan_and_rank_candidates(self) -> List[Tuple[str, float, float]]:
        """Multi-Detail micro-scanner: finds top dip opportunities across unheld coins."""
        candidates = []
        try:
            tickers = self.client.get_24hr_tickers(quote_asset="USDT")
        except Exception:
            tickers = []

        unheld = [
            t for t in tickers 
            if t.get("symbol") not in self.positions and not any(
                stable in t.get("symbol", "") for stable in ["USDC", "BUSD", "FDUSD", "TUSD", "EUR"]
            )
        ]

        if not unheld:
            unheld = [{"symbol": s} for s in self.symbols if s not in self.positions]

        for t in unheld[:8]:
            sym = t.get("symbol")
            if not sym or sym in self.positions:
                continue

            try:
                candles = self.client.get_klines(sym, interval="1m", limit=30)
                if not candles or len(candles) < 20:
                    continue

                cur_p = candles[-1]["close"]
                analysis = self.strategy.analyze_full_market_details(candles, symbol=sym)
                conf = analysis.get("confidence", 50)
                rsi = analysis.get("rsi", 50.0)
                c_act = analysis.get("candle_action", {})
                v_flow = analysis.get("volume_flow", {})
                bb = analysis.get("bollinger", {})

                # Micro-detail scoring
                score = float(conf)
                pat = c_act.get("pattern", "NORMAL")
                if pat in ("BULLISH_HAMMER_REJECTION", "BULLISH_ENGULFING"):
                    score += 15.0
                elif pat == "UPPER_WICK_REJECTION":
                    score -= 15.0

                if v_flow.get("volume_state") == "BUYER_VOLUME_SPIKE":
                    score += 12.0
                if bb.get("pct_b", 0.5) <= 0.25:
                    score += 12.0

                if rsi <= self.strategy.rsi_overbought:
                    candidates.append((sym, cur_p, round(score, 1)))
            except Exception:
                continue

        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates

    def execute_buy(self, symbol: str, current_price: float):
        """Executes a BUY order for 1 crypto piece."""
        if symbol in self.positions:
            return

        try:
            filters = self.client.get_symbol_filters(symbol)
        except Exception:
            filters = {"step_size": 0.0001, "min_qty": 0.0001}

        qty = self.client.calculate_valid_quantity(symbol, target_usd=self.piece_amount_usd)
        logger.info(f">>> [BUY] Buying piece: {qty} {symbol} @ ~${current_price:,.4f} (${self.piece_amount_usd:.2f} allocated)")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if self.dry_run:
            logger.info(f"[DRY RUN] Simulated BUY filled for {symbol}.")
            self.positions[symbol] = {
                "symbol": symbol,
                "entry_price": current_price,
                "peak_price": current_price,
                "quantity": qty,
                "filters": filters,
                "entry_time": now_str
            }
            return

        try:
            order = self.client.create_order(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                quantity=qty
            )
            order_id = order.get("orderId")
            logger.info(f"BUY Filled on Testnet! {symbol} Order ID: {order_id}")
            self.positions[symbol] = {
                "symbol": symbol,
                "entry_price": current_price,
                "peak_price": current_price,
                "quantity": qty,
                "filters": filters,
                "order_id": order_id,
                "entry_time": now_str
            }
        except Exception as e:
            logger.error(f"Failed to execute BUY order on {symbol}: {e}")

    def execute_sell(self, symbol: str, current_price: float, reason: str):
        """Executes a strictly profitable SELL order to close 1 crypto piece."""
        pos = self.positions.get(symbol)
        if not pos:
            return

        qty = pos.get("quantity", 0.0)
        entry_price = pos.get("entry_price", current_price)
        pnl_pct = ((current_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
        pnl_usd = (current_price - entry_price) * qty if entry_price > 0 else 0.0

        # Strict Zero-Loss rule
        if pnl_pct < 0.15:
            logger.warning(f"Zero-Loss blocked exit on {symbol} (PnL {pnl_pct:+.2f}% is below profit floor).")
            return

        logger.info(f">>> [TAKE-PROFIT] {reason} on {symbol}: +{pnl_pct:.2f}% (+${pnl_usd:.2f})")

        if self.dry_run:
            logger.info(f"[DRY RUN] Simulated SELL closed for {symbol}.")
            self.accumulated_profit += pnl_usd
            self.cycles_completed += 1
            del self.positions[symbol]
            logger.info(f"⚡ Cycle #{self.cycles_completed} WON! Total Profit: +${self.accumulated_profit:.2f} / ${self.target_profit:.2f}")
            return

        try:
            filters = pos.get("filters", {})
            step_size = filters.get("step_size", 0.00001)
            qty = self.client.adjust_to_step_size(qty, step_size)

            order = self.client.create_order(
                symbol=symbol,
                side="SELL",
                order_type="MARKET",
                quantity=qty
            )
            logger.info(f"SELL Filled on Testnet! {symbol} Order ID: {order.get('orderId')}")
            self.accumulated_profit += pnl_usd
            self.cycles_completed += 1
            del self.positions[symbol]
            logger.info(f"⚡ Cycle #{self.cycles_completed} WON! Total Profit: +${self.accumulated_profit:.2f} / ${self.target_profit:.2f}")
        except Exception as e:
            logger.error(f"Failed to execute SELL order on {symbol}: {e}")

    def fill_all_open_slots(self):
        """Immediately buys coins for all empty piece slots."""
        open_slots = self.max_positions - len(self.positions)
        if open_slots <= 0:
            return

        candidates = self.scan_and_rank_candidates()
        for sym, price, score in candidates[:open_slots]:
            if sym in self.positions:
                continue
            logger.info(f">>> Filling piece slot ({len(self.positions)+1}/{self.max_positions}) with {sym} @ ${price:,.4f}...")
            self.execute_buy(sym, price)

    def print_status_table(self):
        """Prints a clean status summary of all active crypto slots."""
        active_count = len(self.positions)
        print(f"\n--- BASKET: {active_count}/{self.max_positions} Active Pieces | Profit: +${self.accumulated_profit:.2f}/${self.target_profit:.2f} ({self.cycles_completed} Wins) ---")
        if not self.positions:
            print("  [All Slots Empty - Scanning top coins...]")
        for i, (sym, pos) in enumerate(self.positions.items(), 1):
            cur_p = pos.get("current_price", pos.get("entry_price", 0.0))
            ent_p = pos.get("entry_price", cur_p)
            pnl_pct = ((cur_p - ent_p) / ent_p) * 100.0 if ent_p > 0 else 0.0
            pnl_usd = (cur_p - ent_p) * pos.get("quantity", 0.0) if ent_p > 0 else 0.0
            pnl_str = f"{pnl_pct:+.2f}% (+${pnl_usd:.2f})" if pnl_pct >= 0 else f"{pnl_pct:+.2f}% (-${abs(pnl_usd):.2f})"
            print(f"  Slot #{i}: {sym:<9} | Alloc: ${self.piece_amount_usd:.2f} | Entry: ${ent_p:,.4f} | Live: ${cur_p:,.4f} | PnL: {pnl_str}")
        print("-" * 65)

    def run(self):
        """Main execution loop managing concurrent positions and refilling open slots."""
        self.initialize()
        logger.info(f"Starting Multi-Crypto main loop with {self.max_positions} slots (Polling every {self.poll_interval}s)... Press Ctrl+C to stop.\n")

        # Initial launch fill: fill all slots simultaneously
        self.fill_all_open_slots()

        while True:
            try:
                # 1. Check Target Profit Goal
                if self.accumulated_profit >= self.target_profit:
                    print("\n" + "=" * 70)
                    print(f"🎯 TARGET PROFIT ACHIEVED! Total Gains: +${self.accumulated_profit:.2f} USDT across {self.cycles_completed} cycles!")
                    print("=" * 70)
                    break

                # 2. Evaluate & Manage All Open Crypto Positions
                for sym in list(self.positions.keys()):
                    pos = self.positions.get(sym)
                    if not pos:
                        continue

                    try:
                        cur_p = self.client.get_ticker_price(sym)
                        pos["current_price"] = cur_p
                        ent_p = pos["entry_price"]
                        pnl_pct = ((cur_p - ent_p) / ent_p) * 100.0 if ent_p > 0 else 0.0

                        if cur_p > pos.get("peak_price", cur_p):
                            pos["peak_price"] = cur_p

                        peak_p = pos.get("peak_price", cur_p)
                        peak_gain = ((peak_p - ent_p) / ent_p) * 100.0 if ent_p > 0 else 0.0
                        pullback = ((peak_p - cur_p) / peak_p) * 100.0 if peak_p > 0 else 0.0

                        # Fast Scalp Take Profit Conditions:
                        # 1. Instant Take Profit on Strong Surge
                        if pnl_pct >= (self.min_profit_pct * 1.8):
                            self.execute_sell(sym, cur_p, f"Instant Scalp Lock (+{pnl_pct:.2f}%)")
                        # 2. Trailing Profit Lock after Peak
                        elif peak_gain >= self.min_profit_pct and pullback >= self.trailing_pct and pnl_pct >= (self.min_profit_pct * 0.70):
                            self.execute_sell(sym, cur_p, f"Trailing Profit Lock (+{pnl_pct:.2f}%)")

                    except Exception as err:
                        logger.error(f"Error checking {sym}: {err}")

                # 3. Check Target Profit Goal again
                if self.accumulated_profit >= self.target_profit:
                    print("\n" + "=" * 70)
                    print(f"🎯 TARGET PROFIT ACHIEVED! Total Gains: +${self.accumulated_profit:.2f} USDT across {self.cycles_completed} cycles!")
                    print("=" * 70)
                    break

                # 4. Immediate Slot Refill (Reinvestment)
                if len(self.positions) < self.max_positions:
                    self.fill_all_open_slots()

                # 5. Print Visual Console Table
                self.print_status_table()

            except KeyboardInterrupt:
                logger.info("\nBot stopped by user. Exiting cleanly...")
                break
            except Exception as e:
                logger.error(f"Unexpected error in trading loop: {e}")

            time.sleep(self.poll_interval)

def parse_args():
    parser = argparse.ArgumentParser(description="Binance Multi-Crypto Divided Pieces Trading Bot")
    parser.add_argument("--budget", type=float, default=60.0, help="Total session budget in USDT (default: 60.0)")
    parser.add_argument("--pieces", type=int, default=1, help="Concurrent positions (default: 1 for single-capital focus)")
    parser.add_argument("--profit", type=float, default=5.0, help="Session target profit in USDT (default: 5.0)")
    parser.add_argument("--min-profit-pct", type=float, default=0.35, help="Min profit %% floor before take-profit (default: 0.35%%)")
    parser.add_argument("--dry-run", action="store_true", default=config.DRY_RUN, help="Run in simulation mode without real orders")
    parser.add_argument("--poll-interval", type=int, default=3, help="Polling interval in seconds (default: 3)")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    bot = BinanceBuySellBot(
        total_budget=args.budget,
        max_positions=args.pieces,
        target_profit=args.profit,
        min_profit_pct=args.min_profit_pct,
        dry_run=args.dry_run,
        poll_interval=args.poll_interval
    )
    bot.run()
