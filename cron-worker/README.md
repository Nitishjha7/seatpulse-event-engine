# Cron worker

Pings the backend's `/api/cron/tick` on a schedule — only needed on a
deployment with no persistent worker process (see the deployment guide
in the main README). docker-compose already runs a worker continuously,
so this isn't used locally.

## Deploy

```bash
npm install -g wrangler
cd cron-worker
wrangler login
wrangler secret put BACKEND_URL     # e.g. https://seatpulse-api.onrender.com
wrangler secret put CRON_SECRET     # same value as the backend's CRON_SECRET
wrangler deploy
```

That's it — Cloudflare runs `worker.js` on the schedule in `wrangler.toml`
(every 2 minutes) for free. Check `wrangler tail` to see the ping results
live.
