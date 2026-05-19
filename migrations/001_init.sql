-- ============================================================================
-- normal people :: Phase 1 schema
-- Target: Supabase (PostgreSQL 15+)
-- Run via Supabase SQL editor or `psql $SUPABASE_DB_URL -f 001_init.sql`
-- ============================================================================

-- USERS ----------------------------------------------------------------------
-- One row per Telegram user that has ever interacted with the Hub Bot.
create table if not exists np_users (
    user_id              bigint       primary key,           -- Telegram user_id
    username             varchar(255),                       -- @handle, may be null
    first_name           varchar(255),
    current_tier         smallint     not null default 0,    -- 0=Gateway, 1=Library, 2=Floor, 3=Syndicate
    strike_count         smallint     not null default 0,
    last_strike_at       timestamptz,
    is_banned            boolean      not null default false,
    accepted_protocols_at timestamptz,                       -- Phase 1 manifesto signed
    certified_at         timestamptz,                        -- Phase 2 behavioral gate passed
    created_at           timestamptz  not null default now(),
    updated_at           timestamptz  not null default now()
);

create index if not exists idx_np_users_tier   on np_users(current_tier);
create index if not exists idx_np_users_banned on np_users(is_banned);

-- INVITE LINKS ---------------------------------------------------------------
-- Tracks every single-use invite link the bot generates. Used for auditing
-- and detecting abuse (e.g., links shared before they expire).
create table if not exists np_invite_links (
    link_id           varchar(512) primary key,              -- the full t.me/+xxx URL
    associated_user_id bigint      not null references np_users(user_id) on delete cascade,
    target_tier       smallint     not null,                 -- 1 or 2
    is_used           boolean      not null default false,
    issued_at         timestamptz  not null default now(),
    expires_at        timestamptz  not null,
    used_at           timestamptz
);

create index if not exists idx_invite_user    on np_invite_links(associated_user_id);
create index if not exists idx_invite_expires on np_invite_links(expires_at);

-- STRIKES LOG ----------------------------------------------------------------
-- Append-only ledger of every moderation event. Decay logic reads from here.
create table if not exists np_strikes (
    id              bigserial    primary key,
    user_id         bigint       not null references np_users(user_id) on delete cascade,
    issued_by       bigint       not null,                   -- admin user_id
    protocol        smallint     not null,                   -- 1=Harm Reduction, 2=Data Integrity, 3=Performative Ego
    message_excerpt text,
    chat_id         bigint,
    issued_at       timestamptz  not null default now(),
    decayed_at      timestamptz                              -- set when 90d compliance reached
);

create index if not exists idx_strikes_user_active
    on np_strikes(user_id) where decayed_at is null;

-- CAPTCHA SESSIONS -----------------------------------------------------------
-- Ephemeral; tracks pending CAPTCHA challenges so users can't brute-force.
create table if not exists np_captcha_sessions (
    user_id        bigint       primary key,
    answer         varchar(32)  not null,
    attempts       smallint     not null default 0,
    issued_at      timestamptz  not null default now(),
    expires_at     timestamptz  not null
);

-- UPDATED_AT trigger ---------------------------------------------------------
create or replace function np_touch_updated_at() returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_np_users_touch on np_users;
create trigger trg_np_users_touch
    before update on np_users
    for each row execute function np_touch_updated_at();
