# ADA Lead Gen — BizStreamPro

Discovers small businesses with ADA-non-compliant websites and logs them as qualified sales leads with persistent AI-generated sales intelligence and email-draft placeholders in Google Sheets.

---

## 1. Clone the repo

```bash
git clone https://github.com/Purrpel/CloudCone.git
cd CloudCone
git checkout claude/create-github-repos-jp3aU
```

> All work lives on the `claude/create-github-repos-jp3aU` branch.

---

## 2. Install prerequisites

You need **Python 3.11+** and **Node.js** (for axe-core).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium

# Bundle axe-core locally (CDN is not used)
npm install axe-core
mkdir -p axe
cp node_modules/axe-core/axe.min.js axe/
```

---

## 3. Google Cloud setup (one-time)

1. **Enable APIs** in [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services:
   - Places API
   - Google Sheets API
2. **Create API key** (Places): Credentials → Create Credentials → API Key. Restrict to Places API. Put in `.env` as `GOOGLE_MAPS_API_KEY`.
3. **Create service account** (Sheets): Credentials → Create Credentials → Service Account. Open it → Keys → Add Key → JSON. Save the downloaded file as `creds.json` in the project root.
4. **Create a Google Sheet**, copy the ID from the URL (`https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`), and share it with the service account email (`…@…iam.gserviceaccount.com`) as **Editor**. Put the ID in `.env` as `GOOGLE_SHEETS_ID`.

---

## 4. LLM setup — pick ONE

The pipeline auto-detects the provider from whichever key is set. Leave `LLM_PROVIDER` blank unless both keys are set.

### Option A — Anthropic (Claude)
```env
ANTHROPIC_API_KEY=sk-ant-...
```
Defaults: cheap = `claude-haiku-4-5`, premium = `claude-opus-4-7`.

### Option B — OpenAI
```env
OPENAI_API_KEY=sk-...
```
Defaults: cheap = `gpt-4o-mini`, premium = `gpt-4-turbo`.

You can switch between providers any time — just swap which key is present in `.env`.

---

## 5. Fill in `.env`

```bash
cp .env.example .env
```

Open `.env` in an editor and fill every line that is empty. The ones you can leave blank while testing: `MY_CALENDAR_LINK`, `MY_WEBSITE`. Everything else is required.

---

## 6. Edit your target lists

`cities.txt` — one city per line, e.g. `Austin, TX`
`industries.txt` — one industry per line, e.g. `dentist`

Every (city × industry) pair is scanned. 20 cities × 13 industries at `LIMIT_PER_PAIR=25` → up to 6,500 businesses per run.

---

## 7. Run it

**One command does everything:**

```bash
python run.py
```

That script:
- Pre-flight checks that all keys, files, and axe-core are in place.
- Loads `cities.txt` + `industries.txt`.
- Runs the full pipeline for each pair concurrently.
- Writes qualified leads to Google Sheets (3 tabs: **Leads**, **AI Insights**, **Outreach Drafts**).
- Prints a grand-total summary (found / alive / qualified / written / cost).

You do **not** need to run any other file separately. The Python modules under `ada_lead_gen/` are imported by `run.py`.

---

## 8. Dedup — safe to re-run

Running `python run.py` a second time will **not** re-scan, re-process, or re-write anything already captured:

| Layer | Source of truth | Behavior |
|---|---|---|
| Google Places discovery | `places_cache` table in `ada_leads.db` | Cached search results reused for `PLACES_CACHE_DAYS` (default 7). |
| Website scanning | `scanned_domains` table | Any domain touched in the last `RESCAN_COOLDOWN_DAYS` (default 30) is skipped before any HTTP / LLM work. |
| Opt-outs | `opt_outs` table | Any email on the list is filtered before writing. |
| Sheets rows | domain → row index cache | Re-writing the same lead updates its existing row, not a duplicate one. |

If you want to force a rescan of a specific domain, delete its row from `scanned_domains`:
```bash
sqlite3 ada_leads.db "DELETE FROM scanned_domains WHERE domain = 'example.com';"
```

Or shorten the cooldown in `.env`:
```env
RESCAN_COOLDOWN_DAYS=0
```

---

## 9. CLI for ad-hoc tasks

`run.py` is the bulk entry point. For smaller operations, use the CLI:

```bash
python -m ada_lead_gen scan --city "Austin, TX" --industry dentist --limit 10
python -m ada_lead_gen report
python -m ada_lead_gen export --since 2026-01-01 --output leads.csv
python -m ada_lead_gen add-optout --email user@example.com
```

---

## 10. Cost guardrail

`MAX_RUN_COST_USD=5.00` in `.env` aborts the current (city, industry) run with `CostGuardrailError` if its LLM spend crosses the limit. The grand loop then moves on to the next pair.

---

## 11. Output tabs

1. **Leads** — domain, business name, city, industry, violations, phone, best email, lead score, tier, run_id, scanned_at.
2. **AI Insights** — business snapshot, pain point, 3 personalization hooks, industry lawsuit context, objection preempt, recommended tone, red flags.
3. **Outreach Drafts** — placeholder rows keyed on domain. Drafting emails is intentionally disabled; fill these manually.

---

## 12. Compliance reminder

Email sending is **not** wired in. Anything you send manually from the drafts must follow CAN-SPAM: truthful headers, commercial intent clear, valid postal address in footer (`MY_BUSINESS_ADDRESS`), working unsubscribe link (`MY_UNSUBSCRIBE_URL`). See [CLAUDE.md](CLAUDE.md) for the full list.
