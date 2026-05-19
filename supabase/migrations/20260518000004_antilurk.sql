-- ============================================================================
-- normal people :: Phase 5 - Anti-Lurk
-- ============================================================================
-- Adds lifecycle tracking to np_users to support:
--   - 24h intro requirement on join
--   - 30/37 day activity floor
--   - 60d demoted -> full removal sweep

alter table np_users
    add column if not exists joined_floor_at      timestamptz,
    add column if not exists intro_completed_at   timestamptz,
    add column if not exists last_floor_msg_at    timestamptz,
    add column if not exists activity_pinged_at   timestamptz,    -- when we sent 'still here?'
    add column if not exists demoted_at           timestamptz;    -- when we revoked write access for inactivity

-- Index for the hourly intro-kick job
create index if not exists idx_users_intro_pending
    on np_users(joined_floor_at)
    where intro_completed_at is null
      and joined_floor_at is not null
      and is_banned = false;

-- Index for the daily activity job
create index if not exists idx_users_active_floor
    on np_users(last_floor_msg_at)
    where current_tier >= 2
      and is_banned = false
      and demoted_at is null;
