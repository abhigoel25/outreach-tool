"""
email_enricher.py
Generate best-guess emails for contacts at known tech companies using
standard corporate email patterns. Completely free and unlimited.

The most common pattern in tech is firstname.lastname@company.com (~70% accuracy).
Also provides SMTP verification to confirm whether a guessed address exists.

Workflow:
  1. python main.py guess-emails          # Generate guesses for known companies
  2. python main.py verify-emails         # SMTP verify (optional, slow)
  3. python main.py scrape-emails         # Web search for remaining unknowns
"""

import smtplib
import random
import string
import time
from database import get_conn

# ---------------------------------------------------------------------------
# Company domain map
# Keys are lowercase substrings that appear in the company name.
# Ordered from most-specific to least-specific within each group.
# ---------------------------------------------------------------------------

COMPANY_EMAIL_DOMAINS: dict[str, str] = {
    # Big Tech (handle "Google LLC", "Apple Inc.", "Meta Platforms Inc." etc.)
    "google": "google.com",
    "alphabet": "google.com",
    "microsoft": "microsoft.com",
    "amazon": "amazon.com",
    "apple": "apple.com",
    "meta platforms": "meta.com",
    "meta": "meta.com",
    "facebook": "meta.com",
    "netflix": "netflix.com",
    "nvidia": "nvidia.com",
    "salesforce": "salesforce.com",
    "oracle": "oracle.com",
    "ibm": "ibm.com",
    "intel": "intel.com",
    "qualcomm": "qualcomm.com",
    "amd": "amd.com",
    "cisco systems": "cisco.com",
    "cisco": "cisco.com",
    "vmware": "vmware.com",
    "broadcom": "broadcom.com",
    "dell technologies": "dell.com",
    "dell": "dell.com",
    "hp inc": "hp.com",
    "hewlett packard": "hp.com",
    "adobe": "adobe.com",
    "workday": "workday.com",
    "servicenow": "servicenow.com",
    "sap": "sap.com",
    "twitter": "twitter.com",
    "x corp": "twitter.com",
    "linkedin": "linkedin.com",
    # AI / ML
    "openai": "openai.com",
    "anthropic": "anthropic.com",
    "deepmind": "deepmind.com",
    "cohere": "cohere.com",
    "scale ai": "scale.com",
    "hugging face": "huggingface.co",
    "perplexity": "perplexity.ai",
    "mistral": "mistral.ai",
    "xai": "x.ai",
    "inflection": "inflection.ai",
    # Cloud & Infrastructure
    "cloudflare": "cloudflare.com",
    "datadog": "datadoghq.com",
    "snowflake": "snowflake.com",
    "databricks": "databricks.com",
    "hashicorp": "hashicorp.com",
    "confluent": "confluent.io",
    "elastic": "elastic.co",
    "splunk": "splunk.com",
    "pagerduty": "pagerduty.com",
    "okta": "okta.com",
    "crowdstrike": "crowdstrike.com",
    # Developer tools
    "github": "github.com",
    "gitlab": "gitlab.com",
    "atlassian": "atlassian.com",
    "vercel": "vercel.com",
    "supabase": "supabase.io",
    # SaaS / Productivity
    "figma": "figma.com",
    "notion": "notion.so",
    "slack": "slack.com",
    "zoom": "zoom.us",
    "dropbox": "dropbox.com",
    "box": "box.com",
    "hubspot": "hubspot.com",
    "zendesk": "zendesk.com",
    "airtable": "airtable.com",
    "retool": "retool.com",
    "twilio": "twilio.com",
    # Unicorns / Fintech
    "stripe": "stripe.com",
    "airbnb": "airbnb.com",
    "uber technologies": "uber.com",
    "uber": "uber.com",
    "lyft": "lyft.com",
    "doordash": "doordash.com",
    "instacart": "instacart.com",
    "robinhood": "robinhood.com",
    "coinbase": "coinbase.com",
    "plaid": "plaid.com",
    "palantir": "palantir.com",
    "mongodb": "mongodb.com",
    "ramp": "ramp.com",
    "brex": "brex.com",
    "gusto": "gusto.com",
    "rippling": "rippling.com",
    "bytedance": "bytedance.com",
    "tiktok": "tiktok.com",
    "shopify": "shopify.com",
    "arm": "arm.com",
    # E-commerce / Consumer
    "ebay": "ebay.com",
    "paypal": "paypal.com",
    "square": "squareup.com",
    "block": "block.xyz",
    # Quant / HFT / Finance
    "bloomberg": "bloomberg.net",
    "two sigma": "twosigma.com",
    "jane street": "janestreet.com",
    "citadel securities": "citadel.com",
    "citadel": "citadel.com",
    "d.e. shaw": "deshaw.com",
    "de shaw": "deshaw.com",
    "jump trading": "jumptrading.com",
    "point72": "point72.com",
    "renaissance": "rentec.com",
    "virtu": "virtu.com",
    "tower research": "tower-research.com",
    "hudson river trading": "hudsonrivertrading.com",
    "hudson river": "hudsonrivertrading.com",
    "optiver": "optiver.com",
    "akuna": "akunacapital.com",
    "susquehanna": "sig.com",
    "sig ": "sig.com",
    "drw": "drw.com",
    "five rings": "fiverings.com",
    # Banks (tech roles)
    "jpmorgan": "jpmorgan.com",
    "jp morgan": "jpmorgan.com",
    "goldman sachs": "gs.com",
    "morgan stanley": "morganstanley.com",
    "blackrock": "blackrock.com",
    "bank of america": "bofa.com",
    "wells fargo": "wellsfargo.com",
    "citigroup": "citi.com",
    "citi": "citi.com",
    # Other common tech
    "samsung": "samsung.com",
    "sony": "sony.com",
    "tsmc": "tsmc.com",
    "asml": "asml.com",
    "bain": "bain.com",
    "mckinsey": "mckinsey.com",
    "boston consulting": "bcg.com",
    "bcg": "bcg.com",
    "deloitte": "deloitte.com",
    "accenture": "accenture.com",
    "virtusa": "virtusa.com",
    "infosys": "infosys.com",
    "wipro": "wipro.com",
    "tata consultancy": "tcs.com",
}


def _normalize_company(s: str) -> str:
    """Strip common corporate suffixes for cleaner matching."""
    noise = [
        ", inc.", " inc.", ", llc", " llc", ", ltd.", " ltd.",
        ", corp.", " corp.", " corporation", " technologies",
        " platforms", " systems", " group", " holdings",
        " services", " solutions",
    ]
    s = s.lower()
    for n in noise:
        s = s.replace(n, "")
    return s.strip()


def _find_domain(company: str) -> str | None:
    """
    Find the corporate email domain for a company name.
    Strips common suffixes like 'Inc.', 'LLC', 'Platforms' before matching.
    """
    if not company:
        return None
    normalized = _normalize_company(company)
    company_lower = company.lower()

    # Try normalized version first, then original
    for text in (normalized, company_lower):
        for key, domain in COMPANY_EMAIL_DOMAINS.items():
            if key in text:
                return domain
    return None


def _guess_email(first: str, last: str, domain: str) -> str:
    """Generate firstname.lastname@domain — the most common corporate pattern."""
    def clean(s):
        return s.lower().replace(" ", "").replace("-", "").replace("'", "")
    return f"{clean(first)}.{clean(last)}@{domain}"


def guess_emails_from_company(dry_run: bool = False) -> tuple[int, int]:
    """
    For every contact whose email is still a placeholder, derive a
    best-guess email using the known corporate domain for their company.

    Returns (updated_count, skipped_count).
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, first_name, last_name, company
            FROM contacts
            WHERE email LIKE '%-pending.local'
            ORDER BY priority DESC, id ASC
        """).fetchall()

    updated = 0
    unknown = 0

    for row in rows:
        domain = _find_domain(row["company"] or "")
        if not domain:
            unknown += 1
            continue

        email = _guess_email(row["first_name"], row["last_name"], domain)

        if dry_run:
            print(f"  [dry-run] {row['first_name']} {row['last_name']} @ {row['company']}  ->  {email}")
            updated += 1
        else:
            try:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE contacts SET email=?, updated_at=date('now') WHERE id=?",
                        (email, row["id"])
                    )
                updated += 1
            except Exception:
                # Email unique constraint — another contact already has this email
                unknown += 1

    return updated, unknown


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    n, skipped = guess_emails_from_company(dry_run=dry)
    tag = "[DRY RUN] Would update" if dry else "Updated"
    print(f"{tag} {n} contacts with guessed emails.")
    print(f"Skipped {skipped} contacts with unknown or duplicate company domain.")


# ---------------------------------------------------------------------------
# SMTP verification
# ---------------------------------------------------------------------------
# Results:
#   'verified'      - server confirmed the address exists
#   'invalid'       - server explicitly rejected the address (RCPT 5xx)
#   'catch-all'     - domain accepts any address, can't tell real from fake
#   'unverifiable'  - port 25 blocked, timeout, or server refused probing
#   'error'         - unexpected error

_mx_cache: dict[str, list[str]] = {}
_catchall_cache: dict[str, bool] = {}


def _get_mx(domain: str) -> list[str]:
    """Return MX hostnames for domain, sorted by preference. Cached."""
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        import dns.resolver
        records = dns.resolver.resolve(domain, "MX", lifetime=5)
        hosts = [str(r.exchange).rstrip(".") for r in sorted(records, key=lambda r: r.preference)]
        _mx_cache[domain] = hosts
        return hosts
    except Exception:
        _mx_cache[domain] = []
        return []


def _smtp_probe(mx_host: str, email: str, timeout: int = 8) -> str:
    """Connect to mx_host and do an RCPT TO check. No email is sent."""
    try:
        with smtplib.SMTP(timeout=timeout) as smtp:
            smtp.connect(mx_host, 25)
            smtp.helo("mail.outreach-verify.com")
            smtp.mail("noreply@outreach-verify.com")
            code, _ = smtp.rcpt(email)
            smtp.quit()
            if code == 250:
                return "accepted"
            elif str(code).startswith("5"):
                return "rejected"
            return "unverifiable"
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected,
            ConnectionRefusedError, OSError, TimeoutError):
        return "unverifiable"
    except Exception:
        return "unverifiable"


def _is_catch_all(domain: str, mx_hosts: list[str]) -> bool:
    """Check whether a domain accepts mail to any address."""
    if domain in _catchall_cache:
        return _catchall_cache[domain]
    rand = "".join(random.choices(string.ascii_lowercase, k=18)) + "@" + domain
    for mx in mx_hosts[:1]:
        if _smtp_probe(mx, rand) == "accepted":
            _catchall_cache[domain] = True
            return True
    _catchall_cache[domain] = False
    return False


def verify_status(email: str) -> str:
    """Return the SMTP verification status of an email address."""
    try:
        domain = email.split("@")[1].lower()
    except IndexError:
        return "error"

    mx_hosts = _get_mx(domain)
    if not mx_hosts:
        return "unverifiable"

    if _is_catch_all(domain, mx_hosts):
        return "catch-all"

    for mx in mx_hosts[:2]:
        result = _smtp_probe(mx, email)
        if result == "accepted":
            return "verified"
        if result == "rejected":
            return "invalid"

    return "unverifiable"


def verify_guessed_emails(dry_run: bool = False, limit: int = None) -> dict:
    """
    SMTP-verify all guessed (non-placeholder) emails in the DB.
    Invalid addresses are reset to placeholder so they won't get sent.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, first_name, last_name, email
            FROM contacts
            WHERE email NOT LIKE '%-pending.local'
              AND email NOT LIKE '%-skipped.local'
              AND email NOT LIKE '%-duplicate.local'
              AND status = 'pending'
            ORDER BY priority DESC, id ASC
        """).fetchall()

    if limit:
        rows = rows[:limit]

    counts = {"verified": 0, "invalid": 0, "catch-all": 0, "unverifiable": 0, "error": 0}
    icon_map = {"verified": "+", "invalid": "x", "catch-all": "~", "unverifiable": "?", "error": "!"}

    print(f"Verifying {len(rows)} emails via SMTP...")
    print("(Google, Microsoft, Apple etc. return 'catch-all' or 'unverifiable' — that's expected)\n")

    for i, row in enumerate(rows, 1):
        email = row["email"]
        status = verify_status(email)
        counts[status] = counts.get(status, 0) + 1
        icon = icon_map.get(status, "?")
        print(f"  [{i}/{len(rows)}] {icon} {email}  ({status})")

        if status == "invalid" and not dry_run:
            slug = email.split("@")[0]
            with get_conn() as conn:
                conn.execute(
                    "UPDATE contacts SET email=?, updated_at=date('now') WHERE id=?",
                    (f"{slug}@guessed-invalid.local", row["id"])
                )

        time.sleep(0.5)

    print(f"\nSummary: {counts}")
    if not dry_run:
        print(f"Reset {counts['invalid']} confirmed-invalid addresses back to placeholder.")
    return counts