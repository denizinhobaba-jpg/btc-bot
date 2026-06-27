"""
main.py - BTC Trading Bot + Dashboard API
Bot döngüsü ve FastAPI aynı anda çalışır.
"""

import asyncio
import logging
import sys
from datetime import datetime

import uvicorn

import config
import analyzer
import telegram_bot as tgbot
from trader import AlpacaTrader
from api import app as fastapi_app, shared_state, broadcast

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


def ts() -> str:
    return datetime.now().strftime("%H:%M")


def push(update: dict):
    """shared_state'i güncelle ve WebSocket üzerinden yayınla"""
    shared_state.update(update)
    # broadcast'i event loop'a ekle
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(broadcast({"type": "update", **update}))
    except Exception:
        pass


def add_signal(text: str, color: str = "blue"):
    sigs = shared_state.get("signal_history", [])
    sigs.insert(0, {"text": text, "color": color, "time": ts()})
    if len(sigs) > 20:
        sigs.pop()
    push({"signal_history": sigs})


async def execute_trade(signal: dict):
    global active_position
    if active_position:
        await tg_app.bot.send_message(config.TELEGRAM_CHAT_ID, "⚠️ Zaten açık pozisyon var!")
        return

    result = trader.open_long(config.TRADE_AMOUNT_USD, signal)
    if result:
        active_position = {**signal, **result}
        pos_data = {
            "entry_price" : signal["current_price"],
            "current_price": signal["current_price"],
            "target_price": signal["target_price"],
            "stop_price"  : signal["stop_price"],
            "qty"         : config.TRADE_AMOUNT_USD / signal["current_price"],
            "market_val"  : config.TRADE_AMOUNT_USD,
            "unrealized"  : 0.0,
            "unrealized_pct": 0.0,
        }
        push({"active_position": pos_data})
        add_signal(f"İşlem açıldı — {signal['direction']} @ ${signal['current_price']:,.0f}", "green")

        await tg_app.bot.send_message(
            config.TELEGRAM_CHAT_ID,
            f"🚀 *İŞLEM AÇILDI!*\n📈 {signal['direction']} @ ${signal['current_price']:,.2f}\n"
            f"🎯 Hedef: ${signal['target_price']:,.2f}\n🛑 Stop: ${signal['stop_price']:,.2f}",
            parse_mode="Markdown"
        )
    else:
        add_signal("İşlem açılamadı — hata oluştu", "red")
        await tg_app.bot.send_message(config.TELEGRAM_CHAT_ID, "❌ İşlem açılamadı!")


async def monitor_loop():
    global active_position, last_signal_time

    push({"bot_status": "aktif", "scan_count": 0})
    add_signal("Bot başladı — izleme aktif", "blue")
    logger.info("🤖 Monitor döngüsü başladı")

    while True:
        try:
            now = datetime.now()
            scan_count = shared_state.get("scan_count", 0) + 1

            # ── Hesap güncelle ──────────────────────────
            try:
                acc = trader.get_account()
                push({
                    "account_cash"  : acc.get("cash", 0),
                    "account_equity": acc.get("equity", 0),
                })
            except Exception:
                pass

            # ── Pozisyon kontrolü ───────────────────────
            if active_position:
                positions = trader.get_open_positions()
                if positions:
                    pos = positions[0]
                    pos_update = {
                        "entry_price"   : active_position["current_price"],
                        "current_price" : pos["avg_entry"] * (1 + pos["unrealized_pct"] / 100),
                        "target_price"  : active_position["target_price"],
                        "stop_price"    : active_position["stop_price"],
                        "qty"           : pos["qty"],
                        "market_val"    : pos["market_val"],
                        "unrealized"    : pos["unrealized"],
                        "unrealized_pct": pos["unrealized_pct"],
                    }
                    push({"active_position": pos_update})

                status = trader.check_position_status(
                    active_position["current_price"],
                    active_position["target_price"],
                    active_position["stop_price"],
                )
                if status == "take_profit":
                    trader.close_position()
                    profit = config.TRADE_AMOUNT_USD * config.TAKE_PROFIT_PCT / 100
                    add_signal(f"Kar alındı! +${profit:.2f}", "green")
                    push({"active_position": None})
                    active_position = None
                    await tg_app.bot.send_message(
                        config.TELEGRAM_CHAT_ID,
                        f"✅ *KAR ALINDI!* +${profit:.2f}",
                        parse_mode="Markdown"
                    )
                elif status == "stop_loss":
                    trader.close_position()
                    loss = config.TRADE_AMOUNT_USD * config.STOP_LOSS_PCT / 100
                    add_signal(f"Stop loss tetiklendi. -${loss:.2f}", "red")
                    push({"active_position": None})
                    active_position = None
                    await tg_app.bot.send_message(
                        config.TELEGRAM_CHAT_ID,
                        f"🛑 *STOP LOSS* -${loss:.2f}",
                        parse_mode="Markdown"
                    )

            # ── Analiz ─────────────────────────────────
            df = analyzer.get_btc_data(limit=100)
            if not df.empty:
                price       = float(df["close"].iloc[-1])
                prev        = float(df["close"].iloc[-2])
                change_5m   = (price - prev) / prev * 100
                rsi         = analyzer.calc_rsi(df["close"])
                bb          = analyzer.calc_bollinger(df["close"])
                vol_ratio   = float(df["volume"].iloc[-1]) / float(df["volume"].iloc[-20:-1].mean())

                change_24h = change_5m
                if len(df) >= 288:
                    p24 = float(df["close"].iloc[-288])
                    change_24h = (price - p24) / p24 * 100

                bb_pos = ("ÜST BANT 🔴" if price > bb["upper"]
                          else "ALT BANT 🔵" if price < bb["lower"]
                          else "Orta Bant ⚪")

                # Fiyat geçmişine ekle
                history = shared_state.get("price_history", [])
                history.append({"t": ts(), "p": round(price, 0)})
                if len(history) > 60:
                    history = history[-60:]

                # Tarama geçmişine ekle
                scans = shared_state.get("scan_history", [])
                scans.insert(0, {"t": ts(), "p": round(price, 0), "c": round(change_5m, 2), "r": round(rsi, 1)})
                if len(scans) > 10:
                    scans.pop()

                push({
                    "price"        : round(price, 2),
                    "change_5m"    : round(change_5m, 3),
                    "change_24h"   : round(change_24h, 2),
                    "rsi"          : round(rsi, 2),
                    "vol_ratio"    : round(vol_ratio, 2),
                    "bb_upper"     : round(bb["upper"], 2),
                    "bb_mid"       : round(bb["mid"], 2),
                    "bb_lower"     : round(bb["lower"], 2),
                    "bb_position"  : bb_pos,
                    "scan_count"   : scan_count,
                    "last_scan"    : ts(),
                    "price_history": history,
                    "scan_history" : scans,
                })

                # Fiyat tick'i yay (grafik için)
                await broadcast({"type": "price_tick", "t": ts(), "p": round(price, 2)})

                # Sinyal ara
                cooldown_ok = (
                    last_signal_time is None or
                    (now - last_signal_time).total_seconds() > SIGNAL_COOLDOWN_SEC
                )
                if cooldown_ok and not active_position:
                    signal = analyzer.analyze(config)
                    if signal:
                        last_signal_time = now
                        add_signal(
                            f"{signal['direction']} sinyali — RSI:{rsi:.0f} Vol:{vol_ratio:.1f}x @ ${price:,.0f}",
                            "green" if "LONG" in signal["direction"] else "red"
                        )
                        await tgbot.send_signal(tg_app, config.TELEGRAM_CHAT_ID, signal, config.TRADE_AMOUNT_USD)
                    else:
                        add_signal(f"Tarama tamam — sinyal yok @ ${price:,.0f}", "blue")

        except Exception as e:
            logger.error(f"Döngü hatası: {e}", exc_info=True)
            add_signal(f"Hata: {str(e)[:60]}", "red")

        await asyncio.sleep(config.CHECK_INTERVAL_SEC)


async def main():
    global trader, tg_app

    logger.info("=" * 55)
    logger.info("  🤖 BTC BOT + DASHBOARD — BAŞLADI")
    logger.info("=" * 55)

    if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        logger.error("❌ ALPACA_API_KEY veya ALPACA_SECRET_KEY eksik! Render Environment Variables kontrol et.")
        push({"bot_status": "hata: alpaca key eksik"})
    
    trader = AlpacaTrader(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, config.ALPACA_BASE_URL)
    
    acc = {"cash": 0, "equity": 0}
    try:
        acc = trader.get_account()
        logger.info(f"✅ Alpaca bağlandı — Bakiye: ${acc.get('cash', 0):,.2f}")
        push({"bot_status": "aktif"})
    except Exception as e:
        logger.error(f"❌ Alpaca bağlantı hatası: {e}")
        logger.error("Render → Environment Variables → ALPACA_API_KEY ve ALPACA_SECRET_KEY kontrol et!")
        push({"bot_status": f"hata: {str(e)[:60]}"})
        # Dashboard açık kalsın, bot çalışmaya devam etsin

    push({
        "account_cash"  : acc.get("cash", 0),
        "account_equity": acc.get("equity", 0),
    })

    tg_app = tgbot.create_app(
        config.TELEGRAM_BOT_TOKEN,
        execute_trade,
        shared_state.get("scan_history", []),
        trader,
        active_position
    )

    await tg_app.initialize()
    await tg_app.start()

    port = int(__import__("os").environ.get("PORT", 8000))
    dashboard_url = f"http://localhost:{port}"
    logger.info(f"🌐 Dashboard: {dashboard_url}")

    await tg_app.bot.send_message(
        config.TELEGRAM_CHAT_ID,
        f"🤖 *BTC Bot + Dashboard Başladı!*\n"
        f"💰 Paper Bakiye: ${acc.get('cash',0):,.2f}\n"
        f"🌐 Panel: Railway URL'sini aç\n"
        f"/durum /gecmis /pozisyon /hesap",
        parse_mode="Markdown"
    )

    # FastAPI + Bot + Monitor paralel çalışır
    uv_config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=port, log_level="warning")
    uv_server = uvicorn.Server(uv_config)

    await asyncio.gather(
        uv_server.serve(),
        tg_app.updater.start_polling(),
        monitor_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot durduruldu.")
