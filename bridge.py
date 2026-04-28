"""
HTTP bridge — receives CAs from the WhatsApp monitor (Node.js) and
forwards them to SigmaBot using the existing, authenticated Telethon client.
Runs inside the same asyncio event loop as main.py.
"""
import logging
from aiohttp import web

log = logging.getLogger(__name__)

_senders: list = []   # list of (TelegramClient, entity) tuples
_seen_cas: set = set()
_persist_ca = None


def init_bridge(senders: list, seen_cas: set, persist_ca) -> None:
    global _senders, _seen_cas, _persist_ca
    _senders = senders
    _seen_cas = seen_cas
    _persist_ca = persist_ca


async def _handle_forward(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "reason": "invalid json"}, status=400)

    ca = (data.get("ca") or "").strip()
    chain = data.get("chain", "?")
    source = data.get("source", "WhatsApp")

    if not ca:
        return web.json_response({"status": "error", "reason": "missing ca"}, status=400)

    if ca in _seen_cas:
        log.info(f"[WA-SKIP-DUP]  {chain} | {ca}")
        return web.json_response({"status": "dup"})

    log.info(f"[WA-DETECTED]  {chain} | {ca} | src: {source}")
    sent = False
    errors = []
    for sender_client, entity in _senders:
        try:
            await sender_client.send_message(entity, ca)
            name = getattr(entity, "username", None) or str(entity.id)
            log.info(f"[WA-FORWARDED] {chain} | {ca} -> {name}")
            sent = True
        except Exception as e:
            name = getattr(entity, "username", None) or str(entity.id)
            log.error(f"[WA-SEND-FAIL] {ca} -> {name}: {e}")
            errors.append(str(e))

    if sent:
        _seen_cas.add(ca)
        await _persist_ca(ca)
        return web.json_response({"status": "ok"})
    return web.json_response({"status": "error", "reason": "; ".join(errors)}, status=500)


async def start_bridge_server(port: int = 5050) -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/forward-ca", _handle_forward)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log.info(f"Bridge server listening on 127.0.0.1:{port}/forward-ca")
    return runner
