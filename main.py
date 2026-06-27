"""
main.py - BTC Trading Bot Ana Kontrol
"""

import asyncio
import logging
import sys
from datetime import datetime

import config
import analyzer
import telegram_bot as tgbot
from trader import AlpacaTrader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("main")

trader: AlpacaTrader | None = None
tg_app = None
active_position: dict | None = None
last_signal_time: datetime | None = None
SIGNAL_COOLDOWN_SEC = 600

# Her analizin sonucu burada tutulur (son 20 kayıt)
scan_history: list = []


async def execute_trade(signal: dict):
    global active_position
    if active_position:
        await tg_app.bot.send_message(config.TELEGRAM_CHAT_ID, "⚠️ Zaten açık pozisyon var!")
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
        await tg_app.bot.send_message(config.TELEGRAM_CHAT_ID, msg, parse_mode="Markdown")
    else:
        await tg_app.bot.send_message(config.TELEGRAM_CHAT_ID, "❌ İşlem açılamadı!")


def _rsi_bar(rsi: float) -> str:
    """RSI'yı görsel bar olarak göster"""
    filled = int(rsi / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {rsi}"


def _trend_arrow(change: float) -> str:
    if change > 1:   return "⬆️"
    if change > 0:   return "↗️"
    if change < -1:  return "⬇️"
    if change < 0:   return "↘️"
    return "➡️"


async def send_scan_report(data: dict):
    """Her 5 dakikada bir tarama raporu gönder"""
    pos_info = ""
    if active_position:
        positions = trader.get_open_positions()
        if positions:
            pos = positions[0]
            pnl_emoji = "🟢" if pos["unrealized"] >= 0 else "🔴"
            pos_info = (
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📂 *Açık Pozisyon:*\n"
                f"  {pnl_emoji} Kar/Zarar: ${pos['unrealized']:+.2f} ({pos['unrealized_pct']:+.2f}%)\n"
                f"  🎯 Hedef: ${active_position['target_price']:,.2f}\n"
                f"  🛑 Stop: ${active_position['stop_price']:,.2f}"
            )

    change = data.get("price_change", 0)
    arrow = _trend_arrow(change)
    rsi = data.get("rsi", 0)

    # RSI rengi
    if rsi < 35:      rsi_label = "Aşırı Satım 🔵"
    elif rsi < 45:    rsi_label = "Zayıf"
    elif rsi < 55:    rsi_label = "Nötr ⚪"
    elif rsi < 65:    rsi_label = "Güçlü"
    else:             rsi_label = "Aşırı Alım 🔴"

    msg = (
        f"📡 *TARAMA RAPORU*\n"
        f"🕐 {datetime.now().strftime('%H:%M')} — Sinyal Yok\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 BTC Fiyat: ${data['current_price']:,.2f} {arrow}\n"
        f"📊 5dk Değişim: {change:+.2f}%\n"
        f"📅 24s Değişim: {data.get('change_24h', 0):+.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 RSI: {_rsi_bar(rsi)}\n"
        f"   → {rsi_label}\n"
        f"📦 Hacim: Normalin {data.get('vol_ratio', 1):.1f}x\n"
        f"🎯 Bollinger: {data.get('bb_position', '-')}"
        f"{pos_info}"
    )
    await tg_app.bot.send_message(config.TELEGRAM_CHAT_ID, msg, parse_mode="Markdown")


async def monitor_loop():
    global active_position, last_signal_time, scan_history
    logger.info("🤖 Monitor döngüsü başladı")

    while True:
        try:
            now = datetime.now()

            # ── Pozisyon kontrolü ──
            if active_position:
                status = trader.check_position_status(
                    entry_price=active_position["current_price"],
                    target=active_position["target_price"],
                    stop=active_position["stop_price"],
                )
                if status == "take_profit":
                    trader.close_position()
                    profit_est = config.TRADE_AMOUNT_USD * (config.TAKE_PROFIT_PCT / 100)
                    await tg_app.bot.send_message(
                        config.TELEGRAM_CHAT_ID,
                        f"✅ *KAR ALINDI!* 🎉\nTahmini Kar: +${profit_est:.2f}",
                        parse_mode="Markdown"
                    )
                    active_position = None

                elif status == "stop_loss":
                    trader.close_position()
                    loss_est = config.TRADE_AMOUNT_USD * (config.STOP_LOSS_PCT / 100)
                    await tg_app.bot.send_message(
                        config.TELEGRAM_CHAT_ID,
                        f"🛑 *STOP LOSS TETİKLENDİ*\nTahmini Zarar: -${loss_est:.2f}",
                        parse_mode="Markdown"
                    )
                    active_position = None

            # ── Analiz ──
            cooldown_ok = (
                last_signal_time is None or
                (now - last_signal_time).total_seconds() > SIGNAL_COOLDOWN_SEC
            )

            # Her durumda ham veri çek (rapor için)
            df = analyzer.get_btc_data(limit=100)
            if not df.empty:
                current_price = df["close"].iloc[-1]
                prev_price    = df["close"].iloc[-2]
                price_change  = ((current_price - prev_price) / prev_price) * 100
                rsi           = analyzer.calc_rsi(df["close"])
                bb            = analyzer.calc_bollinger(df["close"])
                vol_ratio     = df["volume"].iloc[-1] / df["volume"].iloc[-20:-1].mean()

                if len(df) >= 288:
                    change_24h = ((current_price - df["close"].iloc[-288]) / df["close"].iloc[-288]) * 100
                else:
                    change_24h = price_change

                bb_pos = ("ÜST BANT 🔴" if current_price > bb["upper"]
                          else "ALT BANT 🔵" if current_price < bb["lower"]
                          else "Orta Bant ⚪")

                scan_data = {
                    "current_price": current_price,
                    "price_change":  round(price_change, 3),
                    "change_24h":    round(change_24h, 2),
                    "rsi":           rsi,
                    "vol_ratio":     round(vol_ratio, 2),
                    "bb_position":   bb_pos,
                    "time":          now.strftime("%H:%M"),
                }
                scan_history.append(scan_data)
                if len(scan_history) > 20:
                    scan_history.pop(0)

                # Sinyal var mı?
                if cooldown_ok:
                    signal = analyzer.analyze(config)
                    if signal:
                        last_signal_time = now
                        await tgbot.send_signal(tg_app, config.TELEGRAM_CHAT_ID, signal, config.TRADE_AMOUNT_USD)
                    else:
                        # Sinyal yok → tarama raporu gönder
                        await send_scan_report(scan_data)

        except Exception as e:
            logger.error(f"Döngü hatası: {e}", exc_info=True)

        await asyncio.sleep(config.CHECK_INTERVAL_SEC)


async def main():
    global trader, tg_app

    logger.info("=" * 55)
    logger.info("  🤖 BTC TRADING BOT - PAPER MODE BAŞLADI")
    logger.info("=" * 55)

    trader = AlpacaTrader(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, config.ALPACA_BASE_URL)
    acc = trader.get_account()

    tg_app = tgbot.create_app(config.TELEGRAM_BOT_TOKEN, execute_trade, scan_history, trader, active_position)

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.bot.send_message(
        config.TELEGRAM_CHAT_ID,
        f"🤖 *BTC Bot Başladı!*\n"
        f"📊 Paper Bakiye: ${acc.get('cash', 0):,.2f}\n"
        f"⏱ Her 5 dakikada tarama raporu gelecek\n\n"
        f"📌 *Komutlar:*\n"
        f"/durum — Anlık BTC durumu\n"
        f"/gecmis — Son 5 tarama\n"
        f"/pozisyon — Açık pozisyon\n"
        f"/hesap — Bakiye bilgisi",
        parse_mode="Markdown"
    )

    await asyncio.gather(
        tg_app.updater.start_polling(),
        monitor_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot durduruldu (Ctrl+C)")
