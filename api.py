"""
api.py - FastAPI Backend
WebSocket + REST endpoints → Panel buradan veri çeker.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

app = FastAPI(title="BTC Bot Dashboard API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Bağlı WebSocket istemcileri ──────────────────────────────
connected_clients: Set[WebSocket] = set()

# ── Paylaşılan durum (main.py tarafından güncellenir) ────────
shared_state: dict = {
    "price"         : 0.0,
    "change_5m"     : 0.0,
    "change_24h"    : 0.0,
    "rsi"           : 50.0,
    "vol_ratio"     : 1.0,
    "bb_upper"      : 0.0,
    "bb_mid"        : 0.0,
    "bb_lower"      : 0.0,
    "bb_position"   : "Orta Bant",
    "account_cash"  : 0.0,
    "account_equity": 0.0,
    "scan_count"    : 0,
    "active_position": None,
    "scan_history"  : [],
    "signal_history": [],
    "price_history" : [],   # [{t, p}]  son 60 nokta
    "last_scan"     : "",
    "bot_status"    : "başlatılıyor",
}


async def broadcast(data: dict):
    """Tüm bağlı istemcilere mesaj gönder"""
    if not connected_clients:
        return
    msg = json.dumps(data)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    connected_clients -= dead


def push_update(update: dict):
    """Sync context'ten çağrılabilir (asyncio.create_task yerine)"""
    shared_state.update(update)


# ── REST Endpoints ───────────────────────────────────────────

@app.get("/api/state")
async def get_state():
    """Panel ilk yüklendiğinde tam durumu çeker"""
    return shared_state


@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


# ── WebSocket ────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    logger.info(f"WS bağlantı kuruldu. Toplam: {len(connected_clients)}")

    # Bağlanan istemciye hemen mevcut durumu gönder
    await websocket.send_text(json.dumps({"type": "full_state", **shared_state}))

    try:
        while True:
            # Ping al (bağlantıyı canlı tut)
            await asyncio.wait_for(websocket.receive_text(), timeout=60)
    except (WebSocketDisconnect, asyncio.TimeoutError, Exception):
        pass
    finally:
        connected_clients.discard(websocket)
        logger.info(f"WS bağlantı kapandı. Toplam: {len(connected_clients)}")


# ── Panel HTML (tek dosya — ayrı host gerekmez) ──────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Trading dashboard — /  adresinde açılır"""
    html = open("dashboard.html", encoding="utf-8").read()
    return HTMLResponse(content=html)
