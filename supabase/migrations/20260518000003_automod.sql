-- ============================================================================
-- normal people :: Phase 4 - Auto-Mod
-- ============================================================================

-- audit log of every automod decision. permanent, append-only.
create table if not exists np_mod_actions (
    id              bigserial    primary key,
    user_id         bigint       not null,
    chat_id         bigint       not null,
    message_id      bigint,
    rule_code       varchar(64)  not null,    -- e.g. "spam.too_many_links"
    severity        varchar(16)  not null,    -- "block" | "flag"
    action_taken    varchar(32)  not null,    -- "deleted+strike" | "flagged" | "passed"
    message_excerpt text,
    detected_at     timestamptz  not null default now(),
    reviewed_at     timestamptz,
    reviewer_id     bigint,
    review_decision varchar(32)               -- "uphold" | "overturn" | "no_action"
);

create index if not exists idx_mod_actions_user
    on np_mod_actions(user_id, detected_at desc);

create index if not exists idx_mod_actions_pending
    on np_mod_actions(detected_at desc) where reviewed_at is null and severity = 'flag';
