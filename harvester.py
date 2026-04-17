"""
ParcellIQ — Data Harvester
==========================
Pulls tax-delinquent and pre-foreclosure data for Santa Clara County,
scores leads with a motivation algorithm, and upserts into Supabase.

Setup:
  pip install -r requirements.txt

Run manually:
  python harvester.py --source attom --type pre_foreclosure
  python harvester.py --source county --type tax_delinquent

Run on a schedule (weekly):
  python harvester.py --schedule
"""

import argparse
import json
import logging
import os
import time
from datetime import date, datetime
from datetime import timezone

import requests
import schedule
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Supabase client ─────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # use service key for server-side writes

# ── ATTOM Data API (https://api.attomdata.com) ───────────────────────────────
ATTOM_KEY = os.getenv("ATTOM_API_KEY")
ATTOM_BASE = "https://api.attomdata.com/propertyapi/v1.0.0"
DEFAULT_ATTOM_ZIP_LIMIT = int(os.getenv("ATTOM_ZIP_LIMIT", "5"))

# ── Santa Clara County target area ──────────────────────────────────────────
SJ_ZIP_CODES = [
    "95101", "95102", "95103", "95106", "95108", "95109", "95110", "95111",
    "95112", "95113", "95115", "95116", "95117", "95118", "95119", "95120",
    "95121", "95122", "95123", "95124", "95125", "95126", "95127", "95128",
    "95129", "95130", "95131", "95132", "95133", "95134", "95135", "95136",
    "95138", "95139", "95140", "95141", "95148", "95150", "95151", "95152",
    "95153", "95154", "95155", "95156", "95157", "95158", "95159", "95160",
    "95161", "95164", "95170", "95172", "95173", "95190", "95191", "95192",
    "95193", "95194", "95196",
]


def get_supabase() -> Client:
    """Create and return a Supabase client with basic validation."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def extract_iso_date(value: str | None) -> str | None:
    """Return YYYY-MM-DD from an input string when possible."""
    if not value or not isinstance(value, str):
        return None
    if len(value) < 10:
        return None
    return value[:10]


def parse_iso_date(value: str | None) -> date | None:
    """Parse YYYY-MM-DD string into a date object."""
    if not value:
        return None


def positive_int(value: str) -> int:
    """Argparse validator for strictly positive integer values."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# ── MOTIVATION SCORING ───────────────────────────────────────────────────────
def compute_motivation_score(lead: dict) -> tuple[int, str]:
    """
    Score a lead 1–100 based on distress signals.
    Higher = more motivated seller.
    """
    score = 0
    notes = []

    # Tax delinquency signals
    months = lead.get("tax_delinquent_months", 0) or 0
    if months >= 24:
        score += 35
        notes.append("Severely delinquent (24+ months)")
    elif months >= 12:
        score += 25
        notes.append("Delinquent 12+ months")
    elif months >= 6:
        score += 15
        notes.append("Delinquent 6+ months")

    tax_owed = lead.get("tax_owed", 0) or 0
    if tax_owed >= 30000:
        score += 15
        notes.append(f"High tax debt (${tax_owed:,})")
    elif tax_owed >= 10000:
        score += 8

    # Pre-foreclosure signals
    if lead.get("nts_filed_date"):
        score += 40
        notes.append("Notice of Trustee Sale filed — urgent")
    elif lead.get("nod_filed_date"):
        nod = lead["nod_filed_date"]
        nod_date = nod if isinstance(nod, date) else parse_iso_date(nod)
        if nod_date:
            nod_age = (date.today() - nod_date).days
            if nod_age > 90:
                score += 30
                notes.append("NOD > 90 days old")
            else:
                score += 20
                notes.append("Recent NOD filing")
        else:
            score += 20
            notes.append("Recent NOD filing")

    # Absentee owner
    if lead.get("is_absentee"):
        score += 10
        notes.append("Absentee owner")

    # Equity (more equity = more room to negotiate)
    value = lead.get("estimated_value", 0) or 0
    loan = lead.get("loan_balance", 0) or 0
    if value and loan:
        equity_pct = (value - loan) / value
        if equity_pct > 0.5:
            score += 10
            notes.append("High equity (>50%)")

    return min(score, 100), " · ".join(notes)


# ── ATTOM: Pre-foreclosure leads ─────────────────────────────────────────────
def fetch_attom_preforeclosures(zip_code: str) -> list[dict]:
    """Fetch pre-foreclosure properties from ATTOM API for a given zip."""
    if not ATTOM_KEY:
        log.warning("ATTOM_API_KEY not set; skipping ATTOM fetch")
        return []

    headers = {"apikey": ATTOM_KEY, "accept": "application/json"}
    params = {
        "postalcode": zip_code,
        "preforeclosurestatus": "NOD,NTS",  # Notice of Default or Notice of Trustee Sale
        "pagesize": 100,
        "page": 1,
    }
    try:
        response = requests.get(
            f"{ATTOM_BASE}/assessment/snapshot",
            headers=headers,
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("property", [])
    except requests.HTTPError as exc:
        log.error("ATTOM HTTP error for zip %s: %s", zip_code, exc)
        return []
    except requests.RequestException as exc:
        log.error("ATTOM request failed for zip %s: %s", zip_code, exc)
        return []


def parse_attom_preforeclosure(raw: dict) -> tuple[dict, dict]:
    """Parse raw ATTOM record into (property_dict, lead_dict)."""
    addr = raw.get("address", {})
    assess = raw.get("assessment", {})
    prefc = raw.get("preforeclosure", {})

    property_data = {
        "apn": raw.get("identifier", {}).get("apn", ""),
        "address": addr.get("line1", ""),
        "city": addr.get("locality", "San Jose"),
        "state": addr.get("countrySubd", "CA"),
        "zip": addr.get("postal1", ""),
        "lat": raw.get("location", {}).get("latitude"),
        "lng": raw.get("location", {}).get("longitude"),
        "beds": raw.get("building", {}).get("rooms", {}).get("beds"),
        "sqft": raw.get("building", {}).get("size", {}).get("livingsize"),
        "year_built": raw.get("summary", {}).get("yearbuilt"),
        "estimated_value": assess.get("market", {}).get("mktttlvalue"),
        "property_type": raw.get("summary", {}).get("proptype", "SFR"),
    }

    nod_date_str = prefc.get("noddate")
    nts_date_str = prefc.get("ntsdate")

    lead_data = {
        "lead_type": "pre_foreclosure",
        "nod_filed_date": extract_iso_date(nod_date_str),
        "nts_filed_date": extract_iso_date(nts_date_str),
        "lender_name": prefc.get("lendername"),
        "loan_balance": prefc.get("openloans", {}).get("amount1stmtg"),
        "owner_name": raw.get("owner", {}).get("owner1", {}).get("fullname"),
        "data_source": "attom",
        "raw_data": raw,
    }
    return property_data, lead_data


# ── COUNTY SCRAPER: Tax delinquent ───────────────────────────────────────────
def fetch_county_tax_delinquent() -> list[dict]:
    """
    Santa Clara County publishes delinquent tax rolls.
    Real URL: https://www.sccgov.org/sites/dtac/Pages/TaxDelinquency.aspx

    For production: request the data via public records (CPRA request) or
    scrape the published delinquent roll PDF/CSV. This function shows the
    structure — swap in real HTTP calls to the county portal.

    Alternatively use ATTOM's 'delinquent tax' endpoint or PropStream API.
    """
    log.info("Fetching Santa Clara County tax delinquent roll...")

    # TODO: Replace this with actual county data pull.
    # Option A — ATTOM delinquent tax endpoint:
    #   GET https://api.attomdata.com/propertyapi/v1.0.0/assessment/snapshot
    #   params: postalcode=95125&taxdelinquency=true
    #
    # Option B — PropStream API:
    #   POST https://api.propstream.com/leads
    #   body: { leadType: "taxDelinquent", state: "CA", county: "Santa Clara" }
    #
    # Option C — County direct (CPRA request):
    #   Email dtac@fin.sccgov.org and request the monthly delinquent roll in CSV format.
    #   They are required to provide it under California Government Code 6250.

    # Simulated structure matching what you'd get from the county:
    return [
        {
            "apn": "264-38-021",
            "owner_name": "Garcia, Ramon",
            "address": "1847 Willow Glen Dr",
            "city": "San Jose",
            "zip": "95125",
            "tax_owed": 22400,
            "tax_delinquent_months": 18,
            "is_absentee": True,
            "estimated_value": 1240000,
        },
        {
            "apn": "012-44-091",
            "owner_name": "Chen, Wei",
            "address": "782 S 10th St",
            "city": "San Jose",
            "zip": "95112",
            "tax_owed": 14200,
            "tax_delinquent_months": 11,
            "is_absentee": False,
            "estimated_value": 420000,
        },
    ]


# ── UPSERT: Property ─────────────────────────────────────────────────────────
def upsert_property(supabase: Client, property_data: dict) -> str | None:
    """Insert or update a property record. Returns the property UUID."""
    if not property_data.get("apn"):
        log.warning("Skipping property with no APN")
        return None
    try:
        result = (
            supabase.table("properties")
            .upsert(property_data, on_conflict="apn")
            .execute()
        )
        if result.data:
            return result.data[0]["id"]
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to upsert property record: %s", exc)
    return None


# ── UPSERT: Lead ─────────────────────────────────────────────────────────────
def upsert_lead(supabase: Client, lead_data: dict, property_id: str) -> bool:
    """Insert or update a lead. Score it before saving."""
    lead_data["property_id"] = property_id

    # Compute AI motivation score
    score, notes = compute_motivation_score(lead_data)
    lead_data["motivation_score"] = score
    lead_data["motivation_notes"] = notes
    lead_data["last_verified_at"] = datetime.now(timezone.utc).isoformat()

    # Serialize raw_data if present
    if "raw_data" in lead_data and not isinstance(lead_data["raw_data"], str):
        lead_data["raw_data"] = json.dumps(lead_data["raw_data"])

    try:
        supabase.table("leads").upsert(lead_data, on_conflict="property_id,lead_type").execute()
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to upsert lead: %s", exc)
        return False


# ── MAIN HARVEST RUNNERS ─────────────────────────────────────────────────────
def run_preforeclosure_harvest(supabase: Client, zip_limit: int = DEFAULT_ATTOM_ZIP_LIMIT):
    log.info("=== Starting pre-foreclosure harvest ===")
    run = supabase.table("harvest_runs").insert({
        "source": "attom", "lead_type": "pre_foreclosure"
    }).execute().data[0]
    run_id = run["id"]

    fetched = inserted = errors = 0

    for zip_code in SJ_ZIP_CODES[: max(zip_limit, 1)]:
        log.info("Fetching pre-foreclosures for zip %s...", zip_code)
        raw_records = fetch_attom_preforeclosures(zip_code)
        fetched += len(raw_records)

        for raw in raw_records:
            prop, lead = parse_attom_preforeclosure(raw)
            prop_id = upsert_property(supabase, prop)
            if prop_id and upsert_lead(supabase, lead, prop_id):
                inserted += 1
            else:
                errors += 1

        time.sleep(0.5)  # respect ATTOM rate limits

    supabase.table("harvest_runs").update({
        "status": "success",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "records_fetched": fetched,
        "records_inserted": inserted,
        "errors": errors,
    }).eq("id", run_id).execute()

    log.info("=== Pre-foreclosure harvest done: %s/%s leads saved ===", inserted, fetched)


def run_tax_delinquent_harvest(supabase: Client):
    log.info("=== Starting tax delinquent harvest ===")
    run = supabase.table("harvest_runs").insert({
        "source": "county_scraper", "lead_type": "tax_delinquent"
    }).execute().data[0]
    run_id = run["id"]

    raw_records = fetch_county_tax_delinquent()
    fetched = len(raw_records)
    inserted = errors = 0

    for raw in raw_records:
        prop_data = {
            "apn": raw["apn"],
            "address": raw["address"],
            "city": raw["city"],
            "zip": raw.get("zip"),
            "estimated_value": raw.get("estimated_value"),
        }
        lead_data = {
            "lead_type": "tax_delinquent",
            "tax_owed": raw.get("tax_owed"),
            "tax_delinquent_months": raw.get("tax_delinquent_months"),
            "owner_name": raw.get("owner_name"),
            "is_absentee": raw.get("is_absentee", False),
            "data_source": "county_scraper",
        }
        prop_id = upsert_property(supabase, prop_data)
        if prop_id and upsert_lead(supabase, lead_data, prop_id):
            inserted += 1
        else:
            errors += 1

    supabase.table("harvest_runs").update({
        "status": "success",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "records_fetched": fetched,
        "records_inserted": inserted,
        "errors": errors,
    }).eq("id", run_id).execute()

    log.info("=== Tax delinquent harvest done: %s/%s leads saved ===", inserted, fetched)


def run_all_harvests(supabase: Client, zip_limit: int = DEFAULT_ATTOM_ZIP_LIMIT):
    run_tax_delinquent_harvest(supabase)
    run_preforeclosure_harvest(supabase, zip_limit=zip_limit)


# ── SCHEDULER ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ParcellIQ Data Harvester")
    parser.add_argument("--source", choices=["attom", "county", "all"], default="all")
    parser.add_argument("--type", choices=["tax_delinquent", "pre_foreclosure", "all"], default="all")
    parser.add_argument("--zip-limit", type=positive_int, default=DEFAULT_ATTOM_ZIP_LIMIT)
    parser.add_argument("--schedule", action="store_true", help="Run on weekly schedule")
    args = parser.parse_args()

    sb = get_supabase()

    if args.schedule:
        log.info("Scheduler started — will run every Monday at 6:00 AM")
        schedule.every().monday.at("06:00").do(lambda: run_all_harvests(sb, zip_limit=args.zip_limit))
        run_all_harvests(sb, zip_limit=args.zip_limit)  # also run immediately on start
        while True:
            schedule.run_pending()
            time.sleep(60)
    elif args.type == "tax_delinquent":
        run_tax_delinquent_harvest(sb)
    elif args.type == "pre_foreclosure":
        run_preforeclosure_harvest(sb, zip_limit=args.zip_limit)
    else:
        run_all_harvests(sb, zip_limit=args.zip_limit)
