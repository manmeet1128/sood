# Official WhatsApp API prototype

Deploy with the root render.yaml manifest using the free plan. Sending is disabled by default (MOCK_SEND=true). Set secrets privately in the host dashboard. Set GRAPH_API_VERSION to a supported version from the Meta app dashboard. Configure the public /webhook endpoint in Meta and subscribe to messages. The application secret verifies webhook signatures. The verification token is separate from the API access token.

Only fresh incoming supported public-video URLs are queued. Download attempts time out; one job runs at a time. Videos are limited to 3 minutes and 15 MiB after transcoding. Temporary files are deleted. This does not guarantee all links will download: sites may block data-centre IP addresses or require authentication. No site cookies or user credentials are included.

This is a prototype, not a verified live bot. Free Render service restarts lose the local SQLite deduplication database and pending queue. Delivery cannot be guaranteed across restarts. A persistent external queue and datastore are required before relying on it for production. Keep MOCK_SEND=true until host, Meta account, applicable pricing and a controlled real-video test are verified. This repository does not implement a billing cap; do not enable paid messaging under a zero-spend requirement.

Local verification covers signature checks and queuing only. Real video download and WhatsApp sending still require testing. The free tier sleeps when idle and may delay incoming requests.
