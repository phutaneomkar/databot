import sys
import time
from datetime import datetime
from binance_client import BinanceTestnetClient
from dashboard import record_trade, get_all_active_trades, state, ai_engine, save_trade_history, trade_history
from ai_trader import save_ai_positions

def close_all():
    print("=" * 60)
    print("STARTING FULL ORDER & POSITION CLOSURE ON BINANCE TESTNET")
    print("=" * 60)

    client = BinanceTestnetClient()

    # 1. Cancel all active open limit/stop orders on Binance Testnet
    print("\n[Step 1] Checking and canceling any active open orders...")
    try:
        open_orders = client.get_open_orders()
        print(f"Found {len(open_orders)} open order(s) on Binance Testnet.")
        for o in open_orders:
            sym = o.get("symbol")
            oid = o.get("orderId")
            try:
                client.cancel_order(sym, oid)
                print(f"  -> Canceled open order #{oid} on {sym} ({o.get('side')} {o.get('origQty')})")
            except Exception as e:
                print(f"  -> Failed to cancel order #{oid} on {sym}: {e}")
    except Exception as e:
        print(f"Error checking open orders: {e}")

    # 2. Get current account balances
    print("\n[Step 2] Fetching wallet balances...")
    try:
        balances = client.get_account_balances()
        print(f"Non-zero wallet assets: {list(balances.keys())}")
    except Exception as e:
        print(f"Error fetching balances: {e}")
        balances = {}

    # 3. Retrieve all active open trades
    print("\n[Step 3] Discovering active open positions...")
    active_trades = get_all_active_trades(force_refresh=True)
    print(f"Found {len(active_trades)} active trade position(s) to close.")

    closed_count = 0
    for t in active_trades:
        sym = t.get("symbol", "").upper()
        target_qty = float(t.get("quantity", 0.0))
        base_asset = sym.replace("USDT", "")
        entry_price = float(t.get("entry_price", 0.0))

        print(f"\nClosing position: {sym} (Qty: {target_qty}, Entry: ${entry_price})")

        # Fetch current price
        try:
            cur_price = client.get_ticker_price(sym)
        except Exception:
            cur_price = float(t.get("current_price", entry_price))

        # Check real wallet balance
        wallet_free = balances.get(base_asset, {}).get("free", 0.0)
        print(f"  -> Available free {base_asset} in wallet: {wallet_free}")

        order_id = None
        executed_qty = target_qty

        if wallet_free > 0 and client.api_key and client.api_secret:
            try:
                filters = client.get_symbol_filters(sym)
                step_size = filters.get("step_size", 0.0001)
                min_qty = filters.get("min_qty", 0.0001)
                min_notional = filters.get("min_notional", 5.0)

                sell_qty = min(target_qty, wallet_free)
                sell_qty = client.adjust_to_step_size(sell_qty, step_size)

                notional = sell_qty * cur_price
                if sell_qty >= min_qty and notional >= min_notional:
                    print(f"  -> Submitting live MARKET SELL for {sell_qty} {sym} (~${notional:.2f} USDT)...")
                    order = client.create_order(sym, "SELL", "MARKET", sell_qty)
                    order_id = order.get("orderId")
                    executed_qty = sell_qty
                    print(f"  -> [SUCCESS] Sold on Binance Testnet! Order ID: {order_id}")
                else:
                    print(f"  -> Warning: Qty {sell_qty} or Notional ${notional:.2f} below minimums (min_qty={min_qty}, min_notional={min_notional}). Exiting in state.")
            except Exception as e:
                print(f"  -> Testnet sell error for {sym}: {e}. Recording closure to clear state.")

        # Calculate realized PnL
        pnl_pct = ((cur_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
        pnl_usd = (cur_price - entry_price) * executed_qty if entry_price > 0 else 0.0

        # Record SELL in trade history to reconcile FIFO queue
        record_trade(
            symbol=sym,
            side="SELL",
            price=cur_price,
            qty=executed_qty,
            order_id=order_id,
            pnl_pct=pnl_pct,
            pnl_usd=pnl_usd
        )
        print(f"  -> Recorded SELL trade: {executed_qty} {sym} @ ${cur_price} | PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})")
        closed_count += 1

    # 4. Check any other non-USDT coins with balance > min_notional and liquidate
    print("\n[Step 4] Checking for any other remaining non-USDT wallet assets...")
    try:
        balances = client.get_account_balances()
        for asset, b in balances.items():
            if asset.upper() in ("USDT", "BUSD", "USDC", "FDUSD"):
                continue
            free_qty = float(b.get("free", 0.0))
            if free_qty <= 0:
                continue
            pair = f"{asset.upper()}USDT"
            try:
                filters = client.get_symbol_filters(pair)
                cur_price = client.get_ticker_price(pair)
                step_size = filters.get("step_size", 0.0001)
                min_qty = filters.get("min_qty", 0.0001)
                min_notional = filters.get("min_notional", 5.0)

                sell_qty = client.adjust_to_step_size(free_qty, step_size)
                notional = sell_qty * cur_price

                if sell_qty >= min_qty and notional >= min_notional:
                    print(f"  -> Liquidating leftover {sell_qty} {pair} (~${notional:.2f} USDT)...")
                    order = client.create_order(pair, "SELL", "MARKET", sell_qty)
                    oid = order.get("orderId")
                    print(f"  -> [SUCCESS] Liquidated {pair}! Order ID: {oid}")
            except Exception as e:
                # Some pairs might not exist against USDT or are dust
                pass
    except Exception as e:
        print(f"Error checking leftover assets: {e}")

    # 5. Clean up AI Engine and state
    print("\n[Step 5] Resetting internal position tracking...")
    if ai_engine:
        ai_engine.positions.clear()
        save_ai_positions(ai_engine.positions)
        ai_engine.status = "IDLE"
        ai_engine.status_message = "All positions closed by user."

    state["in_position"] = False
    state["entry_price"] = 0.0
    state["position_qty"] = 0.0
    state["position_time"] = None
    state["pnl_pct"] = 0.0

    # Invalidate active trades cache
    from dashboard import _active_trades_cache
    _active_trades_cache["timestamp"] = 0.0

    # 6. Verify final state
    remaining = get_all_active_trades(force_refresh=True)
    print("\n" + "=" * 60)
    print(f"CLOSURE COMPLETED! Closed: {closed_count} trade(s). Remaining Active: {len(remaining)}")
    print("=" * 60)

if __name__ == "__main__":
    close_all()

