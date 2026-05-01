# Vercel deployment

## One-time setup

1. **Provision Upstash Redis** (free tier OK):
   - Sign up at https://upstash.com → Create Database → pick `Singapore (ap-southeast-1)` to match the RDS read-replica region.
   - Note the `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`.

2. **Create the Vercel project**:
   ```bash
   npm i -g vercel
   vercel login
   vercel link             # link this directory to a new Vercel project
   ```

3. **Set environment variables** (via Vercel dashboard *or* CLI):
   ```bash
   vercel env add STOCKS_DB_HOST production
   vercel env add STOCKS_DB_PORT production
   vercel env add STOCKS_DB_NAME production
   vercel env add STOCKS_DB_USER production
   vercel env add STOCKS_DB_PASSWORD production
   vercel env add SMARTKARMA_API_TOKEN production
   vercel env add SMARTKARMA_API_EMAIL production
   vercel env add UPSTASH_REDIS_REST_URL production
   vercel env add UPSTASH_REDIS_REST_TOKEN production
   ```
   (Repeat with `preview` and `development` as needed.)

## Deploy

```bash
vercel --prod
```

Vercel auto-detects `vercel.json` + `api/index.py` and serves the FastAPI ASGI app from a Python serverless function.

## Notes & limits

- **Region**: `vercel.json` pins functions to `sin1` (Singapore) to keep RDS round-trips fast.
- **Cold starts**: each cold start re-loads the 29k-row entities list from Postgres (~1-2s). The list is cached in-process for the lifetime of that warm Lambda only.
- **Cache**: Trends + Wikipedia responses cache in Upstash Redis with 7-day TTL; without `UPSTASH_*` set, the app falls back to local SQLite (which on Vercel writes to `/tmp` and doesn't persist).
- **Function timeout**: 60s (configured in `vercel.json`). Trends fetches may need this when retries kick in.
- **Bundle size**: pandas + numpy push close to Vercel's 250 MB unzipped limit. If we hit it, the YoY math in Study 2 can be rewritten without pandas.
- **Logo**: `/static/sk-logo.png` is served by FastAPI's StaticFiles, which means it goes through the Lambda. For a small image this is fine.
