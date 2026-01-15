# Boat Storage Management - Backend

## Setup

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Set environment variables:**
```bash
cp .env.example .env
# Edit .env with your Supabase DATABASE_URL
```

3. **Run the server:**
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## API Endpoints

- `GET /` - API status
- `GET /clients/{phone}` - Get client info and debt status
- `POST /webhook/generate-monthly-debt` - Generate monthly payments (requires `x-webhook-secret` header)
- `GET /health` - Health check

## Deploy to Railway

1. Create new project on Railway
2. Add PostgreSQL database
3. Connect GitHub repo
4. Set environment variables:
   - `DATABASE_URL` (auto-populated)
   - `WEBHOOK_SECRET`
   - `MONTHLY_FEE`
5. Railway will auto-detect Python and deploy

## Cron Job Setup

Use a service like cron-job.org or Railway Cron to hit:
```
POST https://your-api.railway.app/webhook/generate-monthly-debt
Headers: x-webhook-secret: your-secret
```

Schedule: First day of each month at 00:00
