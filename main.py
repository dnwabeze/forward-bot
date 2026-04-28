import asyncio
import logging
import subprocess
import os
import sys
import signal

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
            env_id = str(dialog.id)
        elif isinstance(entity, Chat):
            kind = "Group"
            env_id = str(dialog.id)
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
    senders: list,   # list of (TelegramClient, entity) tuples
    monitored_ids: set[int],
    title_cache: dict[int, str],
) -> None:
    sender_bot_ids = {entity.id for _, entity in senders}

    @client.on(events.NewMessage(chats=list(monitored_ids)))
    async def on_message(event: events.NewMessage.Event) -> None:
        if event.sender_id in sender_bot_ids:
            return

        text = event.raw_text
        if not text:
            return

        cas = extract_cas(text)
        if not cas:
            return

        chat_title = title_cache.get(event.chat_id, str(event.chat_id))

        for ca, chain in cas:
            if ca in seen_cas:
                log.info(f"[SKIP-DUP]  {chain} | {ca}")
                continue

            log.info(f"[DETECTED]  {chain} | {ca} | src: {chat_title}")

            sent = False
            for sender_client, entity in senders:
                try:
                    await sender_client.send_message(entity, ca)
                    name = getattr(entity, "username", None) or str(entity.id)
                    log.info(f"[FORWARDED] {chain} | {ca} -> {name}")
                    sent = True
                except Exception as e:
                    log.error(f"[SEND-FAIL] {ca} -> {getattr(entity, 'username', entity.id)}: {e}")

            if sent:
                seen_cas.add(ca)
                await persist_ca(ca)


# ── Node Monitor ───────────────────────────────────────────────────────────────

def start_node_monitor():
    """Spawns the Node.js WhatsApp monitor in a background process."""
    monitor_dir = os.path.join(os.getcwd(), "whatsapp_monitor")
    if not os.path.exists(monitor_dir):
        log.warning(f"whatsapp_monitor directory not found at {monitor_dir}. Skipping monitor.")
        return None

    log.info("Starting WhatsApp monitor (Node.js)...")
    try:
        process = subprocess.Popen(
            ["node", "index.js"],
            cwd=monitor_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        def relay_logs():
            for line in iter(process.stdout.readline, ""):
                if line:
                    print(f" [NODE] {line.strip()}")
            process.stdout.close()

        import threading
        threading.Thread(target=relay_logs, daemon=True).start()

        return process
    except Exception as e:
        log.error(f"Failed to start Node.js monitor: {e}")
        return None


# ── Entry point ────────────────────────────────────────────────────────────────

async def main() -> None:
    load_seen_cas()

    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE_NUMBER)
    log.info("Client authenticated.")

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

    senders = [(client, sigma_entity)]

    # Friend's account — optional second sender, reuses same API_ID and API_HASH
    friend_client = None
    if config.FRIEND_PHONE_NUMBER and config.FRIEND_SIGMA_BOT:
        friend_client = TelegramClient(config.FRIEND_SESSION_NAME, config.API_ID, config.API_HASH)
        await friend_client.start(phone=config.FRIEND_PHONE_NUMBER)
        log.info("Friend client authenticated.")
        friend_sigma_entity = await friend_client.get_entity(config.FRIEND_SIGMA_BOT)
        log.info(f"Friend's sigma bot resolved: {config.FRIEND_SIGMA_BOT} (id={friend_sigma_entity.id})")
        senders.append((friend_client, friend_sigma_entity))

    bridge.init_bridge(senders, seen_cas, persist_ca)
    await bridge.start_bridge_server(config.BRIDGE_PORT)

    node_proc = start_node_monitor()

    monitored, title_cache = await resolve_groups(client)
    if not monitored:
        log.error("No groups resolved. Check MONITORED_GROUPS in .env. Exiting.")
        await client.disconnect()
        return

    monitored_ids = {e.id for e in monitored}
    register_handler(client, senders, monitored_ids, title_cache)

    log.info(f"Listening on {len(monitored_ids)} group(s). Waiting for CAs...")

    try:
        await client.run_until_disconnected()
    finally:
        if node_proc:
            log.info("Stopping Node.js monitor...")
            node_proc.terminate()
            try:
                node_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                node_proc.kill()
        if friend_client:
            await friend_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
