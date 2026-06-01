# Internship Outreach Tool — Setup Guide

Automated daily email outreach to Columbia alumni and LinkedIn connections.

---

## 1. Prerequisites

- Python 3.11+
- A Google account (Gmail) for sending emails
- An Anthropic API key (optional — falls back to templates if missing)

---

## 2. Install Dependencies

```bash
cd internship-outreach
pip install -r requirements.txt
playwright install chromium
```

---

## 3. Gmail API Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable **Gmail API** under APIs & Services → Library
4. Go to APIs & Services → Credentials → Create Credentials → OAuth Client ID
5. Application type: **Desktop app**
6. Download the JSON file and save it as `credentials.json` in this folder
7. Run `python main.py test-gmail` — it will open a browser to authorize

> The tool only requests `gmail.send` scope. It cannot read your inbox.

---

## 4. Configure `.env`

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
# Anthropic API (get from https://console.anthropic.com)
ANTHROPIC_API_KEY=sk-ant-...

# Columbia UNI and password (for alumni scraper)
COLUMBIA_UNI=ag5252
COLUMBIA_PASSWORD=your_password

# LinkedIn credentials (for connections scraper)
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=your_password

# Emails per day (recommended: 10-20)
DAILY_LIMIT=15
```

---

## 5. Fetch Contacts Automatically

### Columbia Alumni Directory
Scrapes alumni in Computer/IT industry via the Coveo API:

```bash
python main.py fetch columbia
```

- Opens a browser window (you'll need to approve Duo MFA if prompted)
- Fetches up to 500 alumni per run
- Imports with placeholder emails — enrich via Apollo.io (see below)

### LinkedIn Connections
Scrapes your 1st-degree LinkedIn connections:

```bash
python main.py fetch linkedin
```

- Opens a browser window, logs in automatically
- Scrolls your connections list and visits each profile
- Filters to tech-relevant people automatically
- Imports with placeholder emails if no email found

### Both at once
```bash
python main.py fetch all
```

---

## 6. Enrich Emails (Apollo.io)

Columbia and LinkedIn profiles don't include email addresses. To get real ones:

1. Go to [apollo.io](https://app.apollo.io) (free tier: 50 exports/month)
2. Search for the person by name + company
3. Export to CSV
4. Import: `python main.py import apollo apollo_export.csv`

Contacts with placeholder emails (ending in `@*-pending.local`) are automatically
skipped during email sending — only contacts with real emails get emailed.

---

## 7. Import from CSV (Manual)

If you have a spreadsheet of contacts:

```bash
python main.py import linkedin  LinkedIn_Connections.csv
python main.py import apollo    apollo_export.csv
python main.py import columbia  columbia_list.csv
python main.py import manual    any_contacts.csv
```

---

## 8. Run the Daily Batch

```bash
python main.py
```

Or preview without sending:

```bash
python main.py --dry-run
```

---

## 9. Schedule Daily Runs (Windows Task Scheduler)

1. Open Task Scheduler → Create Basic Task
2. Name: "Internship Outreach"
3. Trigger: Daily, 9:00 AM
4. Action: Start a program
   - Program: `C:\Python311\python.exe` (or `python`)
   - Arguments: `main.py`
   - Start in: `C:\path\to\internship-outreach\`
5. Finish

---

## 10. CLI Reference

| Command | Description |
|---|---|
| `python main.py` | Run daily email batch |
| `python main.py --dry-run` | Preview without sending |
| `python main.py fetch columbia` | Scrape Columbia alumni |
| `python main.py fetch linkedin` | Scrape LinkedIn connections |
| `python main.py fetch all` | Scrape both |
| `python main.py import apollo FILE` | Import Apollo.io CSV |
| `python main.py import linkedin FILE` | Import LinkedIn CSV export |
| `python main.py import columbia FILE` | Import Columbia alumni CSV |
| `python main.py stats` | Show contact/email stats |
| `python main.py list` | List all contacts |
| `python main.py list --status pending` | List pending contacts |
| `python main.py test-gmail` | Test Gmail connection |

---

## 11. Contact Status Flow

```
pending → emailed → replied
       → skipped
```

Contacts with placeholder emails are shown but skipped automatically during sending.
