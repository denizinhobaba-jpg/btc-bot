"""
telegram_bot.py - Telegram Bildirim & Komut Sistemi
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logger = logging.getLogger(__name__)

pending_trade: dict | None = None
trade_executor_callback = None
_scan_history = []
_trader = None
_active_position_ref = None


def build_signal_message(signal: dict, trade_amount: float) -> str:
    bars = "🔥" * min(signal["signal_str"], 5)
    signals_text = "\n".join(f"  • {s}" for s in signal["signals"])
    return f"""
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
  • RSI: {signal['rsi']} 
  • Hacim: Normalin {signal['vol_ratio']}x üstünde
  • 5dk Değişim: {signal['price_change']:+.2f}%
  • 24s Değişim: {signal['change_24h']:+.2f}%
  • Bollinger: {signal['bb_position']}

━━━━━━━━━━━━━━━━━━━━
🎯 *İşlem Planı (${trade_amount:.0f} ile):*
  • Giriş: ${signal['current_price']:,.2f}
  • Hedef Satış: ${signal['target_price']:,.2f} ✅
  • Stop Loss: ${signal['stop_price']:,.2f} 🛑

⚠️ _Paper Trading (demo) işlemi_
""".strip()


async def send_signal(app: Application, chat_id: str, signal: dict, trade_amount: float):
    global pending_trade
    pending_trade = signal
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ONAYLA - İŞLEM AÇ", callback_data="approve"),
        InlineKeyboardButton("❌ REDDET", callback_data="reject"),
    ]])
    msg = build_signal_message(signal, trade_amount)
    await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=keyboard)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pending_trade
    query = update.callback_query
    await query.answer()
    if query.data == "approve" and pending_trade:
        await query.edit_message_text(
            f"✅ *ONAYLANDI!* İşlem açılıyor...\n"
            f"📈 {pending_trade['direction']} @ ${pending_trade['current_price']:,.2f}",
            parse_mode="Markdown"
        )
        if trade_executor_callback:
            await trade_executor_callback(pending_trade)
        pending_trade = None
    elif query.data == "reject":
        await query.edit_message_text("❌ *REDDEDİLDİ.* İşlem açılmadı.", parse_mode="Markdown")
        pending_trade = None


async def cmd_durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/durum - anlık BTC analizi"""
    import analyzer, config
    await update.message.reply_text("🔍 Anlık veri çekiliyor...")
    df = analyzer.get_btc_data(limit=100)
    if df.empty:
        await update.message.reply_text("❌ Veri alınamadı.")
        return

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

    rsi_bar = "█" * int(rsi / 10) + "░" * (10 - int(rsi / 10))
    bb_pos = ("ÜST BANT 🔴" if current_price > bb["upper"]
              else "ALT BANT 🔵" if current_price < bb["lower"]
              else "Orta Bant ⚪")

    arrow = "⬆️" if price_change > 0 else "⬇️"

    msg = (
        f"📡 *ANLIK BTC DURUMU*\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Fiyat: ${current_price:,.2f} {arrow}\n"
        f"📊 5dk: {price_change:+.2f}%\n"
        f"📅 24s: {change_24h:+.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 RSI: [{rsi_bar}] {rsi}\n"
        f"📦 Hacim: {vol_ratio:.1f}x normal\n"
        f"🎯 Bollinger: {bb_pos}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⬆️ BB Üst: ${bb['upper']:,.2f}\n"
        f"➡️ BB Orta: ${bb['mid']:,.2f}\n"
        f"⬇️ BB Alt: ${bb['lower']:,.2f}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_gecmis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/gecmis - son 5 tarama"""
    if not _scan_history:
        await update.message.reply_text("Henüz tarama geçmişi yok.")
        return

    son5 = _scan_history[-5:]
    lines = ["📋 *SON 5 TARAMA*\n"]
    for s in reversed(son5):
        arrow = "⬆️" if s["price_change"] > 0 else "⬇️"
        lines.append(
            f"🕐 {s['time']} — ${s['current_price']:,.0f} {arrow} {s['price_change']:+.2f}% | RSI:{s['rsi']} | Vol:{s['vol_ratio']}x"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_pozisyon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pozisyon - açık pozisyon bilgisi"""
    if _trader is None:
        await update.message.reply_text("Bot henüz başlamadı.")
        return
    positions = _trader.get_open_positions()
    if not positions:
        await update.message.reply_text("📭 Açık pozisyon yok.")
        return
    pos = positions[0]
    pnl_emoji = "🟢" if pos["unrealized"] >= 0 else "🔴"
    msg = (
        f"📂 *AÇIK POZİSYON*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Sembol: {pos['symbol']}\n"
        f"📊 Miktar: {pos['qty']:.6f} BTC\n"
        f"📈 Giriş Fiyatı: ${pos['avg_entry']:,.2f}\n"
        f"💵 Piyasa Değeri: ${pos['market_val']:,.2f}\n"
        f"{pnl_emoji} Kar/Zarar: ${pos['unrealized']:+.2f} ({pos['unrealized_pct']:+.2f}%)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_hesap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hesap - bakiye bilgisi"""
    if _trader is None:
        await update.message.reply_text("Bot henüz başlamadı.")
        return
    acc = _trader.get_account()
    msg = (
        f"💼 *HESAP BİLGİSİ*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Nakit: ${acc.get('cash', 0):,.2f}\n"
        f"📊 Toplam Değer: ${acc.get('equity', 0):,.2f}\n"
        f"💳 Alım Gücü: ${acc.get('buying_power', 0):,.2f}\n"
        f"✅ Hesap Durumu: {acc.get('status', '-')}\n"
        f"⚠️ _Paper Trading (Sahte Para)_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status - bot durumu"""
    if pending_trade:
        await update.message.reply_text(
            f"⏳ Onay bekleyen işlem var:\n{pending_trade['direction']} @ ${pending_trade['current_price']:,.2f}"
        )
    else:
        await update.message.reply_text(
            "✅ Bot aktif çalışıyor. Bekleyen işlem yok.\n\n"
            "/durum /gecmis /pozisyon /hesap"
        )


def create_app(token: str, executor_callback, scan_history: list, trader, active_pos) -> Application:
    global trade_executor_callback, _scan_history, _trader, _active_position_ref
    trade_executor_callback = executor_callback
    _scan_history = scan_history
    _trader = trader
    _active_position_ref = active_pos

    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("durum",    cmd_durum))
    app.add_handler(CommandHandler("gecmis",   cmd_gecmis))
    app.add_handler(CommandHandler("pozisyon", cmd_pozisyon))
    app.add_handler(CommandHandler("hesap",    cmd_hesap))
    return app
