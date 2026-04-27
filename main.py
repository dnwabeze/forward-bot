import asyncio
import logging

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat

import config
import bridge
from detector import extract_cas

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Deduplication ──────────────────────────────────────────────────────────────

seen_cas: set[str] = set()


def load_seen_cas() -> None:
    try:
        with open(config.DEDUP_FILE, "r") as f:
            for line in f:
                seen_cas.add(line.strip())
        log.info(f"Loaded {len(seen_cas)} previously seen CAs from {config.DEDUP_FILE}")
    except FileNotFoundError:
        pass


def _write_ca(ca: str) -> None:
    with open(config.DEDUP_FILE, "a") as f:
        f.write(ca + "\n")


async def persist_ca(ca: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write_ca, ca)


# ── Group listing (runs on every startup) ─────────────────────────────────────

async def list_all_groups(client: TelegramClient) -> None:
    COL_NAME = 42
    COL_ID   = 22

    header  = f"  {'NAME':<{COL_NAME}} {'ID':<{COL_ID}} TYPE"
    divider = "─" * len(header)

    print()
    print(divider)
    print("  YOUR TELEGRAM GROUPS & CHANNELS")
    print(divider)
    print(header)
    print(divider)

    count = 0
    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue

        entity = dialog.entity
        if isinstance(entity, Channel):
            kind = "Supergroup" if entity.megagroup else "Channel"
            env_id = str(dialog.id)          # already -100XXXXXXXXXX in Telethon
        elif isinstance(entity, Chat):
            kind = "Group"
            env_id = str(dialog.id)          # already negative
        else:
            continue

        name = dialog.name or "(no name)"
        print(f"  {name[:COL_NAME]:<{COL_NAME}} {env_id:<{COL_ID}} {kind}")
        count += 1

    print(divider)
    print(f"  {count} group(s)/channel(s) found.")
    print("  Copy the IDs you want into MONITORED_GROUPS in your .env")
    print(divider)
    print()


# ── Group resolution ───────────────────────────────────────────────────────────

async def resolve_groups(client: TelegramClient) -> tuple[list, dict[int, str]]:
    """Returns (entities, title_cache) — title_cache pre-populated to avoid
    any get_chat() call inside the event handler hot path."""
    resolved = []
    title_cache: dict[int, str] = {}
    for raw in config.MONITORED_GROUPS:
        try:
            entity = await client.get_entity(raw)
            title  = getattr(entity, "title", getattr(entity, "username", raw))
            title_cache[entity.id] = title
            log.info(f"Monitoring: {title} (id={entity.id})")
            resolved.append(entity)
        except Exception as e:
            log.warning(f"Could not resolve group '{raw}': {e}")
    return resolved, title_cache


# ── Event handler ──────────────────────────────────────────────────────────────

def register_handler(
    client: TelegramClient,
    sigma_entity,
    monitored_ids: set[int],
    title_cache: dict[int, str],
) -> None:
    @client.on(events.NewMessage(chats=list(monitored_ids)))
    async def on_message(event: events.NewMessage.Event) -> None:
        # Fast loop-guard: sender_id is available immediately, no network call
        if event.sender_id == sigma_entity.id:
            return

        text = event.raw_text
        if not text:
            return

        cas = extract_cas(text)
        if not cas:
            return

        # Title was pre-populated at startup — zero awaits in the hot path
        chat_title = title_cache.get(event.chat_id, str(event.chat_id))

        for ca, chain in cas:
            if ca in seen_cas:
                log.info(f"[SKIP-DUP]  {chain} | {ca}")
                continue

            log.info(f"[DETECTED]  {chain} | {ca} | src: {chat_title}")

            try:
                await client.send_message(sigma_entity, ca)
                seen_cas.add(ca)
                await persist_ca(ca)   # non-blocking file write
                log.info(f"[FORWARDED] {chain} | {ca} -> {config.SIGMA_BOT}")
            except Exception as e:
                log.error(f"[SEND-FAIL] {ca}: {e} — will retry on next occurrence")


# ── Entry point ────────────────────────────────────────────────────────────────

async def main() -> None:
    load_seen_cas()

    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE_NUMBER)
    log.info("Client authenticated.")

    # Always show all groups/channels on startup
    await list_all_groups(client)

    if not config.MONITORED_GROUPS:
        log.info("MONITORED_GROUPS is empty — add group IDs to .env then restart.")
        await client.disconnect()
        return

    if not config.SIGMA_BOT:
        log.error("SIGMA_BOT_USERNAME is not set in .env. Exiting.")
        await client.disconnect()
        return

    sigma_entity = await client.get_entity(config.SIGMA_BOT)
    log.info(f"Sigma bot resolved: {config.SIGMA_BOT} (id={sigma_entity.id})")

    # Start WhatsApp→Telegram bridge (used by whatsapp_monitor/index.js)
    bridge.init_bridge(client, sigma_entity, seen_cas, persist_ca)
    await bridge.start_bridge_server(config.BRIDGE_PORT)

    monitored, title_cache = await resolve_groups(client)
    if not monitored:
        log.error("No groups resolved. Check MONITORED_GROUPS in .env. Exiting.")
        await client.disconnect()
        return

    monitored_ids = {e.id for e in monitored}
    register_handler(client, sigma_entity, monitored_ids, title_cache)

    log.info(f"Listening on {len(monitored_ids)} group(s). Waiting for CAs...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
