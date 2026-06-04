"""
contact_importer.py
Import contacts from LinkedIn CSV exports, Apollo.io exports, or manual CSVs.

Priority scale (matches columbia_alumni_scraper.py and database ordering):
  5 = exec/staff-level at a major tech company
  4 = major tech company (any role), OR exec/staff at any company
  3 = senior/lead title at any company
  2 = regular tech role
  1 = weak signal / unclear role

columbia_alumni is stored as a separate field and used as a sort tiebreaker
within the same priority tier — it does NOT inflate the priority number.
A Columbia alum who is a junior dev and a Google VP who is not an alum
should not have the same priority.
"""

import re
import pandas as pd
from database import init_db, upsert_contact

# ---------------------------------------------------------------------------
# Priority computation (same logic as columbia_alumni_scraper._priority)
# ---------------------------------------------------------------------------

_BIG_TECH_COMPANIES = {
    "google", "alphabet", "microsoft", "amazon", "apple", "meta", "facebook",
    "openai", "anthropic", "netflix", "nvidia", "salesforce", "oracle", "ibm",
    "intel", "qualcomm", "amd", "cisco", "adobe", "workday", "servicenow",
    "stripe", "airbnb", "uber", "lyft", "doordash", "palantir", "databricks",
    "snowflake", "datadog", "cloudflare", "figma", "notion", "slack", "zoom",
    "shopify", "coinbase", "robinhood", "plaid", "twilio", "atlassian",
    "github", "gitlab", "bytedance", "tiktok", "twitter", "x corp",
    "bloomberg", "two sigma", "jane street", "citadel", "d.e. shaw", "de shaw",
    "jump trading", "point72", "renaissance", "optiver", "akuna", "susquehanna",
    "drw", "five rings", "hudson river", "virtu", "tower research",
    "jpmorgan", "goldman sachs", "morgan stanley", "blackrock",
}

# Short abbreviations matched with word boundaries to avoid false positives
# e.g. "cto" must NOT match inside "postdoctoral", "vp" must not match inside "development"
_EXEC_ABBREV_RE = re.compile(r'\b(cto|cio|ceo|coo|cpo|cso|cfo|vp)\b')

# Multi-word exec phrases — safe to match as substrings (long enough to avoid collisions)
_EXEC_PHRASES = [
    "chief technology", "chief data", "chief information", "chief operating",
    "chief product", "chief security", "chief financial",
    "vice president", "director", "head of",
]

_SENIOR_KEYWORDS = [
    "principal engineer", "principal scientist", "staff engineer", "staff scientist",
    "distinguished", "engineering manager", "senior staff", "senior director",
    "senior principal", "partner",
]

_MID_KEYWORDS = [
    "senior software", "senior data", "senior ml", "senior machine", "senior backend",
    "senior frontend", "senior full", "senior platform", "senior site",
    "tech lead", "technical lead", "lead engineer", "lead developer",
    "lead data", "lead software", "lead machine",
]

_TECH_ROLE_KEYWORDS = [
    "engineer", "developer", "software", "data", "ml", "ai", "product",
    "devops", "backend", "frontend", "fullstack", "full-stack", "swe",
    "sde", "architect", "scientist", "analyst", "quant", "researcher",
    "technical", "tech", "infrastructure", "platform", "security", "cyber",
    "cloud", "mobile", "ios", "android", "machine learning", "deep learning",
    "vp engineering", "director of engineering",
]

# Regex for _is_tech exec titles (same word-boundary safety)
_TECH_ABBREV_RE = re.compile(r'\b(cto|cio|ceo)\b')


def _is_exec(role_l: str) -> bool:
    """True if the role contains an executive-level title."""
    return bool(_EXEC_ABBREV_RE.search(role_l)) or any(kw in role_l for kw in _EXEC_PHRASES)


def _compute_priority(role: str, company: str) -> int:
    """
    Compute 1-5 priority purely from role + company.
    columbia_alumni is NOT a factor — it is a separate tiebreaker field in the DB.
    """
    role_l = (role or "").lower()
    company_l = (company or "").lower()

    at_big_tech = any(co in company_l for co in _BIG_TECH_COMPANIES)
    is_exec     = _is_exec(role_l)
    is_senior   = any(kw in role_l for kw in _SENIOR_KEYWORDS)
    is_mid      = any(kw in role_l for kw in _MID_KEYWORDS)
    is_tech     = bool(_TECH_ABBREV_RE.search(role_l)) or any(kw in role_l for kw in _TECH_ROLE_KEYWORDS)

    if at_big_tech and (is_exec or is_senior):
        return 5
    if at_big_tech or is_exec or is_senior:
        return 4
    if is_mid:
        return 3
    if is_tech:
        return 2
    return 1


def _is_tech(company: str, role: str) -> bool:
    text = f"{company} {role}".lower()
    if bool(_TECH_ABBREV_RE.search(text)) or any(kw in text for kw in _TECH_ROLE_KEYWORDS):
        return True
    if any(co in (company or "").lower() for co in _BIG_TECH_COMPANIES):
        return True
    return False


# ---------------------------------------------------------------------------
# Import functions
# ---------------------------------------------------------------------------

def import_linkedin(filepath: str) -> int:
    """Import from LinkedIn connections CSV export (3-row header)."""
    df = pd.read_csv(filepath, skiprows=3)
    df.columns = [c.strip() for c in df.columns]

    imported = 0
    for _, row in df.iterrows():
        first = str(row.get("First Name", "") or "").strip()
        last  = str(row.get("Last Name", "") or "").strip()
        company = str(row.get("Company", "") or "").strip()
        role    = str(row.get("Position", "") or "").strip()
        email   = str(row.get("Email Address", "") or "").strip()
        linkedin_url = str(row.get("URL", "") or "").strip()

        if not first:
            continue
        if not _is_tech(company, role):
            continue
        if not email or "@" not in email:
            safe_last = last.lower() if last else "x"
            email = f"{first.lower()}.{safe_last}.li@linkedin-pending.local"

        upsert_contact({
            "first_name":     first,
            "last_name":      last,
            "email":          email,
            "company":        company,
            "role":           role,
            "linkedin_url":   linkedin_url,
            "columbia_alumni": 0,
            "source":         "linkedin_csv",
            "priority":       _compute_priority(role, company),
        })
        imported += 1

    return imported


def import_apollo(filepath: str) -> int:
    """Import from Apollo.io CSV export (already has real emails)."""
    df = pd.read_csv(filepath)
    df.columns = [c.strip() for c in df.columns]

    imported = 0
    for _, row in df.iterrows():
        first = str(row.get("First Name", "") or "").strip()
        last  = str(row.get("Last Name", "") or "").strip()
        email = str(row.get("Email", "") or "").strip()
        # Handle both legacy "Company" and Apollo standard "Company Name"
        company = str(row.get("Company Name", row.get("Company", "")) or "").strip()
        role    = str(row.get("Title", "") or "").strip()
        # Handle both legacy "LinkedIn URL" and Apollo standard "Person Linkedin Url"
        linkedin_url = str(row.get("Person Linkedin Url", row.get("LinkedIn URL", "")) or "").strip()

        if not first or not email or "@" not in email:
            continue
        if not _is_tech(company, role):
            continue

        upsert_contact({
            "first_name":     first,
            "last_name":      last,
            "email":          email,
            "company":        company,
            "role":           role,
            "linkedin_url":   linkedin_url,
            "columbia_alumni": 0,
            "source":         "apollo",
            "priority":       _compute_priority(role, company),
        })
        imported += 1

    return imported


def import_manual(filepath: str, default_columbia: int = 0) -> int:
    """Import from a generic CSV (e.g., manually curated list)."""
    df = pd.read_csv(filepath)
    df.columns = [c.strip() for c in df.columns]

    imported = 0
    for _, row in df.iterrows():
        first = str(row.get("first_name", "") or "").strip()
        last  = str(row.get("last_name", "") or "").strip()
        email = str(row.get("email", "") or "").strip()
        company = str(row.get("company", "") or "").strip()
        role    = str(row.get("role", "") or "").strip()
        linkedin_url   = str(row.get("linkedin_url", "") or "").strip()
        columbia_alumni = int(row.get("columbia_alumni", default_columbia) or default_columbia)
        grad_year = str(row.get("grad_year", "") or "").strip()
        notes     = str(row.get("notes", "") or "").strip()

        # Use explicit priority from CSV if provided; otherwise compute from role + company
        raw_p = str(row.get("priority", "") or "").strip()
        if raw_p and raw_p not in ("nan", ""):
            priority = int(float(raw_p))
        else:
            priority = _compute_priority(role, company)

        if not first:
            continue
        if not email or "@" not in email:
            safe_last = last.lower() if last else "x"
            email = f"{first.lower()}.{safe_last}@manual-pending.local"

        upsert_contact({
            "first_name":     first,
            "last_name":      last,
            "email":          email,
            "company":        company,
            "role":           role,
            "linkedin_url":   linkedin_url,
            "columbia_alumni": columbia_alumni,
            "grad_year":      grad_year,
            "source":         "manual",
            "notes":          notes,
            "priority":       priority,
        })
        imported += 1

    return imported


def enrich_from_apollo(filepath: str) -> int:
    """
    Match rows in an Apollo-enriched CSV against existing contacts by LinkedIn URL
    and update their placeholder emails with real addresses.
    """
    from database import enrich_email_by_linkedin

    df = pd.read_csv(filepath)
    df.columns = [c.strip() for c in df.columns]

    updated = skipped_no_data = skipped_no_match = 0

    for _, row in df.iterrows():
        email        = str(row.get("Email", "") or "").strip()
        linkedin_url = str(row.get("LinkedIn URL", "") or "").strip()

        if not email or not linkedin_url or "@" not in email or email.endswith(".local"):
            skipped_no_data += 1
            continue

        if enrich_email_by_linkedin(linkedin_url, email):
            updated += 1
        else:
            skipped_no_match += 1

    print(f"  Matched and updated:       {updated}")
    print(f"  No LinkedIn match in DB:   {skipped_no_match}")
    print(f"  Skipped (missing data):    {skipped_no_data}")
    return updated