"""
HTTP bridge — receives CAs from the WhatsApp monitor (Node.js) and
forwards them to SigmaBot using the existing, authenticated Telethon client.
Runs inside the same asyncio event loop as main.py.
"""
import logging
from aiohttp import web

log = logging.getLogger(__name__)

_client = None
_sigma_entity = None
_seen_cas: set = set()
_persist_ca = None


def init_bridge(client, sigma_entity, seen_cas: set, persist_ca) -> None:
    global _client, _sigma_entity, _seen_cas, _persist_ca
    _client = client
    _sigma_entity = sigma_entity
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
    try:
        await _client.send_message(_sigma_entity, ca)
        _seen_cas.add(ca)
        await _persist_ca(ca)
        log.info(f"[WA-FORWARDED] {chain} | {ca} -> {_sigma_entity.username or _sigma_entity.id}")
        return web.json_response({"status": "ok"})
    except Exception as e:
        log.error(f"[WA-SEND-FAIL] {ca}: {e}")
        return web.json_response({"status": "error", "reason": str(e)}, status=500)


async def start_bridge_server(port: int = 5050) -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/forward-ca", _handle_forward)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log.info(f"Bridge server listening on 127.0.0.1:{port}/forward-ca")
    return runner
