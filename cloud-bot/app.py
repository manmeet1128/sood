import hashlib, hmac, json, os, queue, re, shutil, sqlite3, subprocess, tempfile, threading, time, urllib.request
from pathlib import Path
from flask import Flask, request, jsonify

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
DB = os.environ.get('STATE_DB', '/tmp/reel-bot.sqlite')
JOBS = queue.Queue(maxsize=20)
MOCK = os.environ.get('MOCK_SEND', 'true').lower() != 'false'
ALLOWED = re.compile(r'https://(?:www\.|m\.)?(?:instagram\.com/(?:reel|reels|p)/[^\s<>]+|youtube\.com/shorts/[^\s<>]+|youtu\.be/[^\s<>]+|facebook\.com/(?:reel/|reels/|share/r/|watch/)[^\s<>]+)', re.I)

def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, sender TEXT, url TEXT, state TEXT, created REAL)')
    return conn


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
