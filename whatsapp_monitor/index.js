import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} from '@whiskeysockets/baileys';
import axios from 'axios';
import dotenv from 'dotenv';
import pino from 'pino';
import qrcode from 'qrcode-terminal';
import path from 'path';
import { fileURLToPath } from 'url';
import { extractCAs } from './detector.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const BRIDGE_URL = `http://127.0.0.1:${process.env.BRIDGE_PORT || 5050}/forward-ca`;

// Parse WA_MONITORED_GROUPS — empty means monitor ALL groups
const MONITORED_GROUPS = new Set(
  (process.env.WA_MONITORED_GROUPS || '')
    .split(',')
    .map(s => s.trim())
    .filter(Boolean)
);

const logger = pino({ level: 'silent' }); // suppress Baileys noise

async function listAllGroups(sock) {
  try {
    const groups = await sock.groupFetchAllParticipating();
    const entries = Object.values(groups).sort((a, b) =>
      (a.subject || '').localeCompare(b.subject || '')
    );

    const COL = 45;
    const divider = '─'.repeat(COL + 30);
    console.log(divider);
    console.log('  YOUR WHATSAPP GROUPS');
    console.log(divider);
    console.log(`  ${'NAME'.padEnd(COL)} JID`);
    console.log(divider);
    for (const g of entries) {
      const name = (g.subject || '(no name)').slice(0, COL).padEnd(COL);
      console.log(`  ${name} ${g.id}`);
    }
    console.log(divider);
    console.log(`  ${entries.length} group(s) found.`);
    console.log('  Copy the JIDs you want into WA_MONITORED_GROUPS in your .env');
    console.log(divider + '\n');

    if (MONITORED_GROUPS.size === 0) {
      console.log('[WA] WA_MONITORED_GROUPS not set — monitoring ALL groups');
      console.log('[WA] Tip: set WA_MONITORED_GROUPS=<JID1>,<JID2> in .env to restrict\n');
    } else {
      console.log(`[WA] Monitoring ${MONITORED_GROUPS.size} group(s):`);
      for (const jid of MONITORED_GROUPS) {
        if (!jid.endsWith('@g.us')) {
          console.log(`  ✗ "${jid}" — INVALID (must be a JID ending in @g.us, not a group name)`);
        } else {
          console.log(`  • ${jid}`);
        }
      }
      console.log('');
    }
  } catch (err) {
    console.error('[WA] Could not fetch groups:', err.message);
  }
}

async function startWatcher() {
  const { state, saveCreds } = await useMultiFileAuthState(
    path.join(__dirname, 'wa_session')
  );
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    logger,
    markOnlineOnConnect: false,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log('\n[WA] Scan this QR code with WhatsApp (Settings → Linked Devices → Link a Device):\n');
      qrcode.generate(qr, { small: true });
    }
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = code !== DisconnectReason.loggedOut;
      console.log(`[WA] Connection closed (code=${code}). Reconnecting: ${shouldReconnect}`);
      if (shouldReconnect) startWatcher();
      else console.log('[WA] Logged out — delete wa_session/ and restart to re-link');
    } else if (connection === 'open') {
      console.log('[WA] Connected to WhatsApp\n');
      await listAllGroups(sock);
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    // 'notify' = new message from others, 'append' = sent by self or history sync
    if (type !== 'notify' && type !== 'append') return;

    await Promise.all(messages.map(async (msg) => {
      if (!msg.message) return;

      const jid = msg.key.remoteJid;
      if (!jid?.endsWith('@g.us')) return; // groups only

      // Filter by monitored groups (if configured)
      if (MONITORED_GROUPS.size > 0 && !MONITORED_GROUPS.has(jid)) return;

      const text =
        msg.message.conversation ||
        msg.message.extendedTextMessage?.text ||
        msg.message.imageMessage?.caption ||
        msg.message.videoMessage?.caption ||
        '';

      if (!text) return;

      console.log(`[WA-MSG] ${jid} | "${text.slice(0, 80)}"`);

      const cas = extractCAs(text);
      if (!cas.length) {
        console.log(`[WA-NO-CA] No contract address found in that message`);
        return;
      }

      await Promise.all(cas.map(async ({ ca, chain }) => {
        try {
          const res = await axios.post(BRIDGE_URL, {
            ca,
            chain,
            source: `WhatsApp:${jid}`,
          });
          const status = res.data?.status;
          if (status === 'ok')  console.log(`[WA-FORWARDED] ${chain} | ${ca}`);
          if (status === 'dup') console.log(`[WA-SKIP-DUP]  ${chain} | ${ca}`);
        } catch (err) {
          const detail = err.response?.data?.reason || err.message;
          console.error(`[WA-SEND-FAIL] ${ca}: ${detail}`);
        }
      }));
    }));
  });
}

startWatcher().catch(err => {
  console.error('[WA] Fatal startup error:', err);
  process.exit(1);
});
