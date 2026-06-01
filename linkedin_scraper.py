"""
linkedin_scraper.py
Scrapes Columbia University alumni from LinkedIn's alumni search page and saves
them to the outreach database. Does NOT require being connected to them.
"""

import asyncio
import os
import re
import random
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext, Page

load_dotenv()

LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "abhinavgoel115@gmail.com")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")

# Columbia University alumni search page — shows all alumni, not just connections
COLUMBIA_ALUMNI_URL = "https://www.linkedin.com/school/columbia-university/people/"
LINKEDIN_BASE = "https://www.linkedin.com"

PLAYWRIGHT_PROFILE = Path(__file__).parent / ".playwright_profile" / "linkedin"

DEFAULT_MAX_RESULTS = 5000

# Keywords to search on the alumni page — each runs as a separate filtered query
# and results are deduplicated by profile URL across all searches.
TECH_SEARCH_KEYWORDS = [
    "software engineer",
    "software developer",
    "data scientist",
    "data engineer",
    "machine learning",
    "ML engineer",
    "AI engineer",
    "backend engineer",
    "frontend engineer",
    "full stack",
    "platform engineer",
    "devops",
    "site reliability",
    "cloud engineer",
    "security engineer",
    "quantitative",
    "quant researcher",
    "research scientist",
    "applied scientist",
    "product manager",
    "mobile engineer",
    "computer science",
    "systems engineer",
    "infrastructure engineer",
]

# Job title keywords — same intent as columbia_alumni_scraper
_TECH_TITLE_KEYWORDS = [
    "software engineer", "software developer", "swe", "sde",
    "data scientist", "data engineer", "machine learning", "ml engineer",
    "ai engineer", "deep learning", "nlp engineer", "research scientist",
    "backend engineer", "frontend engineer", "full stack", "fullstack",
    "platform engineer", "devops", "site reliability", "sre",
    "cloud engineer", "infrastructure engineer", "systems engineer",
    "security engineer", "quantitative", "quant ", "algorithmic",
    "product manager", "technical product", "engineering manager",
    "cto", "vp engineering", "vp of engineering", "director of engineering",
    "head of engineering", "head of data", "head of product",
    "principal engineer", "staff engineer", "architect",
    "robotics", "embedded", "mobile engineer", "ios engineer", "android engineer",
    "blockchain", "developer", "programmer",
]

_TECH_COMPANIES = {
    "google", "alphabet", "microsoft", "amazon", "aws", "apple", "meta", "facebook",
    "netflix", "nvidia", "salesforce", "oracle", "ibm", "intel", "qualcomm", "amd",
    "twitter", "x.com", "linkedin", "adobe", "sap", "workday", "servicenow",
    "openai", "anthropic", "deepmind", "cohere", "mistral", "hugging face", "scale ai",
    "xai", "perplexity", "inflection",
    "stripe", "palantir", "snowflake", "databricks", "airbnb", "uber", "lyft",
    "doordash", "robinhood", "coinbase", "plaid", "twilio", "datadog", "cloudflare",
    "mongodb", "elastic", "github", "gitlab", "atlassian", "figma", "notion",
    "slack", "zoom", "dropbox", "hubspot", "okta", "crowdstrike", "pagerduty",
    "bytedance", "tiktok", "ramp", "brex", "rippling", "airtable", "retool",
    "vercel", "supabase", "hashicorp", "confluent",
    "samsung", "cisco", "vmware", "broadcom", "dell", "hp",
    "bloomberg", "two sigma", "jane street", "d.e. shaw", "citadel", "jump trading",
    "point72", "renaissance", "virtu", "tower research", "hudson river trading",
    "optiver", "akuna", "susquehanna", "sig", "drw", "five rings",
}


def _is_tech(role: str, company: str) -> bool:
    """Return True if the contact has a tech title or works at a known tech company."""
    role_lower = role.lower()
    if any(kw in role_lower for kw in _TECH_TITLE_KEYWORDS):
        return True
    company_lower = company.lower()
    if any(co in company_lower for co in _TECH_COMPANIES):
        return True
    return False


def _is_columbia_cs(subtitle: str, location: str) -> bool:
    """Return True if the person appears to be a Columbia CS/tech student or grad."""
    combined = f"{subtitle} {location}".lower()
    cs_signals = [
        "cs @", "cs@", "computer science", "computer engineering",
        "electrical engineering", "applied math", "statistics",
        "data science", "machine learning", "artificial intelligence",
        "seas", "fu foundation", "engineering at columbia",
    ]
    columbia_signals = ["columbia", "barnard", "seas"]
    has_cs = any(s in combined for s in cs_signals)
    has_columbia = any(s in combined for s in columbia_signals)
    return has_cs and has_columbia


_EXEC_TITLE_KEYWORDS = [
    "cto", "cio", "ceo", "chief technology", "chief data", "chief information",
    "chief engineer", "vp ", "vice president", "director", "head of",
]

_SENIOR_TITLE_KEYWORDS = [
    "principal engineer", "principal scientist", "staff engineer", "staff scientist",
    "distinguished", "engineering manager", "senior staff", "partner",
    "senior director", "senior principal",
]

_MID_TITLE_KEYWORDS = [
    "senior software", "senior data", "senior ml", "senior machine", "senior backend",
    "senior frontend", "senior full", "senior platform", "senior site",
    "tech lead", "technical lead", "lead engineer", "lead developer",
    "lead data", "lead software", "lead machine",
]


def _priority(role: str, company: str) -> int:
    """
    1-5 holistic priority:
      5 = big-tech company + exec/staff-level title
      4 = big-tech company (any role)  OR  exec/staff-level at any company
      3 = senior/lead title at any company
      2 = regular tech role
      1 = weak signal / student
    """
    role_lower = role.lower()
    company_lower = company.lower()
    at_big_tech = any(co in company_lower for co in _TECH_COMPANIES)
    is_exec = any(kw in role_lower for kw in _EXEC_TITLE_KEYWORDS)
    is_staff = any(kw in role_lower for kw in _SENIOR_TITLE_KEYWORDS)
    is_mid = any(kw in role_lower for kw in _MID_TITLE_KEYWORDS)
    if at_big_tech and (is_exec or is_staff):
        return 5
    if at_big_tech or is_exec or is_staff:
        return 4
    if is_mid:
        return 3
    if any(kw in role_lower for kw in _TECH_TITLE_KEYWORDS):
        return 2
    return 1


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def _ensure_logged_in(page: Page):
    """Navigate to LinkedIn and log in if not already authenticated."""
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)

    # If we land on feed, already logged in
    if "/feed" in page.url or "/mynetwork" in page.url:
        print("[linkedin] Already logged in.")
        return

    # Login required
    print("[linkedin] Logging in to LinkedIn...")
    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30_000)

    await page.locator("input#username").fill(LINKEDIN_EMAIL, timeout=10_000)
    await page.locator("input#password").fill(LINKEDIN_PASSWORD, timeout=10_000)
    await page.locator("button[type='submit']").click(timeout=10_000)

    await asyncio.sleep(4)

    # Handle CAPTCHA / verification challenge
    if "challenge" in page.url or "checkpoint" in page.url or "captcha" in page.url:
        print("\n[linkedin] LinkedIn security challenge detected!")
        print("[linkedin] Please complete the verification in the browser window.")
        print("[linkedin] Press Enter once you're past it...")
        await asyncio.get_event_loop().run_in_executor(None, input, "")

    # Wait for feed
    try:
        await page.wait_for_url("**/feed/**", timeout=30_000)
        print("[linkedin] Login successful!")
    except Exception:
        print(f"[linkedin] After login, landed on: {page.url}")


# ---------------------------------------------------------------------------
# Scrape Columbia alumni page
# ---------------------------------------------------------------------------

async def _scrape_alumni_page(page: Page, url: str, max_per_query: int = 300) -> list:
    """
    Scroll one keyword-filtered alumni URL and collect cards.
    Returns list of dicts: {name, subtitle, location, profile_url}.
    """
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(4)

    alumni = []
    seen_urls = set()
    stale_rounds = 0
    last_count = 0
    debug_printed = False

    print(f"[linkedin] Scrolling: {url.split('?')[1] if '?' in url else 'all alumni'}")

    while len(alumni) < max_per_query and stale_rounds < 6:
        cards = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();

            document.querySelectorAll('a[href*="/in/"]').forEach(anchor => {
                const href = anchor.href.split('?')[0];

                // Only /in/ profile paths
                const path = href.replace(/^https?:\\/\\/[^/]+/, '');
                if (!path.startsWith('/in/')) return;
                const slug = path.replace('/in/', '').replace(/\\/$/, '');
                if (!slug || slug.includes('/')) return;
                if (seen.has(href)) return;

                // Skip degree-indicator anchors (text is "· 2nd", "1st", etc.)
                const anchorText = anchor.textContent.trim()
                    .replace(/\\u00b7|\\u00a0|·/g, '').trim();
                if (!anchorText || /^[123](st|nd|rd)$/.test(anchorText) || anchorText.length < 2) return;

                seen.add(href);
                const name = anchorText;

                // Walk up to find the card container
                let card = anchor;
                for (let i = 0; i < 8; i++) {
                    if (!card.parentElement) break;
                    card = card.parentElement;
                    if (card.tagName === 'LI' || card.tagName === 'ARTICLE') break;
                }

                // Gather all leaf-node text from the card, excluding name and noise
                const leafTexts = [];
                const walk = (el) => {
                    if (el.children.length === 0) {
                        const t = el.textContent.trim()
                            .replace(/\u00b7|\u00a0|·/g, '').trim();
                        if (
                            t.length > 1 &&
                            t !== name &&
                            !/^[123](st|nd|rd)$/.test(t) &&
                            !/degree connection/i.test(t) &&
                            !/^Follow$/i.test(t) &&
                            !/^Message$/i.test(t) &&
                            !/^Connect$/i.test(t)
                        ) {
                            leafTexts.push(t);
                        }
                    } else {
                        Array.from(el.children).forEach(walk);
                    }
                };
                walk(card);

                // Deduplicate while preserving order
                const unique = [...new Set(leafTexts)];
                const subtitle = unique[0] || '';
                const location = unique[1] || '';

                results.push({ profile_url: href, name, subtitle, location });
            });
            return results;
        }""")

        if cards and not debug_printed:
            print(f"[linkedin] Sample card: {cards[0]}")
            if len(cards) > 1:
                print(f"[linkedin] Sample card 2: {cards[1]}")
            debug_printed = True

        for card in cards:
            url = card.get("profile_url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                alumni.append(card)

        if len(alumni) == last_count:
            stale_rounds += 1
        else:
            stale_rounds = 0
            last_count = len(alumni)

        print(f"[linkedin] Found {len(alumni)} alumni so far...")

        # Scroll both the window and any scrollable container
        await page.evaluate("""() => {
            window.scrollBy(0, 1500);
            // Also scroll any scrollable main container
            const main = document.querySelector('main, .scaffold-layout__main, [role="main"]');
            if (main) main.scrollBy(0, 1500);
        }""")
        await asyncio.sleep(random.uniform(2.0, 3.5))

        # Click "Show more results" if present
        try:
            btn = page.locator('button:has-text("Show more results"), button:has-text("Load more")')
            if await btn.count() > 0:
                await btn.first.click()
                await asyncio.sleep(2)
        except Exception:
            pass

    return alumni


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_scrape(
    max_results: int = DEFAULT_MAX_RESULTS,
    headless: bool = False,
):
    """
    Full LinkedIn alumni scrape pipeline:
    1. Log in (or reuse persistent session)
    2. Scroll Columbia alumni page to collect cards
    3. Parse name/role/company from each card and import to DB
    """
    from database import init_db, upsert_contact

    init_db()
    PLAYWRIGHT_PROFILE.mkdir(parents=True, exist_ok=True)

    print(f"[linkedin] Starting Columbia alumni LinkedIn scrape (max {max_results})...")

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            str(PLAYWRIGHT_PROFILE),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page: Page = await context.new_page()

        await _ensure_logged_in(page)

        all_seen_urls: set = set()
        raw_alumni: list = []

        for keyword in TECH_SEARCH_KEYWORDS:
            if len(raw_alumni) >= max_results:
                break
            encoded = keyword.replace(" ", "+")
            url = f"{COLUMBIA_ALUMNI_URL}?keywords={encoded}"
            print(f"\n[linkedin] Keyword search: '{keyword}'")
            try:
                cards = await _scrape_alumni_page(page, url, max_per_query=300)
            except Exception as e:
                err_str = str(e)
                if "ERR_CONNECTION_CLOSED" in err_str or "ERR_CONNECTION_RESET" in err_str or "net::" in err_str:
                    print(f"[linkedin] Connection dropped by LinkedIn — pausing 60s then continuing...")
                    await asyncio.sleep(60)
                    # Try once more
                    try:
                        cards = await _scrape_alumni_page(page, url, max_per_query=300)
                    except Exception:
                        print(f"[linkedin] Still failing on '{keyword}', skipping to next keyword.")
                        await asyncio.sleep(30)
                        continue
                else:
                    print(f"[linkedin] Unexpected error on '{keyword}': {e}, skipping.")
                    continue
            new_cards = [c for c in cards if c["profile_url"] not in all_seen_urls]
            for c in new_cards:
                all_seen_urls.add(c["profile_url"])
            raw_alumni.extend(new_cards)
            print(f"[linkedin] +{len(new_cards)} new (total unique: {len(raw_alumni)})")
            await asyncio.sleep(random.uniform(3.0, 6.0))

        await context.close()

    print(f"\n[linkedin] Collected {len(raw_alumni)} unique alumni cards across all keyword searches")

    imported = 0
    skipped_non_tech = 0
    skipped_error = 0

    for person in raw_alumni:
        raw_name = person.get("name", "").strip()
        if not raw_name:
            continue

        parts = raw_name.split(None, 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        # subtitle is usually "Role at Company" or "Role @ Company"
        subtitle = person.get("subtitle", "")
        role, company = "", ""
        if " at " in subtitle:
            role, company = subtitle.split(" at ", 1)
            role, company = role.strip(), company.strip()
        elif " @ " in subtitle:
            role, company = subtitle.split(" @ ", 1)
            role, company = role.strip(), company.strip()
        elif "@" in subtitle:
            role, company = subtitle.split("@", 1)
            role, company = role.strip(), company.strip()
        else:
            role = subtitle.strip()  # treat entire subtitle as role for filtering

        if not _is_tech(role, company) and not _is_columbia_cs(subtitle, person.get("location", "")):
            skipped_non_tech += 1
            continue

        profile_url = person.get("profile_url", "")
        slug = profile_url.rstrip("/").split("/")[-1] if profile_url else ""
        placeholder_email = f"{slug}@linkedin-pending.local" if slug else f"{first_name.lower()}.{last_name.lower()}@linkedin-pending.local"

        contact = {
            "first_name": first_name,
            "last_name": last_name,
            "email": placeholder_email,
            "company": company,
            "role": role,
            "linkedin_url": profile_url,
            "columbia_alumni": 1,
            "grad_year": "",
            "source": "linkedin_alumni",
            "notes": person.get("location", ""),
            "priority": _priority(role, company),
        }

        try:
            upsert_contact(contact)
            imported += 1
        except Exception as e:
            print(f"[linkedin] DB error for {first_name} {last_name}: {e}")
            skipped_error += 1

    print(f"[linkedin] Done! Imported: {imported}, Non-tech filtered: {skipped_non_tech}, Errors: {skipped_error}")
    return imported


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LinkedIn Columbia Alumni Scraper")
    parser.add_argument(
        "--max", type=int, default=DEFAULT_MAX_RESULTS, help="Max alumni to scrape"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run browser headlessly"
    )
    args = parser.parse_args()

    asyncio.run(run_scrape(max_results=args.max, headless=args.headless))
