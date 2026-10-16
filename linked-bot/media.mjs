import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {stat} from 'node:fs/promises';
const run = promisify(execFile);
export const MAX_BYTES = 100_000_000;
export function extractURL(text) {
  for (const match of String(text).matchAll(/https:\/\/[^\s<>]+/gi)) {
    try {
      const u = new URL(match[0]);
      const host = u.hostname.replace(/^(www\.|m\.)/, '');
      if (u.username || u.password || (u.port && u.port !== '443')) continue;
      const valid = (host === 'instagram.com' && /^\/(reel|reels|p)\/[\w-]+\/?$/.test(u.pathname)) ||
        (host === 'youtube.com' && /^\/shorts\/[\w-]+\/?$/.test(u.pathname)) ||
        (host === 'youtu.be' && /^\/[\w-]+\/?$/.test(u.pathname)) ||
        (host === 'facebook.com' && /^\/(reel|reels|share\/r)\/[\w-]+\/?$/.test(u.pathname));
      if (valid) {u.search = ''; u.hash = ''; return u.href;}
    } catch {}
  }
  return null;
}
export async function validateVideo(file) {
  const size = (await stat(file)).size;
  if (!size || size > MAX_BYTES) throw Error('size_limit');
  const {stdout} = await run('ffprobe', ['-v','error','-show_entries','stream=codec_type,codec_name','-of','json',file], {timeout:15_000});
  const streams = JSON.parse(stdout).streams || [];
  const video = streams.filter(s => s.codec_type === 'video');
  const audio = streams.filter(s => s.codec_type === 'audio');
  if (video.length !== 1 || video[0].codec_name !== 'h264' || audio.length > 1 || audio.some(s => s.codec_name !== 'aac')) throw Error('unsupported_codec');
  return size;
}
export async function download(url, directory) {
  const {stdout} = await run(process.env.PYTHON || 'python3', ['download.py', url, directory], {timeout:240_000, maxBuffer:8192});
  return stdout.trim();
}
export async function sendOriginal(sock, jid, file) {
  const bytes = await validateVideo(file);
  await sock.sendMessage(jid, {video:{url:file}, mimetype:'video/mp4', ptv:false});
  return bytes;
}

