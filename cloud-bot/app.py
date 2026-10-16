import hashlib, hmac, json, os, queue, re, shutil, sqlite3, subprocess, tempfile, threading, time, urllib.request
from pathlib import Path
from contextlib import contextmanager
import urllib.error
import secrets
from flask import Flask, request, jsonify, send_file, abort

app = Flask(__name__)
app.logger.setLevel('INFO')
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
DB = os.environ.get('STATE_DB', '/tmp/reel-bot.sqlite')
JOBS = queue.Queue(maxsize=20)
MOCK = os.environ.get('MOCK_SEND', 'true').lower() != 'false'
ALLOWED = re.compile(r'https://(?:www\.|m\.)?(?:instagram\.com/(?:reel|reels|p)/[^\s<>]+|youtube\.com/shorts/[^\s<>]+|youtu\.be/[^\s<>]+|facebook\.com/(?:reel/|reels/|share/r/|watch/)[^\s<>]+)', re.I)

class DownloadFailure(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Video download failed')


def failure_text(error):
    if isinstance(error, DownloadFailure) and error.reason == 'original_delivery_rejected':
        return 'WhatsApp could not accept the original video file. It has not been compressed, split, or replaced with a link.'
    if isinstance(error, DownloadFailure) and error.reason == 'download_size_limit':
        return "The original video exceeds this bot's 100 MB download limit. It has not been compressed or split."
    if isinstance(error, DownloadFailure) and error.reason in ('http_429', 'rate_limited', 'login_or_rate_limit'):
        return 'The video source is rate-limiting requests from this server. No video was downloaded. Please try later or send another link.'
    return 'I could not download or send this video. Please check that the link is public and try another short video.'


def send_text(sender, text):
    if not MOCK:
        graph(os.environ['WHATSAPP_PHONE_NUMBER_ID'] + '/messages', json.dumps({
            'messaging_product': 'whatsapp', 'to': sender, 'type': 'text',
            'text': {'body': text}}).encode())


@contextmanager
def db():
    conn = sqlite3.connect(DB, timeout=10)
    try:
        with conn:
            conn.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, sender TEXT, url TEXT, state TEXT, created REAL)')
            yield conn
    finally:
        conn.close()


def graph(path, payload=None, content_type='application/json'):
    token = os.environ['WHATSAPP_ACCESS_TOKEN']
    version = os.environ['GRAPH_API_VERSION']
    req = urllib.request.Request(f'https://graph.facebook.com/{version}/{path}', data=payload,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': content_type})
    operation = path.rsplit('/', 1)[-1]
    app.logger.info('WhatsApp operation=%s started', operation)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.load(response)
        app.logger.info('WhatsApp operation=%s accepted', operation)
        return result
    except urllib.error.HTTPError as error:
        code = subcode = None
        try:
            detail = json.load(error).get('error', {})
            code = detail.get('code') if type(detail.get('code')) is int else None
            subcode = detail.get('error_subcode') if type(detail.get('error_subcode')) is int else None
        except (ValueError, AttributeError, TypeError):
            pass
        app.logger.warning('WhatsApp operation=%s failed http_status=%s api_code=%s api_subcode=%s',
            operation, error.code, code, subcode)
        raise


DOWNLOAD_FILES = {}
DOWNLOAD_LOCK = threading.Lock()
DOWNLOAD_ROOT = Path(tempfile.gettempdir()) / 'reel-bot-originals'
DOWNLOAD_TTL = 3600


def prune_downloads():
    now = time.time()
    with DOWNLOAD_LOCK:
        for token, item in list(DOWNLOAD_FILES.items()):
            if item['expires'] <= now:
                item['path'].unlink(missing_ok=True)
                del DOWNLOAD_FILES[token]


def host_for_meta(source):
    # A short-lived media-fetch URL for Meta; users receive a document, never this URL.
    base = (os.environ.get('PUBLIC_BASE_URL') or os.environ.get('RENDER_EXTERNAL_URL', '')).rstrip('/')
    if not base.startswith('https://'):
        raise ValueError('Public media-fetch base URL is missing')
    prune_downloads()
    token = secrets.token_urlsafe(32)
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_ROOT / (token + source.suffix)
    with DOWNLOAD_LOCK:
        if sum(i['path'].stat().st_size for i in DOWNLOAD_FILES.values()) + source.stat().st_size > 256 * 1024 * 1024:
            raise ValueError('Temporary media storage is full')
        shutil.copyfile(source, target)
        DOWNLOAD_FILES[token] = {'path': target, 'expires': time.time() + DOWNLOAD_TTL}
    return token, base + '/media-fetch/' + token


@app.get('/media-fetch/<token>')
def media_fetch(token):
    prune_downloads()
    with DOWNLOAD_LOCK:
        item = DOWNLOAD_FILES.get(token)
        if item is None:
            abort(404)
        path = item['path']
    response = send_file(path, mimetype='video/mp4', as_attachment=True, download_name='video' + path.suffix)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def send_original(sender, source):
    if MOCK:
        return
    token, url = host_for_meta(source)
    try:
        graph(os.environ['WHATSAPP_PHONE_NUMBER_ID'] + '/messages', json.dumps({
            'messaging_product': 'whatsapp', 'to': sender, 'type': 'document',
            'document': {'link': url, 'filename': 'video' + source.suffix}}).encode())
    except Exception as error:
        with DOWNLOAD_LOCK:
            item = DOWNLOAD_FILES.pop(token, None)
            if item:
                item['path'].unlink(missing_ok=True)
        raise DownloadFailure('original_delivery_rejected') from error


def process(job_id, sender, url):
    with tempfile.TemporaryDirectory(prefix='reel-bot-') as directory:
        app.logger.info('Original-quality video download started')
        download = subprocess.run(['python', '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
            '--max-filesize', '100M', '--socket-timeout', '15', '--retries', '1',
            '--format', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            '--merge-output-format', 'mp4', '--output', directory + '/source.%(ext)s', '--', url],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180, text=True)
        if download.returncode:
            diagnostic = (download.stderr or '').lower()
            reason = 'extractor_error'
            for marker, label in [('requested format is not available', 'format_unavailable'),
                ('login required', 'login_required'), ('login', 'login_or_rate_limit'),
                ('rate-limit', 'rate_limited'), ('http error 403', 'http_403'),
                ('http error 429', 'http_429'), ('not available', 'content_unavailable'),
                ('timed out', 'network_timeout'), ('too large', 'download_size_limit')]:
                if marker in diagnostic:
                    reason = label
                    break
            app.logger.warning('Video download rejected reason=%s', reason)
            raise DownloadFailure(reason)
        source = next(p for p in Path(directory).glob('source.*') if p.suffix not in ('.part', '.ytdl'))
        if source.stat().st_size > 100 * 1024 * 1024:
            raise DownloadFailure('download_size_limit')
        app.logger.info('Original video ready bytes=%s; no re-encoding, resizing, or trimming', source.stat().st_size)
        send_original(sender, source)


def worker():
    while True:
        job_id, sender, url = JOBS.get()
        state = 'done'
        started = time.monotonic()
        app.logger.info('Video job started')
        try:
            process(job_id, sender, url)
        except Exception as error:
            state = 'failed'
            app.logger.warning('Video job failed error_type=%s exit_code=%s elapsed_seconds=%.1f',
                type(error).__name__, getattr(error, 'returncode', None), time.monotonic() - started)
            try:
                send_text(sender, failure_text(error))
            except Exception as notification_error:
                app.logger.warning('Failure notification failed error_type=%s', type(notification_error).__name__)
        finally:
            with db() as conn:
                conn.execute('UPDATE jobs SET state=? WHERE id=?',(state, job_id))
            JOBS.task_done()
            app.logger.info('Video job finished state=%s elapsed_seconds=%.1f', state, time.monotonic() - started)


@app.get('/health')
def health():
    return jsonify(status='ok', sending_enabled=not MOCK)


@app.get('/privacy')
def privacy():
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daas WhatsApp Downloader — Privacy policy</title>
<style>body{font:17px/1.65 system-ui,sans-serif;max-width:760px;margin:48px auto;padding:0 24px;color:#17252a}h1,h2{line-height:1.25}a{color:#075e54}</style>
<main><h1>Daas WhatsApp Downloader</h1><h2>Privacy policy</h2><p>Updated 16 October 2026. Operated by Manmeet Sood.</p>
<p>This bot processes public video links you send through WhatsApp so it can attempt to download and return the video. Only submit links you have permission to use. The service is a prototype and may be unavailable or unable to download a link.</p>
<h2>Information processed</h2><p>The server receives WhatsApp webhook events, which may include your WhatsApp number, message text, message identifier, timestamp and other metadata provided by Meta. For accepted video requests, it stores your number, the matched video URL, message identifier, processing status and creation time in a local database to process requests and prevent duplicate work. The complete message text is not stored in that database.</p>
<h2>Use and sharing</h2><p>Information is used to handle your request and operate the bot. Render hosts the server. Meta provides WhatsApp messaging and, when sending is enabled, receives your recipient number and the video for delivery. Video platforms receive download requests for the submitted URLs. These providers may process service and network data under their own policies. The bot does not implement advertising, tracking cookies or a sale of your information.</p>
<h2>Retention</h2><p>Original video files are held temporarily for Meta to fetch and deliver as a document. Access requires an unguessable media-fetch URL. These files expire after one hour, may be lost earlier on a service restart, and are removed by periodic cleanup. Videos are not re-encoded, resized or trimmed by the bot. Request records remain in the local database until removed by the operator or lost when Render replaces the service's temporary filesystem. There is currently no scheduled record-expiration process. Queued requests may be lost on restart. Render, Meta and video platforms may retain their own logs or copies under their policies. WhatsApp chat history and delivered videos are separate from the bot's temporary files.</p>
<h2>Your choices and contact</h2><p>Stop sending messages to stop making new requests. For privacy questions or deletion requests, contact the operator using the contact details on this number's WhatsApp business profile. Include only enough information to identify your request; never send passwords or access tokens. Requests require manual review. Do not post your phone number or private messages in public repository issues.</p>
<p><a href="/data-deletion">Data deletion instructions</a></p></main></html>'''


@app.get('/data-deletion')
def data_deletion():
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daas — Data deletion</title><style>body{font:17px/1.65 system-ui,sans-serif;max-width:760px;margin:48px auto;padding:0 24px;color:#17252a}</style>
<main><h1>Data deletion instructions</h1><p>Contact the operator using the contact details on this bot number's WhatsApp business profile and request deletion of your bot request records. Identify the WhatsApp number you used and, if available, the approximate date of your request. Never provide passwords or access tokens. This is a manual request, not an automated deletion command.</p>
<p>Bot request records contain the sender number, submitted video URL, message identifier, processing status and creation time. Temporary original video files expire after one hour or may be lost earlier on restart. Deleting bot records does not remove messages or videos already delivered to WhatsApp, or copies and logs retained independently by Meta, Render or video platforms. Manage your WhatsApp chat history separately.</p><p><a href="/privacy">Privacy policy</a></p></main></html>'''


@app.get('/webhook')
def verify():
    token = os.environ.get('WEBHOOK_VERIFY_TOKEN', '')
    if token and request.args.get('hub.mode') == 'subscribe' and hmac.compare_digest(request.args.get('hub.verify_token',''), token):
        return request.args.get('hub.challenge',''), 200
    return '', 403


@app.post('/webhook')
def webhook():
    secret = os.environ.get('META_APP_SECRET', '')
    raw = request.get_data()
    expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(request.headers.get('X-Hub-Signature-256',''), expected):
        return '', 403
    if not MOCK and not all(os.environ.get(k) for k in ('WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','GRAPH_API_VERSION')):
        return '', 503
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return '', 400
    for entry in data.get('entry', []):
        for change in entry.get('changes', []):
            for message in change.get('value', {}).get('messages', []):
                if message.get('type') != 'text':
                    continue
                match = ALLOWED.search(message.get('text', {}).get('body',''))
                sender, job_id = message.get('from',''), message.get('id','')
                if not match or not sender.isdigit() or not job_id:
                    continue
                # Old redeliveries must never trigger out-of-window replies.
                try:
                    if time.time() - int(message['timestamp']) > 600:
                        continue
                except (KeyError, ValueError, TypeError):
                    continue
                with db() as conn:
                    try:
                        conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (job_id,sender,match[0],'queued',time.time()))
                    except sqlite3.IntegrityError:
                        continue
                    try:
                        JOBS.put_nowait((job_id,sender,match[0]))
                        app.logger.info('Video request queued')
                    except queue.Full:
                        conn.execute('DELETE FROM jobs WHERE id=?',(job_id,))
                        return '', 503
    return '', 200

threading.Thread(target=worker, daemon=True).start()

# Optional one-off deployment probe. It never sends a WhatsApp message.
def deployment_probe():
    probe_url = os.environ.get('STARTUP_PROBE_URL', '')
    if not probe_url or not MOCK or not ALLOWED.fullmatch(probe_url):
        return
    try:
        process('deployment-probe', '', probe_url)
        app.logger.info('Deployment download probe passed')
    except Exception as error:
        app.logger.warning('Deployment download probe failed error_type=%s reason=%s',
            type(error).__name__, getattr(error, 'reason', 'processing_error'))


threading.Thread(target=deployment_probe, daemon=True).start()


def media_cleanup_worker():
    while True:
        time.sleep(60)
        prune_downloads()


threading.Thread(target=media_cleanup_worker, daemon=True).start()

