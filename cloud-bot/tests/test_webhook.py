import hashlib, hmac, importlib.util, json, os, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch

class WebhookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ.update(STATE_DB=cls.temp.name + '/test.sqlite', META_APP_SECRET='test-secret', WEBHOOK_VERIFY_TOKEN='test-verify', MOCK_SEND='true')
        spec = importlib.util.spec_from_file_location('bot', Path(__file__).resolve().parents[1] / 'app.py')
        cls.bot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bot)
        cls.seen = []
        cls.bot.process = lambda *args: cls.seen.append(args)
        cls.client = cls.bot.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.bot.JOBS.join()
        cls.temp.cleanup()

    def post(self, message, signed=True):
        raw = json.dumps({'entry':[{'changes':[{'value':{'messages':[message]}}]}]}).encode()
        headers = {'Content-Type':'application/json'}
        if signed:
            headers['X-Hub-Signature-256'] = 'sha256=' + hmac.new(b'test-secret',raw,hashlib.sha256).hexdigest()
        return self.client.post('/webhook', data=raw, headers=headers)

    def message(self, id, text='https://www.instagram.com/reel/example/'):
        return {'id':id,'from':'919999999999','timestamp':str(int(time.time())),'type':'text','text':{'body':text}}

    def test_health_sending_disabled(self):
        self.assertFalse(self.client.get('/health').json['sending_enabled'])

    def test_verification(self):
        self.assertEqual(self.client.get('/webhook?hub.mode=subscribe&hub.verify_token=test-verify&hub.challenge=123').data,b'123')
        self.assertEqual(self.client.get('/webhook?hub.mode=subscribe&hub.verify_token=bad').status_code,403)

    def test_unsigned_rejected(self):
        self.assertEqual(self.post(self.message('unsigned'),False).status_code,403)

    def test_bad_signature_rejected(self):
        self.assertEqual(self.client.post('/webhook',data=b'{}',headers={'X-Hub-Signature-256':'sha256=bad'}).status_code,403)

    def test_reply_recipient_and_dedup(self):
        item = self.message('once')
        self.assertEqual(self.post(item).status_code,200)
        self.bot.JOBS.join()
        self.assertEqual(self.seen.count(('once','919999999999',item['text']['body'])),1)
        self.assertEqual(self.post(item).status_code,200)
        self.bot.JOBS.join()
        self.assertEqual(self.seen.count(('once','919999999999',item['text']['body'])),1)

    def test_untrusted_domain_ignored(self):
        self.post(self.message('bad-domain','https://www.instagram.com.evil.invalid/reel/x'))
        self.bot.JOBS.join()
        self.assertFalse(any(x[0]=='bad-domain' for x in self.seen))

    def test_stale_ignored(self):
        item = self.message('old')
        item['timestamp'] = '1'
        self.post(item)
        self.bot.JOBS.join()
        self.assertFalse(any(x[0]=='old' for x in self.seen))

    def test_missing_credentials_fail_closed(self):
        self.bot.MOCK = False
        try:
            self.assertEqual(self.post(self.message('no-secrets')).status_code,503)
        finally:
            self.bot.MOCK = True

    def test_rate_limited_job_notifies_sender_without_private_details(self):
        with patch.object(self.bot, 'process', side_effect=self.bot.DownloadFailure('http_429')), \
             patch.object(self.bot, 'send_text') as notify:
            self.assertEqual(self.post(self.message('rate-limit')).status_code, 200)
            self.bot.JOBS.join()
            notify.assert_called_once()
            recipient, body = notify.call_args.args
            self.assertEqual(recipient, '919999999999')
            self.assertIn('rate-limiting', body)
            self.assertNotIn('example/', body)

    def test_original_document_preserves_bytes_and_expires(self):
        original = b'original-video-bytes-without-reencoding'
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            source.write_bytes(original)
            with patch.object(self.bot, 'MOCK', False), \
                 patch.dict(os.environ, PUBLIC_BASE_URL='https://example.invalid', WHATSAPP_PHONE_NUMBER_ID='test-phone'), \
                 patch.object(self.bot, 'graph', return_value={'messages': [{'id': 'test'}]}) as graph:
                self.bot.send_original('919999999999', source)
            payload = json.loads(graph.call_args.args[1])
            self.assertEqual(payload['type'], 'document')
            self.assertNotIn('text', payload)
            path = '/media-fetch/' + payload['document']['link'].rsplit('/', 1)[-1]
            with self.client.get(path) as response:
                self.assertEqual(response.data, original)
                self.assertIn('attachment;', response.headers['Content-Disposition'])
                self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
            token = path.rsplit('/', 1)[-1]
            stored_path = self.bot.DOWNLOAD_FILES[token]['path']
            self.bot.DOWNLOAD_FILES[token]['expires'] = 0
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertFalse(stored_path.exists())

if __name__=='__main__':
    unittest.main()

