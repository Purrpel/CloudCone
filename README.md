# ADA Lead Gen — BizStreamPro

Discovers small businesses with ADA-non-compliant websites and logs them as qualified sales leads, complete with personalized cold-outreach email drafts.

---

## Prerequisites

- Python 3.11+
- A Google Cloud project
- An Anthropic or OpenAI account

---

## Google Cloud Setup

### 1. Enable APIs
In the [Google Cloud Console](https://console.cloud.google.com/):
- Enable **Places API** (for business discovery)
- Enable **Google Sheets API** (for lead logging)

### 2. Create API Key (Places)
- Go to **APIs & Services → Credentials → Create Credentials → API Key**
- Restrict it to the Places API
- Copy the key → `GOOGLE_MAPS_API_KEY` in `.env`

### 3. Create Service Account (Sheets)
- Go to **APIs & Services → Credentials → Create Credentials → Service Account**
- Give it a name, click **Done**
- Open the service account → **Keys → Add Key → JSON** → download as `creds.json`
- Place `creds.json` in the project root (it is gitignored)
- Set `GOOGLE_SERVICE_ACCOUNT_JSON=./creds.json` in `.env`

### 4. Create & Share the Google Sheet
- Create a new Google Sheet
- Copy its ID from the URL: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`
- Set `GOOGLE_SHEETS_ID=<SHEET_ID>` in `.env`
- Share the sheet with the service account email (looks like `name@project.iam.gserviceaccount.com`) — give it **Editor** access

---

## LLM Setup

### Anthropic (default)
- Get an API key at [console.anthropic.com](https://console.anthropic.com)
- Set `ANTHROPIC_API_KEY=` in `.env`
- Set `LLM_PROVIDER=anthropic`

### OpenAI (alternative)
- Get an API key at [platform.openai.com](https://platform.openai.com)
- Set `OPENAI_API_KEY=` in `.env`
- Set `LLM_PROVIDER=openai`
- Set `CHEAP_MODEL=gpt-4o-mini` and `PREMIUM_MODEL=gpt-4-turbo`

---

## Installation

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # fill in all values
```

---

## First Run

```bash
python -m ada_lead_gen scan --city "Austin, TX" --industry dentist --limit 25
```

### Multi-city / multi-industry run

```bash
python -m ada_lead_gen scan --cities-file cities.txt --industries-file industries.txt --limit 50
```

---

## CLI Reference

| Command | Description |
|---|---|
| `scan --city "X" --industry Y --limit N` | Run pipeline for one city/industry |
| `scan --cities-file f --industries-file f` | Batch run from files |
| `report` | Show stats from last run |
| `export --since 2026-04-01` | CSV dump of leads |
| `add-optout --email x@y.com` | Add email to opt-out list |
| `regenerate-insights --domain x.com` | Rerun AI insights for one lead |
| `draft-email --domain x.com` | Redraft outreach email for one lead |

---

## Cost Guardrails

Set `MAX_RUN_COST_USD=5.00` in `.env`. The pipeline aborts if projected LLM spend exceeds this. An end-of-run summary logs total spend and per-stage breakdown.

---

## Output

Three Google Sheets tabs are written:

1. **Leads** — qualified businesses with accessibility scores and contact info
2. **AI Insights** — persistent AI reasoning per lead (pain points, personalization hooks, red flags)
3. **Outreach Drafts** — subject + two body variants per lead, status tracking

**Emails are never auto-sent.** Review drafts in the sheet before sending.
