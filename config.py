import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    # Fallback to .env.example for default parameter values
    example_path = Path(__file__).parent / ".env.example"
    if example_path.exists():
        load_dotenv(dotenv_path=example_path)

# Market Mode: 'FUTURES' (USDT-M) or 'SPOT'
MARKET_TYPE = os.getenv("MARKET_TYPE", "FUTURES").upper().strip()

# Base URLs
SPOT_BASE_URL = "https://testnet.binance.vision"
FUTURES_BASE_URL = os.getenv("FUTURES_BASE_URL", "https://fapi.binance.com").rstrip("/")

BASE_URL = FUTURES_BASE_URL if MARKET_TYPE == "FUTURES" else SPOT_BASE_URL

API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

# Futures Settings
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "5"))
MARGIN_TYPE = os.getenv("MARGIN_TYPE", "ISOLATED").upper().strip()

# Trading Settings
SYMBOL = os.getenv("SYMBOL", "BTCUSDT").upper().strip()
TRADE_QUANTITY = float(os.getenv("TRADE_QUANTITY", "0.001"))
TIMEFRAME = os.getenv("TIMEFRAME", "1m").strip()

# Strategy Parameters (EMA Crossover + RSI filter)
FAST_MA_PERIOD = int(os.getenv("FAST_MA_PERIOD", "7"))
SLOW_MA_PERIOD = int(os.getenv("SLOW_MA_PERIOD", "25"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", "35"))
RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "68"))

# Risk Management
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "1.5"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))

# Fee & Tax Configuration
BINANCE_FEE_PCT = float(os.getenv("BINANCE_FEE_PCT", "0.05")) # 0.05% Taker fee per side (0.10% round-trip)
TAX_RATE_PCT = float(os.getenv("TAX_RATE_PCT", "30.0"))       # 30.0% Flat Capital Gains Tax on Net Profit
TDS_PCT = float(os.getenv("TDS_PCT", "1.0"))                 # 1.0% TDS on trade turnover
INCLUDE_TAXES = os.getenv("INCLUDE_TAXES", "true").lower() in ("true", "1", "yes")

# Bot Controls
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))

