"""
main.py - BTC Trading Bot Ana Kontrol
Tüm modülleri bir araya getirir, döngüyü yönetir.

Kullanım:
  python main.py

Durdurmak için: Ctrl+C
"""

import asyncio
import logging
import sys
from datetime import datetime

import config
import analyzer
import telegram_bot as tgbot
from trader import AlpacaTrader

# ─── LOGLAMA ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("main")

# ─── GLOBAL DURUM ──────────────────────────────────────────
trader: AlpacaTrader | None = None
tg_app = None
active_position: dict | None = None
last_signal_time: datetime | None = None
SIGNAL_COOLDOWN_SEC = 600  # Aynı yönde 10 dakika içinde 2. sinyal yok


# ─── İŞLEM EXECUTOR ────────────────────────────────────────
async def execute_trade(signal: dict):
    """Telegram'dan onay gelince çalışır"""
    global active_position
    logger.info(f"İşlem açılıyor: {signal['direction']}")

    if active_position:
        await tg_app.bot.send_message(
            config.TELEGRAM_CHAT_ID,
            "⚠️ Zaten açık bir pozisyon var! Önce onu kapat."
        )
        return

    result = trader.open_long(config.TRADE_AMOUNT_USD, signal)
    if result:
        active_position = {**signal, **result}
        msg = (
            f"🚀 *İŞLEM AÇILDI!*\n\n"
            f"📌 Order ID: `{result['order_id']}`\n"
            f"💰 Miktar: ${config.TRADE_AMOUNT_USD}\n"
            f"📈 Giriş: ${signal['current_price']:,.2f}\n"
            f"🎯 Hedef: ${signal['target_price']:,.2f}\n"
            f"🛑 Stop: ${signal['stop_price']:,.2f}\n\n"
            f"_Pozisyon otomatik izleniyor..._"
        )
        await tg_app.bot.send_message(
            config.TELEGRAM_CHAT_ID, msg, parse_mode="Markdown"
        )
    else:
        await tg_app.bot.send_message(
            config.TELEGRAM_CHAT_ID,
            "❌ İşlem açılamadı! Log dosyasını kontrol et."
        )


# ─── DÖNGÜ ─────────────────────────────────────────────────
async def monitor_loop():
    """Ana izleme döngüsü - her 5 dakikada bir çalışır"""
    global active_position, last_signal_time
    logger.info("🤖 Monitor döngüsü başladı")

    while True:
        try:
            now = datetime.now()

            # ── Açık pozisyon varsa → hedef/stop kontrolü ──
            if active_position:
                status = trader.check_position_status(
                    entry_price=active_position["current_price"],
                    target=active_position["target_price"],
                    stop=active_position["stop_price"],
                )
                if status == "take_profit":
                    trader.close_position()
                    profit_est = config.TRADE_AMOUNT_USD * (config.TAKE_PROFIT_PCT / 100)
                    msg = (
                        f"✅ *KAR ALINDI!* 🎉\n"
                        f"Hedef fiyata ulaşıldı.\n"
                        f"Tahmini Kar: +${profit_est:.2f}"
                    )
                    await tg_app.bot.send_message(
                        config.TELEGRAM_CHAT_ID, msg, parse_mode="Markdown"
                    )
                    active_position = None

                elif status == "stop_loss":
                    trader.close_position()
                    loss_est = config.TRADE_AMOUNT_USD * (config.STOP_LOSS_PCT / 100)
                    msg = (
                        f"🛑 *STOP LOSS TETİKLENDİ*\n"
                        f"Zarar durduruldu.\n"
                        f"Tahmini Zarar: -${loss_est:.2f}"
                    )
                    await tg_app.bot.send_message(
                        config.TELEGRAM_CHAT_ID, msg, parse_mode="Markdown"
                    )
                    active_position = None

                else:
                    positions = trader.get_open_positions()
                    if positions:
                        pos = positions[0]
                        logger.info(
                            f"📊 Pozisyon devam: {pos['unrealized_pct']:+.2f}% | "
                            f"Kar/Zarar: ${pos['unrealized']:+.2f}"
                        )

            # ── Yeni sinyal ara ──
            else:
                # Cooldown kontrolü
                cooldown_ok = (
                    last_signal_time is None or
                    (now - last_signal_time).total_seconds() > SIGNAL_COOLDOWN_SEC
                )

                if cooldown_ok:
                    signal = analyzer.analyze(config)
                    if signal:
                        last_signal_time = now
                        logger.info(f"🔔 Sinyal bulundu: {signal['direction']}")
                        await tgbot.send_signal(
                            tg_app,
                            config.TELEGRAM_CHAT_ID,
                            signal,
                            config.TRADE_AMOUNT_USD
                        )
                    else:
                        acc = trader.get_account()
                        logger.info(
                            f"💤 Sinyal yok | "
                            f"Bakiye: ${acc.get('cash', 0):,.2f} | "
                            f"Equity: ${acc.get('equity', 0):,.2f}"
                        )

        except Exception as e:
            logger.error(f"Monitor döngüsü hatası: {e}", exc_info=True)

        await asyncio.sleep(config.CHECK_INTERVAL_SEC)


# ─── BAŞLANGIÇ ──────────────────────────────────────────────
async def main():
    global trader, tg_app

    logger.info("=" * 55)
    logger.info("  🤖 BTC TRADING BOT - PAPER MODE BAŞLADI")
    logger.info("=" * 55)

    # Alpaca bağlantısı
    trader = AlpacaTrader(
        config.ALPACA_API_KEY,
        config.ALPACA_SECRET_KEY,
        config.ALPACA_BASE_URL,
    )
    acc = trader.get_account()
    logger.info(f"💰 Paper Bakiye: ${acc.get('cash', 0):,.2f}")

    # Telegram app
    tg_app = tgbot.create_app(config.TELEGRAM_BOT_TOKEN, execute_trade)

    # Başlangıç bildirimi
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.bot.send_message(
        config.TELEGRAM_CHAT_ID,
        f"🤖 *BTC Bot Başladı!*\n"
        f"📊 Paper Bakiye: ${acc.get('cash', 0):,.2f}\n"
        f"⏱ Her {config.CHECK_INTERVAL_SEC//60} dakikada analiz yapılacak.\n"
        f"📌 Komutlar: /status",
        parse_mode="Markdown"
    )

    # Döngüyü ve Telegram polling'i paralel çalıştır
    await asyncio.gather(
        tg_app.updater.start_polling(),
        monitor_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot durduruldu (Ctrl+C)")
