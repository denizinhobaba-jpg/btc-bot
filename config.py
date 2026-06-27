import os

ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = "https://paper-api.alpaca.markets"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

TRADE_SYMBOL       = "BTC/USD"
TRADE_AMOUNT_USD   = 100

CHECK_INTERVAL_SEC = 300
VOLUME_SPIKE_MULT  = 2.0
PRICE_CHANGE_PCT   = 1.5
RSI_OVERSOLD       = 35
RSI_OVERBOUGHT     = 65

TAKE_PROFIT_PCT    = 3.0
STOP_LOSS_PCT      = 1.5
