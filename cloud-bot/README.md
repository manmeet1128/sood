# WhatsApp original-quality video downloader

Deploy the Docker service from cloud-bot on Render's free plan. Configure WEBHOOK_VERIFY_TOKEN, META_APP_SECRET, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID and GRAPH_API_VERSION privately. Point Meta to /webhook and subscribe to messages. MOCK_SEND defaults to true; false enables sending.

Normal WhatsApp videos only: the bot downloads a compatible native MP4/H.264 video with AAC audio and sends its bytes unchanged. If separate native streams are selected, yt-dlp merges them without re-encoding. The bot does not resize, recompress, trim, split, send documents, or return download links.

Normal video constraints: a conservative 16,000,000-byte maximum; H.264 video with at most one AAC audio stream. Originals exceeding these constraints receive an explanation. Not every source can meet all of single-file, normal-video, no-extra-compression delivery. The bot preserves the source platform's available compatible media, not an unavailable creator master.

One job runs at a time. Temporary files are removed after processing. Stage and delivery-status logs omit phone numbers, message contents and credentials. Free Render instances sleep; local queues and SQLite deduplication records are lost on replacement. Platforms may rate-limit server IPs. The sample Reel downloaded from Frankfurt after Singapore returned HTTP 429; future access is not guaranteed.

Run python -m unittest discover -s tests -v. The optional STARTUP_PROBE_URL runs one mock-only deployment probe and never sends messages. It is ignored when MOCK_SEND=false.
