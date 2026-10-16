import makeWASocket, {DisconnectReason, useMultiFileAuthState} from 'baileys';
import pino from 'pino';
import QRCode from 'qrcode';
import {createServer} from 'node:http';
import {timingSafeEqual} from 'node:crypto';
import {mkdtemp, rm, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {extractURL, download, sendOriginal} from './media.mjs';

const password = process.env.ADMIN_PASSWORD || '';
if (password.length < 24) throw Error('ADMIN_PASSWORD must have at least 24 characters');
const authDir = process.env.AUTH_DIR || '/tmp/sood-linked-auth';
await mkdir(authDir, {recursive:true, mode:0o700});
const {state, saveCreds} = await useMultiFileAuthState(authDir);
let sock, qr = '', status = 'connecting', stopping = false, running = false;
let reconnectTimer;
const queue = [], seen = new Map();
const log = event => console.log(JSON.stringify({event}));
const logger = pino({level:'silent'});

async function drain() {
  if (running || status !== 'connected') return;
  running = true;
  try {
    while (queue.length && status === 'connected' && !stopping) {
      const {jid, url} = queue.shift();
      const directory = await mkdtemp(join(tmpdir(), 'sood-video-'));
      try {
        log('download_started');
        const file = await download(url, directory);
        await sendOriginal(sock, jid, file);
        log('video_submitted_without_reencoding');
      } catch {
        log('video_failed');
        try {await sock.sendMessage(jid, {text:'Unable to download or send this original as one video. It has not been compressed, split, or sent as a document or link.'});} catch {}
      } finally {await rm(directory, {recursive:true, force:true});}
    }
  } finally {running = false;}
}

function connect() {
  if (stopping) return;
  sock = makeWASocket({auth:state, logger, markOnlineOnConnect:false, syncFullHistory:false,
    getMessage:async () => undefined});
  sock.ev.on('creds.update', () => saveCreds().catch(() => log('credential_save_failed')));
  sock.ev.on('connection.update', update => {
    if (update.qr) {qr = update.qr; status = 'awaiting_pairing';}
    if (update.connection === 'open') {qr = ''; status = 'connected'; log('whatsapp_connected'); void drain();}
    if (update.connection === 'close') {
      qr = '';
      const code = update.lastDisconnect?.error?.output?.statusCode;
      status = code === DisconnectReason.loggedOut ? 'logged_out' : 'reconnecting';
      log(status);
      if (status !== 'logged_out' && !stopping) reconnectTimer = setTimeout(connect, 5000);
    }
  });
  sock.ev.on('messages.upsert', event => {
    if (event.type !== 'notify') return;
    for (const message of event.messages) {
      const jid = message.key.remoteJid;
      if (message.key.fromMe || !jid || !/^[\d:]+@(s\.whatsapp\.net|lid)$/.test(jid)) continue;
      const age = Date.now()/1000 - Number(message.messageTimestamp);
      if (!Number.isFinite(age) || age > 300 || age < -60) continue;
      const text = message.message?.conversation || message.message?.extendedTextMessage?.text;
      const url = extractURL(text);
      if (!url || !message.key.id || queue.length >= 20) continue;
      for (const [id, expiry] of seen) if (expiry < Date.now()) seen.delete(id);
      const id = jid + ':' + message.key.id;
      if (seen.has(id)) continue;
      seen.set(id, Date.now()+600_000);
      queue.push({jid,url}); log('video_queued'); void drain();
    }
  });
}

const server = createServer(async (req, res) => {
  res.setHeader('Cache-Control','no-store');
  res.setHeader('X-Content-Type-Options','nosniff');
  res.setHeader('Referrer-Policy','no-referrer');
  if (req.url === '/health') {res.setHeader('Content-Type','application/json'); return res.end(JSON.stringify({status:'ok',whatsapp:status}));}
  const expected = Buffer.from('Basic '+Buffer.from('admin:'+password).toString('base64'));
  const supplied = Buffer.from(req.headers.authorization || '');
  if (supplied.length !== expected.length || !timingSafeEqual(supplied,expected)) {
    res.writeHead(401, {'WWW-Authenticate':'Basic realm="Bot pairing"'}); return res.end('Sign in to pair the bot.');
  }
  if (req.url !== '/') {res.writeHead(404); return res.end();}
  res.setHeader('Content-Type','text/html; charset=utf-8');
  const image = qr ? await QRCode.toDataURL(qr) : '';
  res.end(`<!doctype html><meta name="viewport" content="width=device-width"><meta http-equiv="refresh" content="10"><title>Daas linked bot</title><style>body{font:18px system-ui;max-width:650px;margin:40px auto;padding:20px}img{width:320px;max-width:100%}</style><h1>Daas linked video bot</h1><p>Status: ${status}</p>${image ? `<img alt="WhatsApp pairing QR" src="${image}"><p>Open the bot phone's WhatsApp → Linked devices → Link a device. Scan this QR yourself. Pairing gives this cloud service linked-device access to that account.</p>` : ''}<p>Original compatible videos only. No re-encoding, documents, download links or parts. Delivery still depends on WhatsApp accepting the file.</p><p>Free hosting sleeps and may lose the session on restart, requiring another scan.</p>`);
});
server.listen(Number(process.env.PORT || 10000), '0.0.0.0');
connect();
process.on('SIGTERM', () => {
  stopping = true; clearTimeout(reconnectTimer); sock?.end(undefined); server.close();
  setTimeout(() => process.exit(0), 10_000).unref();
});

