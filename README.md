# normal people — Hub Bot (Phase 1 + 2)

Gateway → Tier 1 (Library) → Tier 2 (Floor) onboarding funnel.

## Stack
- Python 3.11+, `python-telegram-bot[ext]==21.6`, long-polling
- Supabase Postgres (asyncpg)
- Railway worker deploy

## Project layout
```
bot/
  main.py                # entry point
  config.py              # env loader
  db/
    pool.py              # asyncpg pool
    repo.py              # all SQL
  handlers/
    onboarding.py        # /start, CAPTCHA, manifesto, /certify
  services/
    captcha.py           # math challenges
    invites.py           # single-use Telegram invite links
migrations/
  001_init.sql           # run once in Supabase SQL editor
```

## Local run
```bash
cp .env.example .env       # fill in tokens + DATABASE_URL
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
psql "$DATABASE_URL" -f migrations/001_init.sql
python -m bot.main
```

## Railway deploy
1. Push this repo to GitHub.
2. Railway → New → Deploy from GitHub → select repo.
3. Add all env vars from `.env.example`.
4. The `Procfile` / `railway.json` runs `python -m bot.main` as a worker.

## Funnel
| Phase | Trigger              | Outcome                                              |
|------:|----------------------|------------------------------------------------------|
| 1     | `/start`             | CAPTCHA → manifesto → single-use Tier 1 invite (5m)  |
| 2     | `/certify`           | 3-prompt behavioral gate → single-use Tier 2 invite  |
| 3     | admin `/warn1..3`    | Strike + 24h mute (Phase 3 — next milestone)         |

## Required Telegram permissions
Hub Bot must be **Admin** in both Tier 1 channel and Tier 2 supergroup with:
- Post / Delete / Edit Messages
- Invite Users via Link
- Restrict / Ban Members
- Manage Topics (Tier 2 only)
