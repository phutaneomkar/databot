import threading
import time
import math
import json
import concurrent.futures
from pathlib import Path
from datetime import datetime
from collections import deque
from flask import Flask, render_template, jsonify, request

import config
from binance_client import BinanceTestnetClient
from strategy import TradingStrategy
from ai_trader import AITradingEngine, BackgroundMarketAnalyzer

app = Flask(__name__)

# State management
bot_thread = None
bot_running = False
stop_event = threading.Event()

# Local trades history persistence
TRADES_FILE = Path(__file__).parent / "trades_history.json"

def load_trade_history():
    if TRADES_FILE.exists():
        try:
            with open(TRADES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_trade_history(trades):
    try:
        with open(TRADES_FILE, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2)
    except Exception:
        pass

trade_history = load_trade_history()

def record_trade(symbol, side, price, qty, order_id=None, pnl_pct=None, pnl_usd=None, fee_usd=None, tax_usd=None, net_pnl_usd=None, trade_direction=None):
    trade = {
        "id": str(order_id) if order_id else str(int(time.time() * 1000)),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "side": side.upper(),
        "trade_direction": trade_direction or ("LONG" if "LONG" in side.upper() else "SHORT" if "SHORT" in side.upper() else ("BUY" if "BUY" in side.upper() else "SELL")),
        "type": "MARKET",
        "price": float(price),
        "quantity": float(qty),
        "total_usdt": round(float(price) * float(qty), 2),
        "status": "FILLED",
        "pnl_pct": round(float(pnl_pct), 2) if pnl_pct is not None else None,
        "pnl_usd": round(float(pnl_usd), 2) if pnl_usd is not None else None,
        "fee_usd": round(float(fee_usd), 3) if fee_usd is not None else 0.0,
        "tax_usd": round(float(tax_usd), 3) if tax_usd is not None else 0.0,
        "net_pnl_usd": round(float(net_pnl_usd), 2) if net_pnl_usd is not None else (round(float(pnl_usd), 2) if pnl_usd is not None else None),
        "mode": "SIMULATED" if state["dry_run"] else "LIVE TESTNET"
    }
    trade_history.insert(0, trade)
    save_trade_history(trade_history[:100])
    return trade

# In-memory bot tracking
state = {
    "in_position": False,
    "entry_price": 0.0,
    "position_qty": 0.0,
    "position_time": None,
    "current_price": 0.0,
    "pnl_pct": 0.0,
    "symbol": config.SYMBOL,
    "dry_run": config.DRY_RUN,
    "balances": {"USDT": {"free": 1000.0 if config.DRY_RUN else 0.0, "locked": 0.0}},
    "strategy": {
        "fast_ema": None,
        "slow_ema": None,
        "rsi": None,
        "signal": "HOLD",
        "reason": "Initializing..."
    }
}

logs = deque(maxlen=40)

def add_log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    logs.append(f"[{timestamp}] [{level}] {message}")

client = BinanceTestnetClient()
strategy = TradingStrategy(
    fast_period=config.FAST_MA_PERIOD,
    slow_period=config.SLOW_MA_PERIOD,
    rsi_period=config.RSI_PERIOD,
    rsi_oversold=config.RSI_OVERSOLD,
    rsi_overbought=config.RSI_OVERBOUGHT,
    stop_loss_pct=config.STOP_LOSS_PCT,
    take_profit_pct=config.TAKE_PROFIT_PCT
)
ai_engine = AITradingEngine(client=client, record_trade_callback=record_trade, log_callback=add_log)

# ── Background Market Analyzer ───────────────────────────────────────────────
# Starts immediately on app boot and scans all 500+ coins every 60 s so the
# AI engine always has a pre-warmed ranked list ready for instant entry.
bg_analyzer = BackgroundMarketAnalyzer(client=client, strategy=strategy, log_callback=add_log)
ai_engine.bg_analyzer = bg_analyzer   # Wire the cache into the AI engine
bg_analyzer.start()                   # Launch the always-on scanner thread
add_log("Background Market Analyzer started — continuously pre-ranking all 500+ coins.", "INFO")

def update_balances():
    """Safely updates account balances."""
    if client.api_key and client.api_secret and not state["dry_run"]:
        try:
            bals = client.get_account_balances()
            state["balances"] = bals
        except Exception as e:
            add_log(f"Balance check error: {e}", "WARN")
    elif state["dry_run"]:
        sim_free = max(100.0, float(state.get("balances", {}).get("USDT", {}).get("free", 1000.0)))
        state["balances"] = {"USDT": {"free": sim_free, "locked": 0.0}}

def bot_loop():
    """Background thread function executing algorithmic checks."""
    global bot_running
    add_log(f"Bot worker started (All-Coins Market Scanner). Polling every {config.POLL_INTERVAL_SECONDS}s.")

    while not stop_event.is_set():
        try:
            if state["in_position"]:
                # Monitor current open position
                current_sym = state["symbol"]
                candles = client.get_klines(current_sym, interval=config.TIMEFRAME, limit=50)
                candles_15m = client.get_klines(current_sym, interval="15m", limit=35)
                candles_1h = client.get_klines(current_sym, interval="1h", limit=35)
                if candles:
                    current_price = candles[-1]["close"]
                    state["current_price"] = current_price

                    signal, meta = strategy.analyze(
                        candles,
                        in_position=True,
                        entry_price=state["entry_price"],
                        candles_15m=candles_15m,
                        candles_1h=candles_1h
                    )
                    state["strategy"] = {
                        "fast_ema": meta.get("fast_ema"),
                        "slow_ema": meta.get("slow_ema"),
                        "rsi": meta.get("rsi"),
                        "signal": signal,
                        "reason": meta.get("reason"),
                        "mtf": meta.get("mtf", {})
                    }
                    if state["entry_price"] > 0:
                        state["pnl_pct"] = ((current_price - state["entry_price"]) / state["entry_price"]) * 100.0
                    else:
                        state["pnl_pct"] = 0.0

                    if signal == "SELL":
                        try:
                            filters = client.get_symbol_filters(current_sym)
                        except Exception:
                            filters = {"step_size": 0.0001, "min_qty": 0.0001}
                        qty = state["position_qty"] or client.calculate_valid_quantity(current_sym, target_usd=25.0)
                        qty = client.adjust_to_step_size(qty, filters.get("step_size", 0.00001))
                        add_log(f"SELL Signal Triggered on {current_sym}: {meta.get('reason')}", "ACTION")

                        entry = state["entry_price"]
                        pnl_pct = ((current_price - entry) / entry) * 100.0 if entry > 0 else 0.0
                        pnl_usd = (current_price - entry) * qty if entry > 0 else 0.0

                        if state["dry_run"]:
                            add_log(f"[SIMULATED] SELL {qty} {current_sym} @ ${current_price:,.2f} | PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})", "SUCCESS" if pnl_usd >= 0 else "WARN")
                            record_trade(current_sym, "SELL", current_price, qty, pnl_pct=pnl_pct, pnl_usd=pnl_usd)
                            state["in_position"] = False
                            state["entry_price"] = 0.0
                            state["position_qty"] = 0.0
                            state["position_time"] = None
                            state["pnl_pct"] = 0.0
                        else:
                            order = client.create_order(current_sym, "SELL", "MARKET", qty)
                            add_log(f"SELL Filled on Testnet! Order ID: {order.get('orderId')} | PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})", "SUCCESS" if pnl_usd >= 0 else "WARN")
                            record_trade(current_sym, "SELL", current_price, qty, order_id=order.get("orderId"), pnl_pct=pnl_pct, pnl_usd=pnl_usd)
                            state["in_position"] = False
                            state["entry_price"] = 0.0
                            state["position_qty"] = 0.0
                            state["position_time"] = None
                            state["pnl_pct"] = 0.0
                            update_balances()
            else:
                # No active position: scan ALL 500+ tradable coins across Binance for best buy opportunity
                candidates = [state["symbol"]]
                try:
                    tickers = client.get_24hr_tickers(quote_asset="USDT")
                    if tickers:
                        stablecoins = {"USDC", "BUSD", "FDUSD", "TUSD", "EUR", "DAI", "USDS", "PAXG", "AEUR"}
                        macro_pool = []
                        for t in tickers:
                            sym = t.get("symbol", "")
                            if not sym or any(s in sym for s in stablecoins):
                                continue
                            last_p = float(t.get("price", 0.0))
                            high_p = float(t.get("high", 0.0))
                            low_p = float(t.get("low", 0.0))
                            vol = float(t.get("quote_volume", 0.0))
                            chg = float(t.get("change_pct", 0.0))
                            if last_p <= 0.0 or vol < 15000.0 or high_p <= low_p:
                                continue
                            range_24h = high_p - low_p
                            amp_pct = (range_24h / low_p) * 100.0
                            vol_s = 30.0 if amp_pct >= 5.0 else (22.0 if amp_pct >= 3.0 else (12.0 if amp_pct >= 2.0 else -15.0))
                            mom_s = 28.0 if 0.5 <= chg <= 6.0 else (22.0 if -4.0 <= chg < 0.5 else 10.0)
                            pos_in_range = (last_p - low_p) / range_24h
                            range_s = 25.0 if 0.20 <= pos_in_range <= 0.65 else 15.0
                            liq_s = min(20.0, math.log10(max(1000.0, vol)) * 3.5)
                            macro_pool.append((vol_s + mom_s + range_s + liq_s, sym))
                        macro_pool.sort(key=lambda x: x[0], reverse=True)
                        candidates = [item[1] for item in macro_pool[:20]]
                except Exception:
                    pass

                best_sym = None
                best_price = None
                best_meta = None
                best_score = -1

                def eval_sym(sym):
                    try:
                        candles = client.get_klines(sym, interval=config.TIMEFRAME, limit=40)
                        candles_15m = client.get_klines(sym, interval="15m", limit=30)
                        candles_1h = client.get_klines(sym, interval="1h", limit=30)
                        if not candles or len(candles) < 25:
                            return None
                        cur_p = candles[-1]["close"]
                        sig, m = strategy.analyze(
                            candles,
                            in_position=False,
                            entry_price=0.0,
                            candles_15m=candles_15m,
                            candles_1h=candles_1h
                        )
                        rsi = m.get("rsi") or 50.0
                        return (sym, cur_p, sig, m, rsi)
                    except Exception:
                        return None

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    eval_results = list(executor.map(eval_sym, candidates))

                for res in eval_results:
                    if not res:
                        continue
                    sym, cur_p, sig, m, rsi = res
                    if sym == state["symbol"]:
                        state["current_price"] = cur_p
                        state["strategy"] = {
                            "fast_ema": m.get("fast_ema"),
                            "slow_ema": m.get("slow_ema"),
                            "rsi": rsi,
                            "signal": sig,
                            "reason": m.get("reason"),
                            "mtf": m.get("mtf", {})
                        }

                    if sig == "BUY":
                        score = 100 - rsi
                        if score > best_score:
                            best_score = score
                            best_sym = sym
                            best_price = cur_p
                            best_meta = m

                if best_sym and best_price and best_meta:
                    state["symbol"] = best_sym
                    state["current_price"] = best_price
                    state["strategy"] = {
                        "fast_ema": best_meta.get("fast_ema"),
                        "slow_ema": best_meta.get("slow_ema"),
                        "rsi": best_meta.get("rsi"),
                        "signal": "BUY",
                        "reason": best_meta.get("reason")
                    }
                    qty = client.calculate_valid_quantity(best_sym, target_usd=25.0)
                    add_log(f"BUY Signal on {best_sym} (Score: {best_score:.1f}): {best_meta.get('reason')}", "ACTION")

                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if state["dry_run"]:
                        add_log(f"[SIMULATED] BUY {qty} {best_sym} @ ${best_price:,.2f}", "SUCCESS")
                        state["in_position"] = True
                        state["entry_price"] = best_price
                        state["position_qty"] = qty
                        state["position_time"] = now_str
                        record_trade(best_sym, "BUY", best_price, qty)
                    else:
                        order = client.create_order(best_sym, "BUY", "MARKET", qty)
                        add_log(f"BUY Filled on Testnet for {best_sym}! Order ID: {order.get('orderId')}", "SUCCESS")
                        state["in_position"] = True
                        state["entry_price"] = best_price
                        state["position_qty"] = qty
                        state["position_time"] = now_str
                        record_trade(best_sym, "BUY", best_price, qty, order_id=order.get("orderId"))
                        update_balances()

        except Exception as e:
            add_log(f"Bot loop error: {e}", "ERROR")

        # Sleep in short increments to allow instant graceful stop
        for _ in range(config.POLL_INTERVAL_SECONDS):
            if stop_event.is_set():
                break
            time.sleep(1)

    add_log("Bot worker stopped.", "INFO")
    bot_running = False

# ==========================
# Web Routes & API Endpoints
# ==========================

_active_trades_cache = {"data": [], "timestamp": 0.0}

def get_all_active_trades(force_refresh: bool = False):
    """
    Universally discovers and reconciles all active open positions from:
    1. ai_engine.positions (active AI Trader multi-crypto basket slots)
    2. state["in_position"] (bot or single-pair manual trade)
    3. Unclosed BUY orders from trade_history (trades_history.json FIFO matching)
    4. Real account wallet balances (live testnet holdings)
    5. Open limit orders from Binance Testnet

    Uses batch price caching and in-memory TTL caching to execute in milliseconds.
    """
    global _active_trades_cache
    now = time.time()
    if not force_refresh and (now - _active_trades_cache["timestamp"] < 3.5):
        return _active_trades_cache["data"]

    active_trades = []
    seen_symbols = set()

    # Pre-fetch all prices in a single fast batch HTTP call (or in-memory cache)
    try:
        price_map = client.get_all_ticker_prices(max_age_seconds=4.0)
    except Exception:
        price_map = {}

    def get_price(s: str, default: float = 0.0) -> float:
        sym_u = s.upper()
        if sym_u in price_map:
            return price_map[sym_u]
        try:
            return client.get_ticker_price(sym_u, max_age_seconds=3.0)
        except Exception:
            return default

    # 1. AI Trader Multi-Crypto Active Positions
    if ai_engine and ai_engine.positions:
        for sym, pos in list(ai_engine.positions.items()):
            cur_p = get_price(sym, pos.get("current_price", pos.get("entry_price", 0.0)))
            ent_p = pos.get("entry_price", cur_p)
            q = float(pos.get("quantity", 0.0))
            u_pct = ((cur_p - ent_p) / ent_p * 100.0) if ent_p > 0 else 0.0
            u_usd = (cur_p - ent_p) * q if ent_p > 0 else 0.0
            active_trades.append({
                "symbol": sym,
                "side": "BUY",
                "entry_price": round(ent_p, 4 if ent_p < 10 else 2),
                "current_price": round(cur_p, 4 if cur_p < 10 else 2),
                "quantity": q,
                "total_value": round(cur_p * q, 2),
                "unrealized_pnl_pct": round(u_pct, 2),
                "unrealized_pnl_usd": round(u_usd, 2),
                "status": f"AI {pos.get('status', 'ACTIVE')}",
                "time": pos.get("entry_time", "-"),
                "order_id": str(pos.get("order_id", "-"))
            })
            seen_symbols.add(sym)

    # 2. Single-pair bot / manual trade in state
    if state["in_position"] and state["entry_price"] > 0 and state["symbol"] not in seen_symbols:
        sym = state["symbol"]
        cur_p = get_price(sym, state["current_price"] or state["entry_price"])
        state["current_price"] = cur_p
        ent_p = state["entry_price"]
        q = float(state["position_qty"])
        u_pct = ((cur_p - ent_p) / ent_p * 100.0) if ent_p > 0 else 0.0
        u_usd = (cur_p - ent_p) * q if ent_p > 0 else 0.0
        active_trades.append({
            "symbol": sym,
            "side": "BUY",
            "entry_price": round(ent_p, 4 if ent_p < 10 else 2),
            "current_price": round(cur_p, 4 if cur_p < 10 else 2),
            "quantity": q,
            "total_value": round(cur_p * q, 2),
            "unrealized_pnl_pct": round(u_pct, 2),
            "unrealized_pnl_usd": round(u_usd, 2),
            "status": "OPEN",
            "time": state.get("position_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "order_id": "-"
        })
        seen_symbols.add(sym)

    # 3. Unclosed BUY orders from trade_history (FIFO matching)
    history_sorted = sorted(trade_history, key=lambda x: str(x.get("timestamp", "")))
    symbol_buys = {}
    for t in history_sorted:
        sym = t.get("symbol")
        side = t.get("side", "").upper()
        qty = float(t.get("quantity", 0.0))
        price = float(t.get("price", 0.0))
        ts = t.get("timestamp", "-")
        oid = t.get("id", "-")
        mode = t.get("mode", "LIVE TESTNET")

        if sym not in symbol_buys:
            symbol_buys[sym] = []

        if side == "BUY" and qty > 0:
            symbol_buys[sym].append({
                "price": price,
                "quantity": qty,
                "time": ts,
                "id": oid,
                "mode": mode
            })
        elif side == "SELL" and qty > 0:
            rem_sell = qty
            while symbol_buys[sym] and rem_sell > 0:
                buy_item = symbol_buys[sym][0]
                if buy_item["quantity"] <= rem_sell + 1e-6:
                    rem_sell -= buy_item["quantity"]
                    symbol_buys[sym].pop(0)
                else:
                    buy_item["quantity"] = round(buy_item["quantity"] - rem_sell, 6)
                    rem_sell = 0.0

    # 4. Check real wallet balances (if available) to reconcile exact holdings
    real_wallet_bals = state.get("balances", {})
    if client.api_key and client.api_secret and not state["dry_run"]:
        try:
            real_wallet_bals = client.get_account_balances()
            state["balances"] = real_wallet_bals
        except Exception:
            pass

    for sym, buys in symbol_buys.items():
        if sym in seen_symbols or not buys:
            continue
        tot_qty = sum(b["quantity"] for b in buys)
        if tot_qty <= 0.00001:
            continue

        base_asset = sym.replace("USDT", "")
        # If live wallet balance is available and contains the asset, reconcile quantity
        if real_wallet_bals and base_asset in real_wallet_bals:
            w_data = real_wallet_bals.get(base_asset, {})
            w_tot = float(w_data.get("free", 0.0)) + float(w_data.get("locked", 0.0))
            if w_tot > 0.00001:
                tot_qty = min(tot_qty, w_tot)

        weighted_price = sum(b["price"] * b["quantity"] for b in buys) / sum(b["quantity"] for b in buys)
        latest_time = buys[-1]["time"]
        latest_oid = buys[-1]["id"]

        cur_p = get_price(sym, weighted_price)

        val = round(tot_qty * cur_p, 2)
        if val >= 0.50:  # Minimum 50 cents to filter dust
            u_pct = ((cur_p - weighted_price) / weighted_price * 100.0) if weighted_price > 0 else 0.0
            u_usd = (cur_p - weighted_price) * tot_qty if weighted_price > 0 else 0.0
            active_trades.append({
                "symbol": sym,
                "side": "BUY",
                "entry_price": round(weighted_price, 4 if weighted_price < 10 else 2),
                "current_price": round(cur_p, 4 if cur_p < 10 else 2),
                "quantity": round(tot_qty, 6),
                "total_value": val,
                "unrealized_pnl_pct": round(u_pct, 2),
                "unrealized_pnl_usd": round(u_usd, 2),
                "status": "OPEN",
                "time": latest_time,
                "order_id": str(latest_oid)
            })
            seen_symbols.add(sym)

    # 4. Active open limit orders from Binance Testnet
    if client.api_key and client.api_secret and not state["dry_run"]:
        try:
            open_orders = client.get_open_orders()
            for o in open_orders:
                price = float(o.get("price", 0.0))
                qty = float(o.get("origQty", 0.0)) - float(o.get("executedQty", 0.0))
                if qty > 0:
                    active_trades.append({
                        "symbol": o.get("symbol"),
                        "side": o.get("side"),
                        "entry_price": price,
                        "current_price": price,
                        "quantity": qty,
                        "total_value": round(price * qty, 2),
                        "unrealized_pnl_pct": 0.0,
                        "unrealized_pnl_usd": 0.0,
                        "status": f"OPEN ({o.get('type')})",
                        "time": datetime.fromtimestamp(o.get("time", 0) / 1000.0).strftime("%Y-%m-%d %H:%M:%S") if o.get("time") else "-",
                        "order_id": str(o.get("orderId"))
                    })
        except Exception:
            pass

    _active_trades_cache = {"data": active_trades, "timestamp": now}
    return active_trades

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def get_status():
    all_active = get_all_active_trades()
    active_count = len(all_active)

    # If bot is not running, refresh price for the UI using cached price
    if not bot_running:
        try:
            state["current_price"] = client.get_ticker_price(state["symbol"], max_age_seconds=3.0)
        except Exception:
            pass

    # Reconcile position state for active symbol
    cur_sym = state["symbol"]
    matching_pos = [t for t in all_active if t["symbol"] == cur_sym and t["side"] == "BUY"]
    if matching_pos:
        pos = matching_pos[0]
        state["in_position"] = True
        state["entry_price"] = pos["entry_price"]
        state["position_qty"] = pos["quantity"]
        state["position_time"] = pos["time"]
        if pos["entry_price"] > 0 and state["current_price"] > 0:
            state["pnl_pct"] = ((state["current_price"] - pos["entry_price"]) / pos["entry_price"]) * 100.0
    else:
        state["in_position"] = False
        state["entry_price"] = 0.0
        state["position_qty"] = 0.0
        state["pnl_pct"] = 0.0

    return jsonify({
        "bot_running": bot_running,
        "symbol": state["symbol"],
        "dry_run": state["dry_run"],
        "current_price": state["current_price"],
        "in_position": state["in_position"],
        "entry_price": state["entry_price"],
        "position_qty": state["position_qty"],
        "pnl_pct": state["pnl_pct"],
        "active_trades_count": active_count,
        "balances": state["balances"],
        "strategy": state["strategy"],
        "logs": list(logs)
    })

@app.route("/api/candles")
def get_candles():
    try:
        raw_candles = client.get_klines(state["symbol"], interval=config.TIMEFRAME, limit=35)
        closes = [c["close"] for c in raw_candles]
        fast_emas = strategy.calculate_ema(closes, config.FAST_MA_PERIOD)
        slow_emas = strategy.calculate_ema(closes, config.SLOW_MA_PERIOD)

        # Pad EMAs to align with candle indices
        fast_padded = [None] * (len(closes) - len(fast_emas)) + fast_emas
        slow_padded = [None] * (len(closes) - len(slow_emas)) + slow_emas

        formatted = [{
            "time": c["open_time"],
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"]
        } for c in raw_candles]

        return jsonify({
            "candles": formatted,
            "fast_ema": fast_padded,
            "slow_ema": slow_padded
        })
    except Exception as e:
        return jsonify({"error": str(e), "candles": []}), 500

@app.route("/api/tickers")
def get_tickers():
    """Returns 24h ticker statistics for all USDT trading pairs."""
    try:
        quote = request.args.get("quote", "USDT")
        tickers = client.get_24hr_tickers(quote_asset=quote)
        return jsonify({"tickers": tickers})
    except Exception as e:
        return jsonify({"error": str(e), "tickers": []}), 500

@app.route("/api/portfolio")
def get_portfolio():
    """
    Returns complete portfolio data:
    1. Wallet balances, individual asset breakdown & USD net worth
    2. Active open trades (from Bot state & AI Engine Multi-Crypto Basket)
    3. Past recorded trading history & orders
    """
    # 0. Fetch Wallet Balances & Detailed Net Worth
    wallet_summary = {"total_value_usdt": 0.0, "assets": []}
    balances = {}

    if client.api_key and client.api_secret and not state["dry_run"]:
        try:
            balances = client.get_account_balances()
            state["balances"] = balances
            wallet_summary = client.get_detailed_portfolio()
        except Exception as e:
            add_log(f"Portfolio balance error: {e}", "WARN")
            balances = state.get("balances", {})
    elif state["dry_run"]:
        # Simulated balances in dry run mode
        simulated_usdt = max(100.0, float(state.get("balances", {}).get("USDT", {}).get("free", 1000.0)))
        balances = {"USDT": {"free": simulated_usdt, "locked": 0.0}}
        wallet_summary = {
            "total_value_usdt": simulated_usdt,
            "assets": [{
                "asset": "USDT",
                "free": simulated_usdt,
                "locked": 0.0,
                "total": simulated_usdt,
                "price_usdt": 1.0,
                "value_usdt": simulated_usdt,
                "allocation_pct": 100.0
            }]
        }
    else:
        balances = state.get("balances", {})

    # Fallback to compute assets list if get_detailed_portfolio returned empty but balances exist
    if balances and not wallet_summary.get("assets"):
        assets_list = []
        tot_val = 0.0
        for asset, b in balances.items():
            tot = float(b.get("free", 0.0)) + float(b.get("locked", 0.0))
            if tot > 0:
                p_usd = 1.0
                if asset.upper() not in ("USDT", "BUSD", "USDC", "FDUSD"):
                    try:
                        p_usd = client.get_ticker_price(f"{asset.upper()}USDT")
                    except Exception:
                        p_usd = 1.0
                val = tot * p_usd
                if val < 0.50 and asset.upper() != "USDT":
                    continue
                tot_val += val
                assets_list.append({
                    "asset": asset,
                    "free": float(b.get("free", 0.0)),
                    "locked": float(b.get("locked", 0.0)),
                    "total": tot,
                    "price_usdt": p_usd,
                    "value_usdt": round(val, 2),
                    "allocation_pct": 0.0
                })
        for a in assets_list:
            a["allocation_pct"] = round((a["value_usdt"] / tot_val * 100.0), 1) if tot_val > 0 else 0.0
        assets_list.sort(key=lambda x: x["value_usdt"], reverse=True)
        wallet_summary = {"total_value_usdt": round(tot_val, 2), "assets": assets_list}

    # 1. Active Open Trades (aggregated universally across all sources)
    active_trades = get_all_active_trades()

    # 2. Past Trading History
    past_trades = list(trade_history)

    if client.api_key and client.api_secret and not state["dry_run"]:
        try:
            exchange_orders = client.get_all_orders(state["symbol"], limit=30)
            existing_ids = {str(t.get("id")) for t in past_trades}
            for o in exchange_orders:
                oid = str(o.get("orderId"))
                if oid not in existing_ids and o.get("status") in ("FILLED", "CANCELED"):
                    exec_qty = float(o.get("executedQty", 0.0))
                    quote_qty = float(o.get("cummulativeQuoteQty", 0.0))
                    avg_price = (quote_qty / exec_qty) if exec_qty > 0 else float(o.get("price", 0.0))
                    past_trades.append({
                        "id": oid,
                        "timestamp": datetime.fromtimestamp(o.get("time", 0) / 1000.0).strftime("%Y-%m-%d %H:%M:%S") if o.get("time") else "-",
                        "symbol": o.get("symbol"),
                        "side": o.get("side"),
                        "type": o.get("type"),
                        "price": round(avg_price, 2),
                        "quantity": exec_qty,
                        "total_usdt": round(quote_qty, 2),
                        "status": o.get("status"),
                        "pnl_pct": None,
                        "pnl_usd": None,
                        "mode": "LIVE TESTNET"
                    })
        except Exception:
            pass

    # Calculate realized PnL for any SELL orders missing it by matching chronological preceding BUY
    past_trades.sort(key=lambda x: str(x.get("timestamp", "")))
    symbol_last_buy = {}
    for t in past_trades:
        sym = t.get("symbol")
        side = t.get("side")
        price = float(t.get("price", 0.0))
        qty = float(t.get("quantity", 0.0))
        if side == "BUY" and price > 0:
            symbol_last_buy[sym] = price
        elif side == "SELL":
            if (t.get("pnl_pct") is None or t.get("pnl_usd") is None) and sym in symbol_last_buy:
                buy_p = symbol_last_buy[sym]
                if buy_p > 0 and price > 0:
                    t["pnl_pct"] = round(((price - buy_p) / buy_p) * 100.0, 2)
                    t["pnl_usd"] = round((price - buy_p) * qty, 2)

    past_trades.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)

    return jsonify({
        "wallet_summary": wallet_summary,
        "balances": balances,
        "active_trades": active_trades,
        "past_trades": past_trades,
        "dry_run": state["dry_run"],
        "has_credentials": bool(client.api_key and client.api_secret)
    })

@app.route("/api/switch-symbol", methods=["POST"])
def switch_symbol():
    """Dynamically switches the active trading pair."""
    data = request.get_json() or {}
    new_sym = data.get("symbol", "").upper().strip()
    if not new_sym:
        return jsonify({"error": "Symbol is required"}), 400

    old_sym = state["symbol"]
    if old_sym != new_sym:
        state["symbol"] = new_sym
        state["strategy"] = {
            "fast_ema": None,
            "slow_ema": None,
            "rsi": None,
            "signal": "HOLD",
            "reason": f"Target switched to {new_sym}"
        }
        try:
            state["current_price"] = client.get_ticker_price(new_sym)
        except Exception:
            pass

        # Reconcile position status for new symbol
        matching_trades = [t for t in get_all_active_trades() if t["symbol"] == new_sym and t["side"] == "BUY"]
        if matching_trades:
            pos = matching_trades[0]
            state["in_position"] = True
            state["entry_price"] = pos["entry_price"]
            state["position_qty"] = pos["quantity"]
            state["position_time"] = pos["time"]
            if pos["entry_price"] > 0 and state["current_price"] > 0:
                state["pnl_pct"] = ((state["current_price"] - pos["entry_price"]) / pos["entry_price"]) * 100.0
        else:
            state["in_position"] = False
            state["entry_price"] = 0.0
            state["position_qty"] = 0.0
            state["pnl_pct"] = 0.0

        add_log(f"Trading target switched from {old_sym} to {new_sym}", "CONFIG")
        return jsonify({"status": "switched", "symbol": new_sym})
    return jsonify({"status": "unchanged", "symbol": old_sym})

@app.route("/api/bot/start", methods=["POST"])
def start_bot():
    global bot_thread, bot_running
    if not bot_running:
        stop_event.clear()
        bot_running = True
        bot_thread = threading.Thread(target=bot_loop, daemon=True)
        bot_thread.start()
        add_log("User started the automated bot.", "INFO")
        return jsonify({"status": "started"})
    return jsonify({"status": "already_running"})

@app.route("/api/bot/stop", methods=["POST"])
def stop_bot():
    global bot_running
    if bot_running:
        stop_event.set()
        add_log("User signaled bot stop...", "INFO")
        return jsonify({"status": "stopping"})
    return jsonify({"status": "already_stopped"})

@app.route("/api/config/toggle-dry-run", methods=["POST"])
def toggle_dry_run():
    state["dry_run"] = not state["dry_run"]
    mode_str = "DRY RUN (Simulated)" if state["dry_run"] else "LIVE TESTNET"
    add_log(f"Execution mode switched to: {mode_str}", "WARN")
    return jsonify({"dry_run": state["dry_run"]})

@app.route("/api/trade/manual", methods=["POST"])
def manual_trade():
    data = request.get_json() or {}
    side = data.get("side", "BUY").upper()
    trade_symbol = data.get("symbol", state["symbol"]).upper().strip()
    qty_param = data.get("quantity")
    try:
        target_usd = max(10.0, float(data.get("target_usd", 25.0)))
    except (ValueError, TypeError):
        target_usd = 25.0

    try:
        try:
            filters = client.get_symbol_filters(trade_symbol)
        except Exception:
            filters = {"min_qty": 0.00001, "step_size": 0.00001}

        try:
            current_price = client.get_ticker_price(trade_symbol)
        except Exception:
            current_price = 1.0
            for t in trade_history:
                if t.get("symbol") == trade_symbol and t.get("price"):
                    current_price = float(t.get("price"))
                    break

        base_asset = trade_symbol.replace("USDT", "")

        # 1. Determine safe valid quantity & check balances
        if side == "BUY":
            if qty_param is not None and float(qty_param) > 0:
                qty = float(qty_param)
            else:
                qty = client.calculate_valid_quantity(trade_symbol, target_usd=target_usd)
            qty = client.adjust_to_step_size(qty, filters.get("step_size", 0.00001))
            if not state["dry_run"] and client.api_key:
                free_usdt = client.get_asset_balance("USDT")
                needed_usdt = qty * current_price
                if free_usdt < needed_usdt:
                    return jsonify({
                        "success": False,
                        "message": f"Insufficient USDT balance! Need ${needed_usdt:.2f} USDT, but have ${free_usdt:.2f}."
                    }), 400
        else: # SELL
            min_qty = filters.get("min_qty", 0.0001)
            if not state["dry_run"] and client.api_key:
                free_base = client.get_asset_balance(base_asset)
                if free_base < min_qty:
                    return jsonify({
                        "success": False,
                        "message": f"You don't own any {base_asset} to sell! Your free balance is {free_base} {base_asset}. Please BUY some {base_asset} first."
                    }), 400

                if qty_param is not None and float(qty_param) > 0:
                    target_qty = float(qty_param)
                elif state["in_position"] and state["symbol"] == trade_symbol and state["position_qty"] > 0:
                    target_qty = state["position_qty"]
                else:
                    target_qty = client.calculate_valid_quantity(trade_symbol, target_usd=target_usd)

                qty = min(target_qty, free_base)
                qty = client.adjust_to_step_size(qty, filters.get("step_size", 0.00001))
                if qty < min_qty:
                    return jsonify({
                        "success": False,
                        "message": f"Available {base_asset} ({free_base}) is below minimum lot size ({min_qty})."
                    }), 400
            else:
                if qty_param is not None and float(qty_param) > 0:
                    target_qty = float(qty_param)
                elif state["in_position"] and state["symbol"] == trade_symbol and state["position_qty"] > 0:
                    target_qty = state["position_qty"]
                else:
                    target_qty = client.calculate_valid_quantity(trade_symbol, target_usd=target_usd)
                qty = client.adjust_to_step_size(target_qty, filters.get("step_size", 0.00001))

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Find entry price from recent BUY in trade_history or ai_engine
        entry = 0.0
        if trade_symbol in ai_engine.positions:
            entry = float(ai_engine.positions[trade_symbol].get("entry_price", 0.0))
        elif state["in_position"] and state["symbol"] == trade_symbol:
            entry = state["entry_price"]
        if entry <= 0:
            for t in trade_history:
                if t.get("symbol") == trade_symbol and t.get("side") == "BUY":
                    entry = float(t.get("price", 0.0))
                    break

        if state["dry_run"]:
            add_log(f"[MANUAL SIMULATED] {side} {qty} {trade_symbol} @ ${current_price:,.2f}", "SUCCESS")
            if side == "BUY":
                if trade_symbol == state["symbol"]:
                    state["in_position"] = True
                    state["entry_price"] = current_price
                    state["position_qty"] = qty
                    state["position_time"] = now_str
                record_trade(trade_symbol, "BUY", current_price, qty)
            else:
                if entry > 0:
                    pnl_pct = ((current_price - entry) / entry) * 100.0
                    pnl_usd = (current_price - entry) * qty
                else:
                    pnl_pct = 0.0
                    pnl_usd = 0.0

                record_trade(trade_symbol, "SELL", current_price, qty, pnl_pct=pnl_pct, pnl_usd=pnl_usd)
                if trade_symbol == state["symbol"]:
                    state["in_position"] = False
                    state["entry_price"] = 0.0
                    state["position_qty"] = 0.0
                    state["position_time"] = None
                    state["pnl_pct"] = 0.0
                if trade_symbol in ai_engine.positions:
                    del ai_engine.positions[trade_symbol]
                    from ai_trader import save_ai_positions
                    save_ai_positions(ai_engine.positions)
            _active_trades_cache["timestamp"] = 0.0
            return jsonify({"success": True, "message": f"Simulated {side} {qty} {trade_symbol} executed successfully."})

        # Real testnet order
        order = client.create_order(trade_symbol, side, "MARKET", qty)
        order_id = order.get('orderId')
        if side == "BUY":
            add_log(f"[MANUAL LIVE] {side} {qty} {trade_symbol} Filled! Order ID: {order_id}", "SUCCESS")
            if trade_symbol == state["symbol"]:
                state["in_position"] = True
                state["entry_price"] = current_price
                state["position_qty"] = qty
                state["position_time"] = now_str
            record_trade(trade_symbol, "BUY", current_price, qty, order_id=order_id)
        else:
            if entry > 0:
                pnl_pct = ((current_price - entry) / entry) * 100.0
                pnl_usd = (current_price - entry) * qty
            else:
                pnl_pct = 0.0
                pnl_usd = 0.0

            add_log(f"[MANUAL LIVE] {side} {qty} {trade_symbol} Filled! Order ID: {order_id} | PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})", "SUCCESS" if pnl_usd >= 0 else "WARN")
            record_trade(trade_symbol, "SELL", current_price, qty, order_id=order_id, pnl_pct=pnl_pct, pnl_usd=pnl_usd)
            if trade_symbol == state["symbol"]:
                state["in_position"] = False
                state["entry_price"] = 0.0
                state["position_qty"] = 0.0
                state["position_time"] = None
                state["pnl_pct"] = 0.0
            if trade_symbol in ai_engine.positions:
                del ai_engine.positions[trade_symbol]
                from ai_trader import save_ai_positions
                save_ai_positions(ai_engine.positions)

        _active_trades_cache["timestamp"] = 0.0
        update_balances()
        return jsonify({"success": True, "message": f"Live {side} {qty} {trade_symbol} filled on Testnet! Order #{order_id}", "order": order})
    except Exception as e:
        add_log(f"Manual {side} failed: {e}", "ERROR")
        return jsonify({"success": False, "message": str(e)}), 400

@app.route("/api/trades/close-all", methods=["POST"])
def close_all_trades_api():
    """
    Closes all active open positions and cancels all open limit orders.
    Executes market SELL orders for all held assets and reconciles trade history.
    """
    try:
        import close_all_trades
        close_all_trades.close_all()
        add_log("User executed CLOSE ALL: canceled all open orders and market sold all active positions.", "WARN")
        update_balances()
        return jsonify({
            "success": True,
            "message": "All active open orders and crypto positions have been closed and liquidated to USDT!"
        })
    except Exception as e:
        add_log(f"Close All failed: {e}", "ERROR")
        return jsonify({"success": False, "message": str(e)}), 500

# ==========================
# AI Smart Trader Endpoints
# ==========================

@app.route("/api/ai/status")
def get_ai_status():
    return jsonify(ai_engine.get_status_payload())

@app.route("/api/ai/market-radar")
def get_market_radar():
    """
    Returns the latest pre-analyzed, ranked market opportunities from the
    always-on BackgroundMarketAnalyzer — ready instantly even before an AI
    session is launched.
    """
    return jsonify(bg_analyzer.get_radar_payload())

@app.route("/api/ai/start", methods=["POST"])
def start_ai_trader():
    data = request.get_json() or {}
    try:
        allocated = float(data.get("allocated_amount", 60.0))
    except (ValueError, TypeError):
        allocated = 60.0

    try:
        target = float(data.get("target_profit", 5.0))
    except (ValueError, TypeError):
        target = 5.0

    try:
        min_pnl = float(data.get("min_profit_pct", 0.35))
    except (ValueError, TypeError):
        min_pnl = 0.35

    immediate_entry = bool(data.get("immediate_entry", True))
    # Single-capital focus: strictly 1 sniper trade at a time
    max_positions = 1

    try:
        trailing_pct = float(data.get("trailing_pct", 0.12))
    except (ValueError, TypeError):
        trailing_pct = 0.12

    try:
        leverage = int(data.get("leverage", getattr(config, "DEFAULT_LEVERAGE", 5)))
    except (ValueError, TypeError):
        leverage = 5

    try:
        fee_rate = float(data.get("fee_rate_pct", getattr(config, "BINANCE_FEE_PCT", 0.05)))
    except (ValueError, TypeError):
        fee_rate = 0.05

    try:
        tax_rate = float(data.get("tax_rate_pct", getattr(config, "TAX_RATE_PCT", 30.0)))
    except (ValueError, TypeError):
        tax_rate = 30.0

    try:
        tds_rate = float(data.get("tds_pct", getattr(config, "TDS_PCT", 1.0)))
    except (ValueError, TypeError):
        tds_rate = 1.0

    include_taxes = bool(data.get("include_taxes", getattr(config, "INCLUDE_TAXES", True)))

    market_type = str(data.get("market_type", getattr(config, "MARKET_TYPE", "FUTURES"))).upper()

    success = ai_engine.start(
        allocated_amount=allocated,
        target_profit=target,
        min_profit_pct=min_pnl,
        dry_run=state["dry_run"],
        immediate_entry=immediate_entry,
        max_positions=1,
        trailing_pct=trailing_pct,
        leverage=leverage,
        market_type=market_type,
        fee_rate_pct=fee_rate,
        tax_rate_pct=tax_rate,
        tds_pct=tds_rate,
        include_taxes=include_taxes
    )
    if success:
        total_power = allocated * ai_engine.leverage
        return jsonify({
            "status": "started",
            "message": f"AI Futures Sniper started: 100% capital (${allocated:.2f} USDT @ {ai_engine.leverage}x = ${total_power:.2f} buying power) focused on #1 setup | Fees: {fee_rate*2:.2f}% round-trip | Tax: {tax_rate:.0f}% + {tds_rate:.1f}% TDS | Target Net: +${target:.2f} profit."
        })
    return jsonify({"status": "already_running", "message": "AI Trader session is already active."}), 400

@app.route("/api/ai/stop", methods=["POST"])
def stop_ai_trader():
    ai_engine.stop(reason="User stopped AI trading session")
    return jsonify({"status": "stopped", "message": "AI Trader session stopped."})

@app.route("/api/ai/force-open", methods=["POST"])
def force_ai_open():
    data = request.get_json() or {}
    symbol = data.get("symbol")
    side = data.get("side", "LONG").upper()
    res = ai_engine.force_open(symbol=symbol, side=side)
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code

@app.route("/api/ai/force-buy", methods=["POST"])
def force_ai_buy():
    data = request.get_json() or {}
    symbol = data.get("symbol")
    res = ai_engine.force_buy(symbol=symbol)
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code

@app.route("/api/ai/close-position", methods=["POST"])
@app.route("/api/ai/close-piece", methods=["POST"])
@app.route("/api/ai/sell-piece", methods=["POST"])
def sell_ai_piece():
    data = request.get_json() or {}
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"success": False, "message": "Symbol is required"}), 400
    res = ai_engine.close_piece(symbol, reason="Manual User Exit")
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code

@app.route("/api/ai/fill-slots", methods=["POST"])
def fill_ai_slots():
    filled = ai_engine.fill_open_slots()
    return jsonify({
        "success": True,
        "filled": filled,
        "message": f"Scanned market and filled {filled} open crypto piece slot(s)!"
    })

@app.route("/api/ai/adopt-position", methods=["POST"])
def adopt_ai_position():
    res = ai_engine.adopt_existing_btc()
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code

if __name__ == "__main__":
    port = 5000
    host = "0.0.0.0"
    print("=" * 60, flush=True)
    print("  🚀 BINANCE TRADING DASHBOARD (PRODUCTION WSGI SERVER)", flush=True)
    print(f"  * Running on http://127.0.0.1:{port}", flush=True)
    print(f"  * Running on http://localhost:{port}", flush=True)
    print("  * Production WSGI multi-threading active (8 worker threads)", flush=True)
    print("  * Press CTRL+C to quit", flush=True)
    print("=" * 60, flush=True)
    add_log(f"Production WSGI server initialized on http://localhost:{port}")

    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except (ImportError, OSError):
        app.run(host=host, port=port, debug=False, threaded=True)

