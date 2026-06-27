"""
analyzer.py - BTC Sinyal Analiz Motoru
Her 5 dakikada bir çalışır, anormallik tespit eder.
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def get_btc_data(limit: int = 100) -> pd.DataFrame:
    """
    Binance public API'den BTC/USDT 5 dakikalık mum verisi çeker.
    (Alpaca crypto data için fallback olarak kullanılır)
    """
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "5m",
        "limit": limit
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df = df.set_index("open_time")
        return df
    except Exception as e:
        logger.error(f"Veri çekme hatası: {e}")
        return pd.DataFrame()


def calc_rsi(series: pd.Series, period: int = 14) -> float:
    """RSI hesapla"""
    delta = series.diff().dropna()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_bollinger(series: pd.Series, period: int = 20) -> dict:
    """Bollinger Bands hesapla"""
    mid   = series.rolling(period).mean().iloc[-1]
    std   = series.rolling(period).std().iloc[-1]
    upper = mid + 2 * std
    lower = mid - 2 * std
    return {"mid": mid, "upper": upper, "lower": lower}


def analyze(config) -> dict | None:
    """
    Ana analiz fonksiyonu.
    Anormallik varsa sinyal dict döndürür, yoksa None.
    """
    df = get_btc_data(limit=100)
    if df.empty:
        logger.warning("Veri alınamadı, analiz atlandı.")
        return None

    current_price  = df["close"].iloc[-1]
    prev_price     = df["close"].iloc[-2]
    price_change   = ((current_price - prev_price) / prev_price) * 100

    # Hacim analizi
    current_vol    = df["volume"].iloc[-1]
    avg_vol        = df["volume"].iloc[-20:-1].mean()
    vol_ratio      = current_vol / avg_vol if avg_vol > 0 else 1

    # RSI
    rsi = calc_rsi(df["close"])

    # Bollinger
    bb = calc_bollinger(df["close"])
    bb_position = ""
    if current_price > bb["upper"]:
        bb_position = "ÜST BANT (aşırı alım)"
    elif current_price < bb["lower"]:
        bb_position = "ALT BANT (aşırı satım)"
    else:
        bb_position = "Orta Bant"

    # 24 saatlik değişim (son 288 mum = 24 saat @ 5m)
    if len(df) >= 288:
        price_24h_ago  = df["close"].iloc[-288]
        change_24h     = ((current_price - price_24h_ago) / price_24h_ago) * 100
    else:
        change_24h     = price_change

    # ---- SİNYAL KOŞULLARI ----
    signals = []
    signal_strength = 0

    if abs(price_change) >= config.PRICE_CHANGE_PCT:
        signals.append(f"⚡ Fiyat {price_change:+.2f}% değişti (5dk)")
        signal_strength += 2

    if vol_ratio >= config.VOLUME_SPIKE_MULT:
        signals.append(f"📊 Hacim normalin {vol_ratio:.1f}x üstünde")
        signal_strength += 2

    if rsi <= config.RSI_OVERSOLD:
        signals.append(f"📉 RSI={rsi} → Aşırı Satım (Long fırsatı?)")
        signal_strength += 1

    if rsi >= config.RSI_OVERBOUGHT:
        signals.append(f"📈 RSI={rsi} → Aşırı Alım (Dikkat!)")
        signal_strength += 1

    if current_price < bb["lower"]:
        signals.append(f"🎯 Bollinger Alt Bandı Kırıldı")
        signal_strength += 1

    # Yeterli sinyal yoksa çık
    if signal_strength < 2:
        logger.info(f"[{datetime.now().strftime('%H:%M')}] BTC: ${current_price:,.0f} | RSI: {rsi} | Vol: {vol_ratio:.1f}x | Sinyal yok")
        return None

    # Yön tahmini
    direction = "LONG 🟢" if price_change > 0 or rsi <= config.RSI_OVERSOLD else "DİKKAT 🔴"

    # Hedef fiyat hesapla
    if "LONG" in direction:
        target_price = current_price * (1 + config.TAKE_PROFIT_PCT / 100)
        stop_price   = current_price * (1 - config.STOP_LOSS_PCT / 100)
    else:
        target_price = current_price * (1 - config.TAKE_PROFIT_PCT / 100)
        stop_price   = current_price * (1 + config.STOP_LOSS_PCT / 100)

    return {
        "direction"    : direction,
        "current_price": current_price,
        "target_price" : round(target_price, 2),
        "stop_price"   : round(stop_price, 2),
        "rsi"          : rsi,
        "vol_ratio"    : round(vol_ratio, 2),
        "price_change" : round(price_change, 3),
        "change_24h"   : round(change_24h, 2),
        "bb_position"  : bb_position,
        "signals"      : signals,
        "signal_str"   : signal_strength,
        "timestamp"    : datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
