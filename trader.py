"""
trader.py - Alpaca Paper Trading İşlem Modülü
BTC/USD alım/satım emirlerini yönetir.
"""

import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class AlpacaTrader:
    def __init__(self, api_key: str, secret_key: str, base_url: str):
        self.client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=True  # Paper trading modu!
        )
        self.data_client = CryptoHistoricalDataClient()
        logger.info("✅ Alpaca Paper Trading bağlantısı kuruldu")

    def get_account(self) -> dict:
        """Hesap bilgilerini getir"""
        acc = self.client.get_account()
        return {
            "cash"        : float(acc.cash),
            "equity"      : float(acc.equity),
            "buying_power": float(acc.buying_power),
            "status"      : acc.status,
        }

    def get_btc_price(self) -> float | None:
        """Alpaca'dan anlık BTC fiyatı"""
        try:
            req = CryptoBarsRequest(
                symbol_or_symbols=["BTC/USD"],
                timeframe=TimeFrame.Minute,
                start=datetime.now(timezone.utc) - timedelta(minutes=5)
            )
            bars = self.data_client.get_crypto_bars(req)
            df = bars.df
            if not df.empty:
                return float(df["close"].iloc[-1])
        except Exception as e:
            logger.warning(f"Alpaca fiyat hatası: {e}")
        return None

    def open_long(self, usd_amount: float, signal: dict) -> dict | None:
        """Market emri ile BTC al (Long gir)"""
        try:
            # USD notional olarak emir ver
            order_req = MarketOrderRequest(
                symbol="BTC/USD",
                notional=str(round(usd_amount, 2)),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.IOC,
            )
            order = self.client.submit_order(order_req)
            logger.info(f"✅ BUY emri gönderildi: ${usd_amount} | Order ID: {order.id}")

            result = {
                "order_id"    : str(order.id),
                "side"        : "BUY",
                "notional"    : usd_amount,
                "symbol"      : "BTC/USD",
                "entry_price" : signal["current_price"],
                "target_price": signal["target_price"],
                "stop_price"  : signal["stop_price"],
                "status"      : str(order.status),
                "submitted_at": str(order.submitted_at),
            }
            return result

        except Exception as e:
            logger.error(f"❌ BUY emri hatası: {e}")
            return None

    def close_position(self) -> bool:
        """Açık BTC pozisyonunu kapat"""
        try:
            self.client.close_position("BTCUSD")
            logger.info("✅ Pozisyon kapatıldı")
            return True
        except Exception as e:
            logger.error(f"❌ Pozisyon kapatma hatası: {e}")
            return False

    def get_open_positions(self) -> list:
        """Açık pozisyonları listele"""
        try:
            positions = self.client.get_all_positions()
            return [
                {
                    "symbol"    : p.symbol,
                    "qty"       : float(p.qty),
                    "avg_entry" : float(p.avg_entry_price),
                    "market_val": float(p.market_value),
                    "unrealized": float(p.unrealized_pl),
                    "unrealized_pct": float(p.unrealized_plpc) * 100,
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Pozisyon listeleme hatası: {e}")
            return []

    def check_position_status(self, entry_price: float, target: float, stop: float) -> str:
        """
        Pozisyon hedef/stop kontrolü.
        Dönüş: 'hold' | 'take_profit' | 'stop_loss'
        """
        positions = self.get_open_positions()
        btc_pos = next((p for p in positions if "BTC" in p["symbol"]), None)
        if not btc_pos:
            return "no_position"

        current = btc_pos["avg_entry"] * (1 + btc_pos["unrealized_pct"] / 100)

        if current >= target:
            return "take_profit"
        elif current <= stop:
            return "stop_loss"
        return "hold"
