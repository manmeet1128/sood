import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {extractURL, sendOriginal} from './media.mjs';

test('only supported HTTPS video links; strip tracking', () => {
  assert.equal(extractURL('get https://www.instagram.com/reel/DeFrmX9RNSY/?foo=bar'), 'https://www.instagram.com/reel/DeFrmX9RNSY/');
  for (const url of ['https://instagram.com.evil.test/reel/a/', 'https://user:secret@instagram.com/reel/a/', 'https://127.0.0.1/a', 'https://instagram.com:8443/reel/a/', 'http://instagram.com/reel/a/']) assert.equal(extractURL(url),null);
});
test('normal video submission preserves exact file bytes', async () => {
  const dir = await mkdtemp(join(tmpdir(),'sood-test-'));
  try {
    const file = join(dir,'original.mp4');
    execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','color=c=blue:s=128x128:d=1','-c:v','libx264',file]);
    const before = await readFile(file);
    let called = false;
    await sendOriginal({sendMessage:async (jid, payload) => {
      called = true;
      assert.equal(jid,'123@s.whatsapp.net');
      assert.deepEqual(Object.keys(payload).sort(),['mimetype','ptv','video']);
      assert.deepEqual(await readFile(payload.video.url),before);
    }}, '123@s.whatsapp.net', file);
    assert.equal(called,true);
    assert.deepEqual(await readFile(file),before);
  } finally {await rm(dir,{recursive:true,force:true});}
});

