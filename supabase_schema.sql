-- ParcellIQ — Supabase Database Schema
-- Run this once in the Supabase SQL Editor (left sidebar → SQL Editor → New query)
-- It creates all tables, indexes, and row-level security policies.

-- ── Extensions ───────────────────────────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ── Table: properties ────────────────────────────────────────────────────────
-- One row per parcel (deduplicated by APN).
create table if not exists properties (
    id              uuid primary key default uuid_generate_v4(),
    apn             text not null unique,           -- Assessor Parcel Number (e.g. "264-38-021")
    address         text,
    city            text default 'San Jose',
    state           text default 'CA',
    zip             text,
    lat             numeric(10, 7),
    lng             numeric(10, 7),
    beds            int,
    sqft            int,
    year_built      int,
    estimated_value bigint,
    property_type   text default 'SFR',             -- SFR, CONDO, MFR, LAND, etc.
    created_at      timestamptz default now(),
    updated_at      timestamptz default now()
);

create index if not exists properties_zip_idx on properties (zip);
create index if not exists properties_city_idx on properties (city);

-- Auto-update updated_at on any row change
create or replace function update_updated_at_column()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists properties_updated_at on properties;
create trigger properties_updated_at
    before update on properties
    for each row execute function update_updated_at_column();

-- ── Table: leads ─────────────────────────────────────────────────────────────
-- One lead row per property per lead type (pre_foreclosure | tax_delinquent).
create table if not exists leads (
    id                   uuid primary key default uuid_generate_v4(),
    property_id          uuid not null references properties(id) on delete cascade,
    lead_type            text not null check (lead_type in ('pre_foreclosure', 'tax_delinquent')),

    -- Tax delinquency fields
    tax_owed             bigint,
    tax_delinquent_months int,

    -- Pre-foreclosure fields
    nod_filed_date       date,
    nts_filed_date       date,
    lender_name          text,
    loan_balance         bigint,

    -- Owner info
    owner_name           text,
    is_absentee          boolean default false,
    owner_phone          text,   -- populated after skip-trace
    owner_email          text,   -- populated after skip-trace
    owner_mailing_addr   text,   -- populated after skip-trace

    -- Scoring
    motivation_score     int check (motivation_score between 0 and 100),
    motivation_notes     text,

    -- Metadata
    data_source          text,   -- 'attom' | 'county_scraper' | 'propstream'
    raw_data             jsonb,
    last_verified_at     timestamptz,
    status               text default 'new' check (status in ('new', 'contacted', 'qualified', 'closed', 'dead')),
    assigned_to          uuid references auth.users(id),
    notes                text,
    created_at           timestamptz default now(),
    updated_at           timestamptz default now(),

    unique (property_id, lead_type)
);

create index if not exists leads_motivation_idx on leads (motivation_score desc);
create index if not exists leads_lead_type_idx  on leads (lead_type);
create index if not exists leads_status_idx     on leads (status);
create index if not exists leads_nod_date_idx   on leads (nod_filed_date);

drop trigger if exists leads_updated_at on leads;
create trigger leads_updated_at
    before update on leads
    for each row execute function update_updated_at_column();

-- ── Table: harvest_runs ───────────────────────────────────────────────────────
-- Audit log of every harvester execution.
create table if not exists harvest_runs (
    id               uuid primary key default uuid_generate_v4(),
    source           text not null,              -- 'attom' | 'county_scraper'
    lead_type        text not null,              -- 'pre_foreclosure' | 'tax_delinquent'
    status           text default 'running' check (status in ('running', 'success', 'failed')),
    records_fetched  int default 0,
    records_inserted int default 0,
    errors           int default 0,
    started_at       timestamptz default now(),
    finished_at      timestamptz
);

-- ── Row-Level Security ────────────────────────────────────────────────────────
-- Public reads are blocked. Only authenticated users (or service key) can read/write.

alter table properties    enable row level security;
alter table leads         enable row level security;
alter table harvest_runs  enable row level security;

-- Allow service role (used by harvester.py) full access
create policy "service role full access — properties"
    on properties for all
    using (auth.role() = 'service_role');

create policy "service role full access — leads"
    on leads for all
    using (auth.role() = 'service_role');

create policy "service role full access — harvest_runs"
    on harvest_runs for all
    using (auth.role() = 'service_role');

-- Allow authenticated users (subscribers) to read all leads and properties
create policy "authenticated users can read properties"
    on properties for select
    using (auth.role() = 'authenticated');

create policy "authenticated users can read leads"
    on leads for select
    using (auth.role() = 'authenticated');

-- Agents can update leads assigned to them (status, notes)
create policy "agents can update own leads"
    on leads for update
    using (assigned_to = auth.uid());
