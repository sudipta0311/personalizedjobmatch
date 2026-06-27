# Job Search Agent

A private, **email-driven, human-in-the-loop** job-search agent for a senior AI/enterprise
architect. It runs on **GitHub Actions** on a schedule, discovers relevant senior roles from
legitimate sources, ranks them against *your* real filters, and **emails you a ranked digest of
suggested actions**. You control everything by **replying to that email** — or by acting manually
on its suggestions. The agent does the grind (search, rank, research, tailor, draft outreach); you
keep judgment, the final submit, and all LinkedIn clicks.

---

## ⛔ ABSOLUTE PROHIBITION — LinkedIn Automation

**This agent must NEVER automate LinkedIn in any form.**

- No login, no search, no scraping, no API calls to LinkedIn — not even using your own session.
- LinkedIn's User Agreement explicitly prohibits automated access and detection is aggressive.
  A ban would destroy your primary professional visibility channel.
- Any feature that touches LinkedIn must produce **draft text** for you to copy-paste and execute
  manually in your own browser. The agent does not click, type, or read LinkedIn itself.

This rule is permanent and non-negotiable. Any PR that adds LinkedIn automation must be rejected.

---

## Architecture at a glance

Two independent scheduled workflows share one Neon database and one Gmail account. There is **no
always-on server** — GitHub Actions runners are ephemeral, so all state lives in Neon.

```
┌──────────────────────────── discover.yml (weekly cron) ───────────────────────────┐
│                                                                                    │
│  discover ──▶ dedupe ──▶ score ──▶ filter ──▶ notify                               │
│  (ATS APIs)   (hash)   (rules+LLM) (cutoff,   (Gmail digest + record index_map)    │
│                                     top-N)                                          │
└────────────────────────────────────────┬───────────────────────────────────────-─┘
                                          │  digest email (thread T, index_map 1→jobX)
                                          ▼
                                     YOU read it
                                          │  reply "prepare 1,3; warm 5; skip 2"
                                          ▼
┌──────────────────────────── replies.yml (every 8h cron) ──────────────────────────┐
│                                                                                    │
│  poll_replies ──▶ parse_commands ──▶ execute                                       │
│  (unread in     (grammar, quote-    (idempotent claim → run skip/prepare/warm/     │
│   thread T)      aware)              info/ask → email ack → mark read)             │
└────────────────────────────────────────────────────────────────────────────────-─┘
```

Both flows are **LangGraph** state graphs. Each node is a pure-ish function over a typed state dict,
which keeps the encoded judgment (scoring, parsing) unit-testable without touching the network or DB.

### Data flow

1. **Discover** pulls postings from official ATS APIs (Greenhouse today; Lever/career pages next),
   normalises them to a common `JobPosting` schema, and parses a best-effort country code.
2. **Dedupe** hashes each posting on `(company + normalised title + location)` and inserts only
   unseen ones into `jobs` (returns the new rows with their UUIDs).
3. **Score** runs deterministic **rule gates** first (work-auth, seniority-grade, market tag,
   exclusions), then an **LLM semantic fit** (Claude, structured JSON) for non-vetoed roles, and
   blends them into a weighted **composite**. Results persist to `scores`; `jobs.market_tag` is
   stamped.
4. **Filter** drops vetoed + below-cutoff roles and keeps the top-N by composite.
5. **Notify** renders a text+HTML digest, sends it via Gmail, and records a `digests` row with the
   `index_map` (reply index → job UUID) and the Gmail thread id.
6. **Reply poller** reads unread replies in recent digest threads, **parses** the commands,
   **idempotently claims** each Gmail message (unique `gmail_message_id`), **executes** the commands,
   emails an acknowledgement, and marks the message read.

---

## The email control loop

The weekly digest lists each shortlisted role with its fit score, rationale, match points, gaps,
work-auth flag, market tag, and posting URL — everything you need to **act manually without
replying**. To delegate the grind, reply with commands:

| Command | Action | Status |
|---------|--------|--------|
| `prepare 1,3` | Build tailored ATS CV + cover letter + form answers; email back as attachments | Recorded now; generation in Phase 5 |
| `warm 2` | Build LinkedIn play (search string + public contacts + outreach drafts) — **manual execution only** | Recorded now; generation in Phase 5 |
| `info 4` | Deeper company brief | Recorded now; Phase 6 |
| `skip 5` | Dismiss the role | ✅ Fully actioned |
| `ask 3: <question>` | LLM answers using the JD + your profile | Recorded now; Phase 6 |

**Grammar:** case-insensitive; multiple commands per email separated by `;` or newlines
(`prepare 1,3; warm 5; skip 2`); ids comma/space separated; quote-aware (ignores the quoted prior
email, the `On … wrote:` attribution, and your signature). Re-running the poller never
double-processes a reply (idempotency via the unique `gmail_message_id`).

---

## Repository layout

```
profile.yaml                  # SINGLE SOURCE OF TRUTH — CV content, filters, auth, framings, models
agent/
  config.py                   # load_profile() + Settings.from_env() (secrets)
  db/
    client.py                 # Neon (psycopg2) connection + apply_schema()
    schema.sql                # 6 tables: jobs, scores, digests, commands, applications, events
  discovery/
    base.py                   # JobPosting dataclass + BaseSource ABC
    greenhouse.py             # Greenhouse public boards API
    lever.py                  # Lever (enable in profile)
    dedupe.py                 # content-hash dedupe + persist_jobs()
  scoring/
    rules.py                  # PURE rule gates: market_tag, auth, grade, exclusions → RuleResult
    llm_fit.py                # Claude semantic fit via messages.parse + Pydantic LLMFit
    score.py                  # composite_score + score_job/score_jobs → ScoredJob
    filter.py                 # cutoff + top-N shortlist
    persistence.py            # upsert scores, stamp market_tag, log events
  email/
    gmail.py                  # OAuth, MIME build, send + read (threads, unread, mark-read)
    digest.py                 # PURE render_digest → (subject, text, html, index_map)
    persistence.py            # record_digest() + current_run_id()
  replies/
    parser.py                 # PURE command grammar → list[Command]
    executor.py               # dispatch skip/prepare/warm/info/ask + compose_ack
    persistence.py            # recent_digests, idempotent claim_command, app status
  graphs/
    discover_graph.py         # discover→dedupe→score→filter→notify
    reply_graph.py            # poll_replies→parse_commands→execute
scripts/
  run_discover.py             # entrypoint for discover.yml
  run_replies.py              # entrypoint for replies.yml
  gmail_auth.py               # ONE-TIME local OAuth → refresh token
.github/workflows/
  discover.yml                # weekly cron + manual dispatch
  replies.yml                 # every-8h cron + manual dispatch
tests/                        # pytest — pure logic fully covered, network/DB mocked
```

---

## Data model (Neon Postgres)

See [agent/db/schema.sql](agent/db/schema.sql) for the full DDL.

| Table | Purpose |
|-------|---------|
| `jobs` | One row per discovered posting (+ `content_hash` unique, `market_tag`) |
| `scores` | Rule + LLM scoring per job (unique on `job_id`; upserted on re-score) |
| `digests` | One row per digest sent — `gmail_thread_id` + `index_map` (reply index → job_id) |
| `commands` | Parsed reply commands — **unique `gmail_message_id`** is the idempotency key |
| `applications` | Pipeline state per job (discovered → shortlisted → skipped → prepared → …) |
| `events` | Append-only audit trail |

---

## Tech stack

- **Python 3.11+** / **LangGraph** / **LangChain**
- **Anthropic Claude API** — model configurable per node via `profile.yaml`
  (`claude-sonnet-4-6` for high-volume scoring; `claude-opus-4-8` for tailoring/outreach)
- **PostgreSQL on Neon** — persistent state across ephemeral runners
- **Gmail API** — sends the digest and reads replies (the control channel)
- **python-docx** — CV / cover letter generation (Phase 5)
- **GitHub Actions** — scheduled workflows, no always-on server

---

## Setup

### 1. Clone and configure

```bash
git clone <your-private-repo-url>
cd job-search-agent
cp .env.example .env        # fill in for local runs
pip install -r requirements.txt
```

### 2. Fill in `profile.yaml`

Your real CV content, target titles, per-country work-auth status, market framings, exclusions, and
per-node model choices. **This file is the agent's only source of truth — never fabricate
experience; the agent only reorders and keyword-aligns what you give it.**

### 3. Neon database

Create a database at [neon.tech](https://neon.tech), set `NEON_DATABASE_URL`, then apply the schema
(also auto-applied at the start of each run):

```bash
psql $NEON_DATABASE_URL -f agent/db/schema.sql
```

### 4. Gmail API (one-time)

1. [Google Cloud Console](https://console.cloud.google.com) → enable Gmail API.
2. Create OAuth 2.0 credentials (Desktop app); download `credentials.json` to the project root
   (gitignored).
3. Run `python scripts/gmail_auth.py`, authorise in the browser, and copy the printed
   `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`.

### 5. GitHub Secrets

**Settings → Secrets and variables → Actions:** `ANTHROPIC_API_KEY`, `NEON_DATABASE_URL`,
`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `EMAIL_TO`.

### 6. Run locally

```bash
pytest tests/                        # full suite, no network/DB needed
DIGEST_DRY_RUN=1 python scripts/run_discover.py   # discover+score, print digest, don't send
python scripts/run_discover.py       # live: send digest (needs secrets + Neon)
python scripts/run_replies.py        # live: process replies
```

Useful env toggles: `DIGEST_DRY_RUN=1` (render but don't send), `DIGEST_TOP_N=N` (override shortlist
size), `LOG_LEVEL=DEBUG`.

---

## Build phases

| Phase | Status | Scope |
|-------|--------|-------|
| 1 | ✅ Done | Scaffold, config, Neon schema, Greenhouse source, dedupe |
| 2 | ✅ Done | Scoring: rule gates (auth/grade/market/exclusions) + LLM fit, composite, filter |
| 3 | ✅ Done | Gmail send — weekly digest (text+HTML, command syntax); `discover.yml` wired |
| 4 | ✅ Done | Gmail read — reply poller + idempotent command parser; `replies.yml` wired (prepare/warm/skip + info/ask recorded) |
| 5 | ⬜ Next | `prepare` tailoring (CV + letter + form answers + parse-verify); `warm` LinkedIn play |
| 6 | ⬜ | Tracker reminders in the digest; full `info` / `ask` modes |

---

## Legitimate job sources

| Source | API | Notes |
|--------|-----|-------|
| Greenhouse | `boards-api.greenhouse.io` (public, no auth) | Configure board slugs in `profile.yaml` |
| Lever | `api.lever.co/v0/postings` (public, no auth) | Enable in `profile.yaml` |
| Career pages | Configured per company | Respect robots.txt, rate-limit |

No ToS-violating scraping. If a site offers no public API or robots.txt-permitted crawl, it is out
of scope.

---

## Security

- All secrets via GitHub Encrypted Secrets or a local gitignored `.env`.
- `.env`, `credentials.json`, `token.json`, and `profile.yaml` (personal data) stay out of git —
  keep the repo private.
- No secrets printed to logs.

---

## Non-goals (never add these)

- Bulk application submission
- **LinkedIn automation of any kind**
- Auto-submit without human approval
- Fabricated CV content
- Any employer / client data
```
