# RealEstateFinder
# ParcellIQ

> Off-market property lead platform for Bay Area realtors.  
> Surfaces tax-delinquent and pre-foreclosure properties in San Jose / Santa Clara County.

---

## What's in this repo

```
parceliq/
├── index.html              # Landing page (open in browser or deploy to Vercel)
├── harvester.py            # Weekly data harvester (runs on a schedule)
├── supabase_schema.sql     # Full database schema — run once in Supabase SQL editor
├── .env.example            # Copy to .env and fill in your API keys
└── README.md
```

---

## Step 1 — Set up your GitHub repo

1. Go to [github.com](https://github.com) → **New repository** → name it `parceliq`
2. Clone it locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/parceliq.git
   cd parceliq
   ```
3. Copy all these files into that folder, then:
   ```bash
   git add .
   git commit -m "Initial ParcellIQ setup"
   git push
   ```
4. Create a `.gitignore` file (GitHub Copilot can help) — make sure `.env` is in it

---

## Step 2 — Set up Supabase (your database)

Supabase is free to start and gives you a hosted Postgres database + auth + API.

1. Go to [supabase.com](https://supabase.com) → **New project**
2. Name it `parceliq`, choose a strong password, pick **US West** region
3. Once created, go to **SQL Editor** (left sidebar)
4. Open `supabase_schema.sql` from this repo and paste the entire contents → click **Run**
5. This creates all your tables, indexes, and row-level security policies
6. Go to **Settings → API** and copy:
   - `Project URL` → paste as `SUPABASE_URL` in your `.env`
   - `anon public` key → `SUPABASE_ANON_KEY`
   - `service_role` key → `SUPABASE_SERVICE_KEY` (keep this secret — server only)

---

## Step 3 — Get your data API keys

### ATTOM Data (recommended — covers pre-foreclosure + tax data)
1. Go to [api.attomdata.com](https://api.attomdata.com)
2. Sign up for a free trial (includes 100 API calls/day)
3. Paid plans start at ~$300/mo for production use
4. Copy your API key → paste as `ATTOM_API_KEY` in `.env`

### Santa Clara County Tax Delinquent Roll (free, official)
- The county publishes delinquent rolls publicly
- Email `dtac@fin.sccgov.org` and request the monthly delinquent roll in CSV (California CPRA / public records)
- Alternatively call: (408) 808-7900 — Department of Tax & Collections
- Once you have the CSV, the harvester can parse it directly

### BatchSkipTracing (for owner contact info)
1. Go to [batchskiptracing.com](https://batchskiptracing.com)
2. Pay-per-record (~$0.18/record) — no monthly fee
3. Upload a CSV of owner names + property addresses, get back phone/email

---

## Step 4 — Run the data harvester

```bash
# Install Python dependencies
pip install supabase requests python-dotenv schedule

# Copy env file and fill in your keys
cp .env.example .env
# Edit .env with your actual keys

# Run a one-time harvest (good for testing)
python harvester.py --type tax_delinquent
python harvester.py --type pre_foreclosure

# Run on weekly schedule (leave this running on a server or cron job)
python harvester.py --schedule
```

### Running the harvester automatically (two options)

**Option A — GitHub Actions (free, easiest):**
Create `.github/workflows/harvest.yml`:
```yaml
name: Weekly Harvest
on:
  schedule:
    - cron: '0 14 * * 1'   # Every Monday 6am PT
jobs:
  harvest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with: { python-version: '3.11' }
      - run: pip install supabase requests python-dotenv schedule
      - run: python harvester.py --type all
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
          ATTOM_API_KEY: ${{ secrets.ATTOM_API_KEY }}
```
Then add your secrets in GitHub → Settings → Secrets.

**Option B — Railway.app:**
Deploy the harvester as a background service at [railway.app](https://railway.app) (~$5/mo).

---

## Step 5 — Deploy the landing page

### Quickest: Vercel (free)
1. Go to [vercel.com](https://vercel.com) → Import from GitHub
2. Select your `parceliq` repo → Deploy
3. Your site is live at `parceliq.vercel.app` in ~2 minutes

### Custom domain
In Vercel → Settings → Domains → add `parceliq.com` (or whatever you buy)

---

## Step 6 — Add Stripe billing

1. Go to [dashboard.stripe.com](https://dashboard.stripe.com)
2. Create 3 products: Starter ($79/mo), Pro ($199/mo), Team ($449/mo)
3. Copy the Price IDs into your `.env`
4. Add a webhook endpoint pointing to your deployed site: `https://yourdomain.com/api/webhook`
5. GitHub Copilot can help you write the Stripe checkout and webhook handler

---

## Suggested next features (ask Copilot to help build these)

- [ ] User auth with Supabase Auth (email magic link)
- [ ] Dashboard page showing leads table with filters
- [ ] Stripe checkout flow + subscription gating
- [ ] Email alerts when new high-score leads come in
- [ ] Interactive map with Mapbox or Google Maps
- [ ] CSV export button
- [ ] CRM webhook to Follow Up Boss

---

## Data sources reference

| Source | What it provides | Cost |
|--------|-----------------|------|
| ATTOM Data API | Pre-foreclosure, NOD/NTS, AVM, property details | ~$300/mo |
| Santa Clara County Tax Collector | Delinquent tax roll | Free (CPRA request) |
| BatchSkipTracing | Owner phone + email | $0.18/record |
| PropStream | All-in-one alternative to ATTOM | ~$100/mo |
| Mapbox | Interactive property map | Free up to 50K loads |

---

## Questions?

Open an issue on GitHub or ask GitHub Copilot — it has full context of this codebase.
