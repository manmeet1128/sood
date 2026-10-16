# WhatsApp original-file downloader

Deploy with the root render.yaml on the free plan. Set WEBHOOK_VERIFY_TOKEN, META_APP_SECRET, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID and GRAPH_API_VERSION privately in Render. Configure /webhook in Meta and subscribe to messages. MOCK_SEND defaults to true; set it to false to send replies. RENDER_EXTERNAL_URL (or an explicit PUBLIC_BASE_URL) must contain the service's public HTTPS URL.

The bot downloads the best available MP4 video and M4A audio streams, merging streams without re-encoding when needed. It does not resize, recompress, trim or split video. It sends one WhatsApp document message, never a user-facing download link. This preserves the quality of the source platform's available streams, not an unavailable creator master file.

Downloads are capped at 100 MiB and one job runs at a time. Meta fetches original files through unguessable temporary media-fetch URLs. Files expire after one hour and cleanup runs every minute; restarts may remove them earlier. Temporary retained originals are limited to 256 MiB total. Download, submission and delivery outcomes are logged without phone numbers, message contents, credentials or media-fetch URLs. Failures receive an explanatory text reply instead of silent compression or splitting.

Meta officially guarantees document support only for listed document formats. Video documents may be accepted but are not guaranteed; check real delivery-status webhooks, not just HTTP acceptance. Unsupported or oversized originals must be reported as failures.

Free Render instances sleep when idle. Request deduplication and queues use ephemeral local storage and are lost on replacement. Video platforms may rate-limit a cloud region; local success does not establish cloud success. The supplied Reel was downloadable from Frankfurt after Singapore returned HTTP 429. This does not guarantee future availability.

Run python -m unittest discover -s tests -v. The optional STARTUP_PROBE_URL runs one mock-only deployment test and never sends messages. It is ignored when MOCK_SEND=false.
