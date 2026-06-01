"""
email_scraper.py
Multi-signal web email finder. Searches GitHub, DuckDuckGo, and optionally
Hunter.io to recover real email addresses for contacts with placeholder emails.

Completely free using GitHub + DuckDuckGo signals.
Hunter.io is optional: 25 free lookups/month (set HUNTER_API_KEY in .env).
GitHub token is optional but highly recommended: raises rate limit from 60 to
5,000 requests/hour (set GITHUB_TOKEN in .env — any personal access token works).

Usage via main.py:
  python main.py scrape-emails              # process all pending contacts
  python main.py scrape-emails --limit 100  # first 100 only
  python main.py scrape-emails --dry-run    # preview, don't save

Or standalone:
  python email_scraper.py
  python email_scraper.py --limit 50 --dry-run
"""

import os
import re
import time
import random
import requests
from dotenv import load_dotenv
from database import get_conn

load_dotenv()

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
# Get a free GitHub personal access token at https://github.com/settings/tokens
# Any token works — no special scopes needed for public API access.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# ---------------------------------------------------------------------------
# Regex and constants
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")

# Domains that appear in search results but are never the person's real email
_NOISE_DOMAINS = {
    "example.com", "test.com", "sentry.io",
    "github.com", "githubusercontent.com",
    "noreply.github.com", "users.noreply.github.com",
    "wixpress.com", "squarespace.com", "wordpress.com",
    "w3.org", "schema.org", "purl.org",
    "placeholder.com", "yourdomain.com",
}

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "outreach-email-finder/1.0",
}
if GITHUB_TOKEN:
    _GITHUB_HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_email(email: str, preferred_domain: str = None) -> bool:
    """Sanity-check a found email."""
    if not email or "@" not in email:
        return False
    local, domain = email.lower().rsplit("@", 1)
    if domain in _NOISE_DOMAINS:
        return False
    if len(local) < 2 or len(domain) < 4 or "." not in domain:
        return False
    # Must not look like a file extension or URL artifact
    if local.startswith("//") or local.endswith("."):
        return False
    if preferred_domain and domain != preferred_domain.lower():
        return False
    return True


def _name_in_email(first: str, last: str, email: str) -> bool:
    """Return True if the email local-part contains part of the person's name."""
    local = email.split("@")[0].lower()
    first_l, last_l = first.lower(), last.lower()
    # Accept if first 3+ chars of first or last name appear in local
    return (
        (len(first_l) >= 3 and first_l[:4] in local) or
        (len(last_l) >= 3 and last_l[:4] in local) or
        first_l in local or last_l in local
    )


def _find_domain_for_company(company: str) -> str | None:
    """Reuse the domain map from email_enricher without re-importing at module level."""
    try:
        from email_enricher import _find_domain
        return _find_domain(company)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Signal 1: GitHub user search
# ---------------------------------------------------------------------------

def search_github(first: str, last: str, company: str) -> str | None:
    """
    Search GitHub for a user matching name + company.
    Returns their public email if available and the name/company check passes.

    Rate limits:
      - No token: 60 requests/hour (very slow for large batches)
      - With GITHUB_TOKEN: 5,000 requests/hour
    """
    query = f"{first} {last} {company}".strip()
    try:
        resp = requests.get(
            "https://api.github.com/search/users",
            params={"q": query, "per_page": 5},
            headers=_GITHUB_HEADERS,
            timeout=12,
        )
        if resp.status_code == 403:
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            if remaining == "0":
                reset = resp.headers.get("X-RateLimit-Reset", "")
                print(f"[github] Rate limit hit. Add GITHUB_TOKEN to .env. Reset at unix ts: {reset}")
            return None
        if not resp.ok:
            return None

        items = resp.json().get("items", [])
        if not items:
            return None

        company_key = company.lower()[:8]

        for user in items[:3]:
            login = user.get("login", "")
            time.sleep(0.3)

            detail = requests.get(
                f"https://api.github.com/users/{login}",
                headers=_GITHUB_HEADERS,
                timeout=10,
            )
            if not detail.ok:
                continue

            d = detail.json()
            email = (d.get("email") or "").strip().lower()
            gh_name = (d.get("name") or "").lower()
            gh_company = (d.get("company") or "").lower().replace("@", "").strip()

            if not email or not _valid_email(email):
                continue
            if "noreply" in email:
                continue

            # Name must roughly match
            name_ok = (
                first.lower() in gh_name or
                last.lower() in gh_name or
                gh_name in f"{first} {last}".lower()
            )
            # Company should roughly match (lenient — GitHub companies are freeform)
            company_ok = (
                not gh_company or
                company_key in gh_company or
                gh_company[:8] in company_key
            )

            if name_ok and company_ok:
                return email

        return None

    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# Signal 2: DuckDuckGo HTML search
# ---------------------------------------------------------------------------

def search_duckduckgo(first: str, last: str, company: str, domain: str = None) -> str | None:
    """
    POST to DuckDuckGo's HTML endpoint and scan results for email patterns.
    Tries multiple query strategies.
    """
    queries = []
    # Strategy A: exact name + known domain (highest precision)
    if domain:
        queries.append(f'"{first} {last}" "@{domain}"')
    # Strategy B: name + company + email keyword
    queries.append(f'"{first} {last}" "{company}" email')
    # Strategy C: GitHub profile
    queries.append(f'"{first} {last}" {company} site:github.com')

    for query in queries:
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "kl": "us-en"},
                headers={"User-Agent": _BROWSER_UA, "Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            if not resp.ok:
                time.sleep(2)
                continue

            found = _EMAIL_RE.findall(resp.text)
            for email in found:
                email = email.lower().rstrip(".,;>\"'")
                if not _valid_email(email):
                    continue
                e_domain = email.split("@")[1]
                # Prefer company domain match
                if domain and e_domain == domain and _name_in_email(first, last, email):
                    return email
                # Accept any plausible-looking personal/work email
                if e_domain not in _NOISE_DOMAINS and _name_in_email(first, last, email):
                    return email

            time.sleep(random.uniform(2.0, 4.0))

        except requests.RequestException:
            time.sleep(3)

    return None


# ---------------------------------------------------------------------------
# Signal 3: Hunter.io Email Finder API
# ---------------------------------------------------------------------------

def search_hunter(first: str, last: str, domain: str) -> str | None:
    """
    Use Hunter.io's Email Finder API. Free tier: 25 lookups/month.
    Set HUNTER_API_KEY in .env — get a free key at hunter.io.
    """
    if not HUNTER_API_KEY or not domain:
        return None

    try:
        resp = requests.get(
            "https://api.hunter.io/v2/email-finder",
            params={
                "domain": domain,
                "first_name": first,
                "last_name": last,
                "api_key": HUNTER_API_KEY,
            },
            timeout=15,
        )
        if not resp.ok:
            return None

        data = resp.json().get("data", {})
        email = (data.get("email") or "").strip()
        score = data.get("score", 0)

        # Only accept high-confidence results
        if email and score >= 50 and _valid_email(email):
            return email
        return None

    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# Signal 4: Personal / academic pages via DuckDuckGo result links
# ---------------------------------------------------------------------------

def search_personal_page(first: str, last: str, company: str) -> str | None:
    """
    Look for a personal website, university page, or research profile
    by fetching the top DuckDuckGo result links and scanning their HTML for emails.
    """
    query = f'"{first} {last}" {company} personal site OR about OR contact'
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "us-en"},
            headers={"User-Agent": _BROWSER_UA, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if not resp.ok:
            return None

        # Extract result URLs from DuckDuckGo HTML
        url_re = re.compile(r'uddg=([^"&]+)', re.IGNORECASE)
        from urllib.parse import unquote
        urls = [unquote(u) for u in url_re.findall(resp.text)]

        # Filter to plausible personal/academic pages
        skip_domains = {"linkedin.com", "twitter.com", "facebook.com",
                        "instagram.com", "youtube.com", "indeed.com",
                        "glassdoor.com", "yelp.com", "bloomberg.com"}

        for url in urls[:4]:
            try:
                url_domain = url.split("/")[2].lower().replace("www.", "")
                if any(d in url_domain for d in skip_domains):
                    continue

                page = requests.get(
                    url,
                    headers={"User-Agent": _BROWSER_UA},
                    timeout=10,
                    allow_redirects=True,
                )
                if not page.ok:
                    continue

                emails = _EMAIL_RE.findall(page.text)
                for email in emails:
                    email = email.lower().rstrip(".,;>\"'")
                    if _valid_email(email) and _name_in_email(first, last, email):
                        return email

                time.sleep(random.uniform(1.0, 2.0))

            except Exception:
                continue

        return None

    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# Main finder
# ---------------------------------------------------------------------------

def find_email(contact: dict) -> tuple[str | None, str]:
    """
    Try all signals for a single contact.
    Returns (email_or_None, signal_name).
    Order: Hunter.io → GitHub → DuckDuckGo → personal page.
    """
    first = (contact.get("first_name") or "").strip()
    last = (contact.get("last_name") or "").strip()
    company = (contact.get("company") or "").strip()

    if not first or not last:
        return None, "missing_name"

    domain = _find_domain_for_company(company) if company else None

    # Signal 1: Hunter.io (best quality, rate-limited)
    if domain and HUNTER_API_KEY:
        email = search_hunter(first, last, domain)
        if email:
            return email, "hunter"
        time.sleep(0.5)

    # Signal 2: GitHub (great for engineers, free)
    if company:
        email = search_github(first, last, company)
        if email:
            return email, "github"
        time.sleep(random.uniform(0.5, 1.5))

    # Signal 3: DuckDuckGo broad search
    email = search_duckduckgo(first, last, company, domain)
    if email:
        return email, "duckduckgo"

    # Signal 4: Personal/academic page (slower, last resort)
    if company:
        email = search_personal_page(first, last, company)
        if email:
            return email, "personal_page"

    return None, "not_found"


# ---------------------------------------------------------------------------
# Batch enrichment
# ---------------------------------------------------------------------------

def run_enrichment(limit: int = None, dry_run: bool = False) -> dict:
    """
    Process all contacts with placeholder emails and try to find real ones.
    Saves found emails directly to the database.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, first_name, last_name, company, linkedin_url
            FROM contacts
            WHERE email LIKE '%-pending.local'
            ORDER BY priority DESC, id ASC
        """).fetchall()

    if limit:
        rows = rows[:limit]

    total = len(rows)
    counts = {"found": 0, "not_found": 0, "errors": 0}
    signals = {}

    print(f"[email_scraper] Searching for emails for {total} contacts...")
    print(f"[email_scraper] Signals active:")
    print(f"  GitHub:   {'5,000 req/hr (token set)' if GITHUB_TOKEN else '60 req/hr (no token — set GITHUB_TOKEN in .env)'}")
    print(f"  Hunter:   {'active' if HUNTER_API_KEY else 'inactive (set HUNTER_API_KEY in .env for 25 free/month)'}")
    print(f"  DDG:      always active")
    print(f"  Pages:    always active (slower)")
    if dry_run:
        print("[email_scraper] DRY RUN — no changes will be saved.\n")
    else:
        print()

    for i, row in enumerate(rows, 1):
        contact = dict(row)
        name = f"{contact['first_name']} {contact.get('last_name', '')}"
        co = contact.get("company", "?")

        try:
            email, signal = find_email(contact)
        except Exception as e:
            print(f"  [{i}/{total}] ! ERROR {name}: {e}")
            counts["errors"] += 1
            continue

        if email:
            print(f"  [{i}/{total}] + FOUND  {name} ({co}): {email}  [{signal}]")
            counts["found"] += 1
            signals[signal] = signals.get(signal, 0) + 1

            if not dry_run:
                try:
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE contacts SET email=?, updated_at=date('now') WHERE id=?",
                            (email, contact["id"])
                        )
                except Exception as db_e:
                    if "UNIQUE constraint" in str(db_e):
                        # This email is already in the DB for someone else
                        dup_slug = f"{email.split('@')[0]}.dup@duplicate-skipped.local"
                        with get_conn() as conn:
                            conn.execute(
                                "UPDATE contacts SET email=?, status='skipped', updated_at=date('now') WHERE id=?",
                                (dup_slug, contact["id"])
                            )
                        print(f"    (duplicate — marked skipped)")
                    else:
                        print(f"    DB error: {db_e}")
        else:
            print(f"  [{i}/{total}] - miss    {name} ({co})")
            counts["not_found"] += 1

        # Polite delay between contacts
        time.sleep(random.uniform(2.0, 5.0))

    print(f"\n[email_scraper] Complete:")
    print(f"  Found:     {counts['found']}/{total}")
    print(f"  Not found: {counts['not_found']}/{total}")
    print(f"  Errors:    {counts['errors']}/{total}")
    if signals:
        print(f"  By signal: {signals}")
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Web email scraper for outreach contacts")
    parser.add_argument("--limit", type=int, default=None, help="Max contacts to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    run_enrichment(limit=args.limit, dry_run=args.dry_run)