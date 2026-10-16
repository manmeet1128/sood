import hashlib, hmac, json, os, queue, re, shutil, sqlite3, subprocess, tempfile, threading, time, urllib.request
from pathlib import Path
from contextlib import contextmanager
from flask import Flask, request, jsonify

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
DB = os.environ.get('STATE_DB', '/tmp/reel-bot.sqlite')
JOBS = queue.Queue(maxsize=20)
MOCK = os.environ.get('MOCK_SEND', 'true').lower() != 'false'
ALLOWED = re.compile(r'https://(?:www\.|m\.)?(?:instagram\.com/(?:reel|reels|p)/[^\s<>]+|youtube\.com/shorts/[^\s<>]+|youtu\.be/[^\s<>]+|facebook\.com/(?:reel/|reels/|share/r/|watch/)[^\s<>]+)', re.I)

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
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def send_video(sender, video):
    if MOCK:
        return
    boundary = 'reelbot' + os.urandom(12).hex()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\nvideo/mp4\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="video.mp4"\r\nContent-Type: video/mp4\r\n\r\n').encode()
    body += video.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    phone = os.environ['WHATSAPP_PHONE_NUMBER_ID']
    media = graph(phone + '/media', body, 'multipart/form-data; boundary=' + boundary)
    graph(phone + '/messages', json.dumps({'messaging_product':'whatsapp','to':sender,
        'type':'video','video':{'id':media['id']}}).encode())


def process(job_id, sender, url):
    with tempfile.TemporaryDirectory(prefix='reel-bot-') as directory:
        subprocess.run(['python', '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
            '--max-filesize', '30M', '--socket-timeout', '15', '--retries', '1',
            '--format', 'best[ext=mp4][height<=720]/best[height<=720]',
            '--output', directory + '/source.%(ext)s', '--', url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True)
        source = next(p for p in Path(directory).glob('source.*') if p.suffix not in ('.part','.ytdl'))
        if source.stat().st_size > 30 * 1024 * 1024:
            raise ValueError('Download too large')
        target = Path(directory) / 'video.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(source),'-t','180',
            '-vf',"scale='min(720,iw)':-2",'-c:v','libx264','-preset','veryfast','-crf','28',
            '-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',str(target)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True)
        if target.stat().st_size > 15 * 1024 * 1024:
            raise ValueError('Video too large for this bot')
        send_video(sender, target)


def worker():
    while True:
        job_id, sender, url = JOBS.get()
        state = 'done'
        try:
            process(job_id, sender, url)
        except Exception:
            state = 'failed'
            app.logger.warning('A download or send failed; no message contents or credentials logged')
        finally:
            with db() as conn:
                conn.execute('UPDATE jobs SET state=? WHERE id=?',(state, job_id))
            JOBS.task_done()


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
<h2>Retention</h2><p>Downloaded and converted video files are temporary and are removed when processing finishes or fails. Request records remain in the local database until removed by the operator or lost when Render replaces the service's temporary filesystem. There is currently no scheduled record-expiration process. Queued requests may be lost on restart. Render, Meta and video platforms may retain their own logs or copies under their policies. WhatsApp chat history and delivered videos are separate from the bot's temporary files.</p>
<h2>Your choices and contact</h2><p>Stop sending messages to stop making new requests. For privacy questions or deletion requests, contact the operator using the contact details on this number's WhatsApp business profile. Include only enough information to identify your request; never send passwords or access tokens. Requests require manual review. Do not post your phone number or private messages in public repository issues.</p>
<p><a href="/data-deletion">Data deletion instructions</a></p></main></html>'''


@app.get('/data-deletion')
def data_deletion():
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daas — Data deletion</title><style>body{font:17px/1.65 system-ui,sans-serif;max-width:760px;margin:48px auto;padding:0 24px;color:#17252a}</style>
<main><h1>Data deletion instructions</h1><p>Contact the operator using the contact details on this bot number's WhatsApp business profile and request deletion of your bot request records. Identify the WhatsApp number you used and, if available, the approximate date of your request. Never provide passwords or access tokens. This is a manual request, not an automated deletion command.</p>
<p>Bot request records contain the sender number, submitted video URL, message identifier, processing status and creation time. Downloaded video files are removed after processing. Deleting bot records does not remove messages or videos already delivered to WhatsApp, or copies and logs retained independently by Meta, Render or video platforms. Manage your WhatsApp chat history separately.</p><p><a href="/privacy">Privacy policy</a></p></main></html>'''


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
                    except queue.Full:
                        conn.execute('DELETE FROM jobs WHERE id=?',(job_id,))
                        return '', 503
    return '', 200

threading.Thread(target=worker, daemon=True).start()

