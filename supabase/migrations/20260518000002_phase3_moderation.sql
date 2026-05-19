-- ============================================================================
-- normal people :: Phase 3 — Moderation
-- ============================================================================

-- np_messages: append-only log of every Floor message the bot observes.
-- Used by Strike 2 scrub to delete all of a banned user's content.
create table if not exists np_messages (
    chat_id      bigint   not null,
    message_id   bigint   not null,
    user_id      bigint   not null,
    posted_at    timestamptz not null default now(),
    primary key (chat_id, message_id)
);

create index if not exists idx_np_messages_user_recent
    on np_messages (user_id, posted_at desc);

-- Optional retention: keep last 14 days only to prevent unbounded growth.
-- (Bot calls a daily prune job; see services/retention.py)

-- Add mute tracking to np_users
alter table np_users
    add column if not exists muted_until      timestamptz,
    add column if not exists must_reverify    boolean not null default false;
