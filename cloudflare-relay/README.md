# Overcode Cloudflare Relay

A lightweight relay service that lets you monitor your Overcode agents from anywhere (like your phone when you're away from your Mac).

## Architecture

```
┌─────────────┐     POST /update     ┌─────────────────────┐     GET /     ┌──────────┐
│  Your Mac   │ ──────────────────▶  │  Cloudflare Worker  │ ◀──────────── │  Phone   │
│  (monitor   │   (every 30s)        │  (edge network)     │   (dashboard) │          │
│   daemon)   │                      │                     │               │          │
└─────────────┘                      └─────────────────────┘               └──────────┘
```

## Setup Instructions

### 1. Install Wrangler CLI

```bash
npm install -g wrangler
# or
brew install cloudflare-wrangler
```

### 2. Authenticate with Cloudflare

```bash
wrangler login
```

This opens a browser to authenticate. Follow the prompts.

### 3. Create a KV Namespace (for persistent storage)

```bash
cd cloudflare-relay
wrangler kv:namespace create STATE
```

This outputs something like:
```
{ binding = "STATE", id = "abcd1234..." }
```

Copy the ID and update `wrangler.toml`:
```toml
[[kv_namespaces]]
binding = "STATE"
id = "abcd1234..."  # <-- paste your ID here
```

### 4. Deploy the Worker

```bash
wrangler deploy
```

This gives you a URL like: `https://overcode-relay.your-subdomain.workers.dev`

### 5. Set your API Key

Generate a random key:
```bash
openssl rand -hex 32
```

Set it as a secret in Cloudflare:
```bash
wrangler secret put API_KEY
# Paste your key when prompted
```

### 6. Configure Overcode to Push to the Relay

Add to your `~/.overcode/config.yaml`:

```yaml
relay:
  enabled: true
  url: https://overcode-relay.your-subdomain.workers.dev/update
  api_key: your-api-key-here
  interval: 30  # seconds between updates
```

Then restart the monitor daemon (`\` in TUI).

### 7. Access from Your Phone

Open the worker URL in your phone's browser:
```
https://overcode-relay.your-subdomain.workers.dev
```

Bookmark it or add to home screen for quick access.

## Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/` | GET | No | Dashboard HTML |
| `/status` | GET | No | Current agent status JSON |
| `/timeline` | GET | No | Timeline history JSON |
| `/update` | POST | API Key | Push new state (daemon uses this) |
| `/health` | GET | No | Health check |

## Security Notes

- The `/update` endpoint requires the `X-API-Key` header
- Read endpoints are public (anyone with the URL can view)
- If you want private reads, modify `handleGetStatus()` to check the API key
- The worker URL is not guessable but not secret - treat it like an unlisted link

## Costs

Cloudflare Workers free tier includes:
- 100,000 requests/day
- 10ms CPU time per request
- 1,000 KV writes/day
- 100,000 KV reads/day

For personal monitoring, you'll never hit these limits.

## Troubleshooting

**Worker not updating?**
```bash
wrangler tail  # Live logs from your worker
```

**Check what's stored:**
```bash
curl https://your-worker.workers.dev/status | jq .
```

**Test pushing manually:**
```bash
curl -X POST https://your-worker.workers.dev/update \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"test": true}'
```

## Custom Domain (Optional)

To use a custom domain like `status.yourdomain.com`:

1. Go to Cloudflare Dashboard > Workers > your worker
2. Click "Triggers" tab
3. Add a Custom Domain
4. Enter your subdomain (must be on a domain in your Cloudflare account)
