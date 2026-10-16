# Linked WhatsApp video bot

Uses https://github.com/WhiskeySockets/Baileys (MIT), pinned to baileys 7.0.0-rc14, with yt-dlp. This is an alternative transport, not an extension of Meta's Cloud API. A phone account must pair via WhatsApp Linked devices; Meta access tokens cannot pair it. Do not deregister the existing API number as part of setup.

The bot receives fresh direct-message public Reel/Short links, downloads the best compatible H264/AAC MP4 source streams, and submits one normal video using Baileys. Merging separate streams copies media without encoding. It never compresses, resizes, splits, provides a download link, or sends a document. The 100 MB ceiling is an application resource limit, not a promise that WhatsApp accepts every file below it. Actual delivery and received-file quality need an end-to-end paired test. Only use content you have permission to download.

## Free cloud setup

Render Docker web service: root directory `linked-bot`, Dockerfile `./Dockerfile`, region Frankfurt, plan Free, health path `/health`. Set a random `ADMIN_PASSWORD` of at least 24 characters. Open the HTTPS service root, sign in with user `admin` and that password, then scan the QR on the bot phone. QR and session credentials are never logged. Only the password-protected page exposes the QR. Do not publish its screenshot.

Free Render sleeps after inactivity and does not provide persistent disks. `/tmp/sood-linked-auth` contains sensitive session keys and can disappear on redeploy/restart: pairing must then be repeated. Queued jobs/dedup records are in memory; temporary videos are deleted after processing. This free setup is not continuously available. Do not use artificial keep-alive traffic. The paid persistent disk option has not been enabled.

Baileys is unofficial; account restrictions/disconnects are possible. Pair a bot account you control and use it only for requested replies. Never commit keys, expose the pairing page without authentication, or send session files to third-party pairing sites. Disconnect through the phone's Linked devices screen. No paid services, proxies, or API subscriptions are needed.

Run `pnpm install --frozen-lockfile --ignore-scripts`, `npm test`, then `ADMIN_PASSWORD=... npm start` on a Node 24 Linux host with Python, yt-dlp and ffmpeg. Node core tests cover allowed URL handling and unchanged-byte native-video submission with a fake socket; they do not prove WhatsApp delivery.

