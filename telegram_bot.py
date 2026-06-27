"""
telegram_bot.py - Telegram Bildirim & Onay Sistemi
Sinyal gönderir, /onayla veya /reddet komutunu bekler.
"""

import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

logger = logging.getLogger(__name__)

# Global: bekleyen işlem
pending_trade: dict | None = None
trade_executor_callback = None  # main.py'den set edilir


def build_signal_message(signal: dict, trade_amount: float) -> str:
    """Telegram'a gönderilecek sinyal mesajını oluştur"""
    bars = "🔥" * min(signal["signal_str"], 5)
    signals_text = "\n".join(f"  • {s}" for s in signal["signals"])

    msg = f"""
🚨 *BTC SİNYAL TESPİT EDİLDİ* 🚨
{bars}

💹 *Yön:* {signal['direction']}
💰 *Fiyat:* ${signal['current_price']:,.2f}
📅 *Zaman:* {signal['timestamp']}

━━━━━━━━━━━━━━━━━━━━
📌 *Tespit Edilen Sinyaller:*
{signals_text}

━━━━━━━━━━━━━━━━━━━━
📊 *Teknik Göstergeler:*
  • RSI: {signal['rsi']} ({_rsi_comment(signal['rsi'])})
  • Hacim: Normalin {signal['vol_ratio']}x üstünde
  • 5dk Değişim: {signal['price_change']:+.2f}%
  • 24s Değişim: {signal['change_24h']:+.2f}%
  • Bollinger: {signal['bb_position']}

━━━━━━━━━━━━━━━━━━━━
🎯 *İşlem Planı (${trade_amount:.0f} ile):*
  • Giriş: ${signal['current_price']:,.2f}
  • Hedef Satış: ${signal['target_price']:,.2f} ✅
  • Stop Loss: ${signal['stop_price']:,.2f} 🛑

⚠️ _Bu bir Paper Trading (demo) işlemidir._
Onaylamak istiyor musun?
"""
    return msg.strip()


def _rsi_comment(rsi: float) -> str:
    if rsi < 30:   return "Aşırı Satım 📉"
    if rsi < 45:   return "Zayıf"
    if rsi < 55:   return "Nötr"
    if rsi < 70:   return "Güçlü"
    return "Aşırı Alım 📈"


async def send_signal(app: Application, chat_id: str, signal: dict, trade_amount: float):
    """Sinyal mesajı + Onayla/Reddet butonları gönder"""
    global pending_trade
    pending_trade = signal

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ONAYLA - İŞLEM AÇ", callback_data="approve"),
            InlineKeyboardButton("❌ REDDET", callback_data="reject"),
        ]
    ])

    msg = build_signal_message(signal, trade_amount)
    await app.bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    logger.info(f"Sinyal Telegram'a gönderildi: {signal['direction']} @ ${signal['current_price']:,.0f}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline buton basıldığında çalışır"""
    global pending_trade
    query = update.callback_query
    await query.answer()

    if query.data == "approve" and pending_trade:
        await query.edit_message_text(
            text=f"✅ *ONAYLANDI!* İşlem açılıyor...\n\n"
                 f"📈 {pending_trade['direction']} @ ${pending_trade['current_price']:,.2f}\n"
                 f"🎯 Hedef: ${pending_trade['target_price']:,.2f}\n"
                 f"🛑 Stop: ${pending_trade['stop_price']:,.2f}",
            parse_mode="Markdown"
        )
        if trade_executor_callback:
            await trade_executor_callback(pending_trade)
        pending_trade = None

    elif query.data == "reject":
        await query.edit_message_text(
            text="❌ *REDDEDİLDİ.* İşlem açılmadı.",
            parse_mode="Markdown"
        )
        pending_trade = None
        logger.info("Kullanıcı işlemi reddetti.")


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status komutu - mevcut durumu göster"""
    if pending_trade:
        await update.message.reply_text(
            f"⏳ Bekleyen işlem var:\n"
            f"{pending_trade['direction']} @ ${pending_trade['current_price']:,.2f}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("✅ Bekleyen işlem yok. Bot aktif izleme yapıyor.")


def create_app(token: str, executor_callback) -> Application:
    """Telegram Application nesnesini kur"""
    global trade_executor_callback
    trade_executor_callback = executor_callback

    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("status", handle_status))
    return app
