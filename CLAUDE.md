# CLAUDE.md — ADA Lead Gen Project Rules

Read this file fully at the start of every session before touching code.

## What this project does
Discovers small businesses in the US with ADA-non-compliant websites, scores them as sales leads for BizStreamPro's accessibility remediation service, generates personalized cold-outreach email drafts, and logs everything to Google Sheets for manual review and send.

## What this project does NOT do
- Does not auto-send emails. Ever. Drafts only.
- Does not scrape login-walled, paywalled, or explicitly disallowed content.
- Does not target consumers (B2C). Business contacts only.
- Does not target non-profits, government, education, or healthcare patient portals.
- Does not store personal data beyond business contact info.

## Legal & compliance guardrails — non-negotiable

### CAN-SPAM Act (US federal law, every outbound email must comply)
Every email draft this project generates must satisfy ALL of these:

1. **Truthful headers** — From name, From email, Reply-to, and routing info must be accurate. No spoofing, no misleading sender.
2. **Truthful subject line** — Must accurately reflect the email content. No clickbait, no false urgency, no fake "Re:" or "Fwd:".
3. **Identification as commercial** — Does not need the word "ad" but must not masquerade as personal correspondence. Being a cold sales email is fine if the body makes the commercial nature clear.
4. **Valid physical postal address** — Real street address, PO Box, or registered commercial mailbox. Pulled from env `MY_BUSINESS_ADDRESS`. Included in every email footer.
5. **Clear, conspicuous unsubscribe** — Every email body must contain an unsubscribe link (`MY_UNSUBSCRIBE_URL`) and/or a reply-to-opt-out instruction. The mechanism must work for at least 30 days and process opt-outs within 10 business days.
6. **Honor opt-outs immediately** — The `opt_outs` SQLite table is the source of truth. The pipeline must filter against it before drafting or writing any email. Never sell, transfer, or reuse an opted-out address.

Penalty for violations: up to ~$53,000 per email. Treat this as a hard wall.

### Email deliverability hygiene (not law, but required)
- Send only from a real business domain with SPF, DKIM, and DMARC configured.
- Never send from gmail.com, yahoo.com, outlook.com, etc.
- Warm up any new sending domain for at least 2 weeks before bulk sends.

### State laws
Some states add requirements (California, Colorado). Default to the stricter rule when in doubt. If the project ever adds non-US targets, stop and flag it — Canada (CASL) and the EU (GDPR) have much stricter consent rules not covered by this project.

### Scraping ethics & legal
- Respect robots.txt on every fetch.
- 1 second minimum delay between requests to the same domain.
- Spoof a realistic User-Agent but do not impersonate Googlebot.
- Only collect publicly posted business contact info.
- Do not scrape behind logins, captchas, or "do not scrape" notices.
- If a site returns 403/429, back off and mark it as unreachable.

### Claims in outreach emails
- Never fabricate lawsuit statistics. Only use AI-generated stats if they are grounded in real, current, cite-able cases. If the AI can't verify, omit.
- Never claim the recipient has been sued, is about to be sued, or is "on a list."
- Never impersonate a government agency, the DOJ, or an attorney.
- Never use scare tactics presented as fact ("you WILL be sued").
- Position the service as preventative, helpful, and concrete.

### Data protection
- API keys live only in `.env`, never in code or logs.
- `creds.json` for Google service account is gitignored.
- SQLite database and Sheets ID are gitignored.
- Never log full email bodies or scraped contact lists to stdout in production.

## Technical rules

- Do not use browser extensions (WAVE, axe DevTools) — they cannot be automated. Use axe-core injected into Playwright.
- Do not hit the CDN for axe-core. Bundle it locally at `axe/axe.min.js`.
- Do not use premium LLM models for bulk work. Cheap model for scoring, premium only for final email drafts.
- Every LLM call must log token usage and cost to `llm_calls` table.
- Every new module gets a small test script before moving on.
- No secrets in git. Check `.gitignore` before every commit.

## The AI Insights layer is the heart of this project
The `ai/insights.py` output is saved to its own Sheets tab BEFORE any email is drafted. This serves two purposes:
1. The outreach generator uses these insights as the personalization backbone, so drafts are genuinely tailored rather than templated.
2. I can open the sheet, read the AI's reasoning about each lead, and manually write a better email if I want — the AI's thoughts are persistent reference material, not throwaway.

Never skip the insights step to save tokens. Never overwrite insights without preserving the prior version in a history column.

## Style & quality
- Type hints everywhere.
- Docstrings on every public function.
- No silent exception swallowing — log and continue, or raise.
- Prefer pure functions and explicit I/O boundaries.
- Small modules over one big file.

## Definition of done (per module)
- Runs without errors on a known-good test input.
- Has a small runnable test in `tests/` or a `if __name__ == "__main__":` demo.
- Does not exceed cost guardrails.
- Logs meaningfully.
- I've said "ok, move on."
