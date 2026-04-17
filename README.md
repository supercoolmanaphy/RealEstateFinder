# RealEstateFinder

## ParcellIQ Data Harvester (Python)

This repository now includes `harvester.py` for collecting and scoring San Jose-area leads (tax-delinquent and pre-foreclosure) and upserting them into Supabase.

### Setup

1. Install dependencies:
   - `pip install -r requirements.txt`
2. Set environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `ATTOM_API_KEY` (required for ATTOM pre-foreclosure fetches)

### Run manually

- `python harvester.py --source attom --type pre_foreclosure`
- `python harvester.py --source county --type tax_delinquent`

### Run weekly schedule

- `python harvester.py --schedule`
