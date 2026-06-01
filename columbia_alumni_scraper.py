"""
columbia_alumni_scraper.py
Scrapes the Columbia Alumni Community directory using Playwright + Coveo REST API.
Imports contacts directly into the outreach database.
"""

import asyncio
import json
import os
import re
import time
import random
import requests
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext, Page

load_dotenv()

COLUMBIA_UNI = os.getenv("COLUMBIA_UNI", "ag5252")
COLUMBIA_PASSWORD = os.getenv("COLUMBIA_PASSWORD", "")

COVEO_ENDPOINT = "https://columbiauniversityalumniportal.org.coveo.com/rest/search/v2"
PORTAL_HOME = "https://community.alumni.columbia.edu/s/"
PORTAL_URL = (
    "https://community.alumni.columbia.edu/s/global-search/%40uri"
    "#t=Alumni&sort=relevancy"
    "&f:Industry=[Computer%2FInformation%20Technology]"
    "&f:Industry:operator=or"
)
PROFILE_BASE = "https://community.alumni.columbia.edu"

PLAYWRIGHT_PROFILE = Path(__file__).parent / ".playwright_profile" / "columbia"

# Max results to fetch per scrape run
DEFAULT_MAX_RESULTS = 3000


# ---------------------------------------------------------------------------
# Token capture
# ---------------------------------------------------------------------------

async def get_coveo_token(headless: bool = False) -> str:
    """
    Navigate to the Columbia alumni portal, log in if necessary,
    and capture the Coveo Bearer token from network requests.
    Returns the token string.
    """
    PLAYWRIGHT_PROFILE.mkdir(parents=True, exist_ok=True)

    captured = {"token": None}

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            str(PLAYWRIGHT_PROFILE),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page: Page = await context.new_page()

        # Route Coveo requests to capture the Bearer token
        async def handle_route(route):
            request = route.request
            if "coveo.com/rest/search" in request.url:
                auth = request.headers.get("authorization", "")
                if auth.startswith("Bearer ") and captured["token"] is None:
                    captured["token"] = auth[len("Bearer "):]
                    print(f"[columbia] Coveo token captured ({len(captured['token'])} chars)")
            await route.continue_()

        await page.route("**", handle_route)

        print("[columbia] Navigating to Columbia alumni portal...")
        await page.goto(PORTAL_HOME, wait_until="domcontentloaded", timeout=60_000)

        # Handle login if redirected to SSO
        current = page.url
        if "login" in current or "sso" in current.lower() or "columbia.edu/as/" in current:
            print("[columbia] Login page detected, authenticating...")
            await _login(page)

        # Navigate to the search page to trigger the Coveo auth request
        print("[columbia] Navigating to alumni search page...")
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)

        print("[columbia] Waiting for Coveo token...")
        for _ in range(20):
            if captured["token"]:
                break
            await asyncio.sleep(1)

        await context.close()

    if not captured["token"]:
        raise RuntimeError(
            "Could not capture Coveo Bearer token. "
            "Try running with headless=False to see what's happening."
        )

    return captured["token"]


async def _login(page: Page):
    """Fill Columbia UNI/password on the SSO page, handle Duo MFA."""
    try:
        # Columbia SSO: UNI field
        uni_field = page.locator(
            "input#username, input[name='user'], input[name='uid'], input[type='text']"
        ).first
        await uni_field.fill(COLUMBIA_UNI, timeout=10_000)

        pw_field = page.locator(
            "input#password, input[name='password'], input[type='password']"
        ).first
        await pw_field.fill(COLUMBIA_PASSWORD, timeout=10_000)

        submit = page.locator(
            "button[type='submit'], input[type='submit'], "
            "button:has-text('Login'), button:has-text('Sign in')"
        ).first
        await submit.click(timeout=10_000)

        # Wait a moment for Duo to appear
        await asyncio.sleep(3)

        # Check for Duo MFA
        if "duo" in page.url.lower() or await page.locator("iframe[src*='duo']").count() > 0:
            print("\n[columbia] Duo MFA detected!")
            print("[columbia] Please approve the Duo push notification on your phone.")
            print("[columbia] Press Enter here once you've approved it...")
            await asyncio.get_event_loop().run_in_executor(None, input, "")

        # Wait for redirect back to portal
        await page.wait_for_url("**/community.alumni.columbia.edu/**", timeout=120_000)
        print("[columbia] Login successful!")

    except Exception as e:
        print(f"[columbia] Login step error (may be fine if already logged in): {e}")


# ---------------------------------------------------------------------------
# Tech filtering
# ---------------------------------------------------------------------------

_TECH_TITLE_KEYWORDS = [
    # Software / Engineering
    "software engineer", "software developer", "software architect",
    "backend engineer", "frontend engineer", "full stack", "fullstack",
    "mobile engineer", "ios engineer", "android engineer",
    "devops", "sre", "site reliability", "platform engineer", "infrastructure engineer",
    "cloud engineer", "systems engineer", "embedded engineer", "firmware engineer",
    "hardware engineer", "network engineer", "security engineer", "cybersecurity",
    "robotics engineer", "computer scientist", "solutions architect", "technical lead",
    "staff engineer", "principal engineer", "distinguished engineer",
    # Data / AI / ML
    "data scientist", "data engineer", "data analyst", "ml engineer",
    "machine learning engineer", "machine learning", "deep learning",
    "ai engineer", "artificial intelligence", "nlp engineer", "computer vision",
    "llm", "generative ai", "research scientist", "applied scientist",
    "quantitative researcher", "quantitative analyst", "quantitative developer",
    # Product
    "product manager", "product management", "technical product",
    "technical program manager", "tpm",
    # Quant / Finance Tech
    "quant", "algorithmic trader", "algo trader", "hft", "high frequency",
    "fintech", "blockchain developer", "crypto engineer",
    # Leadership (tech-specific)
    "cto", "cio", "chief technology", "chief data", "chief information",
    "vp engineering", "vp of engineering", "vp software",
    "director of engineering", "director of software", "director of data",
    "head of engineering", "head of data", "head of product",
    "engineering manager", "vp product", "director of product",
]

_TECH_COMPANIES = [
    # Big Tech
    "google", "alphabet", "microsoft", "amazon", "aws", "apple", "meta", "facebook",
    "netflix", "nvidia", "salesforce", "oracle", "ibm", "intel", "qualcomm", "amd",
    "twitter", "x.com", "linkedin", "adobe", "sap", "workday", "servicenow",
    # AI / ML Companies
    "openai", "anthropic", "deepmind", "cohere", "mistral", "hugging face", "scale ai",
    "stability ai", "inflection", "xai", "perplexity", "together ai",
    # Startups / Unicorns
    "stripe", "palantir", "snowflake", "databricks", "airbnb", "uber", "lyft",
    "doordash", "instacart", "robinhood", "coinbase", "plaid", "twilio", "datadog",
    "splunk", "cloudflare", "hashicorp", "confluent", "mongodb", "elastic",
    "github", "gitlab", "atlassian", "figma", "notion", "slack", "zoom",
    "dropbox", "box", "hubspot", "zendesk", "pagerduty", "okta", "crowdstrike",
    "tanium", "veeva", "twitch", "bytedance", "tiktok", "ramp", "brex", "gusto",
    "rippling", "airtable", "retool", "vercel", "supabase",
    # Hardware / Semiconductors
    "samsung", "sony", "cisco", "vmware", "broadcom", "dell", "hp", "hpe",
    "arm", "tsmc", "asml",
    # Quant / HFT
    "bloomberg", "two sigma", "jane street", "d.e. shaw", "citadel", "jump trading",
    "point72", "renaissance", "virtu", "iex", "tower research", "hudson river",
    "optiver", "akuna", "sig", "susquehanna", "drw", "five rings",
]

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


def _is_tech(role: str, company: str) -> bool:
    """Return True only if the contact has a clear tech role title."""
    role_lower = role.lower()
    return any(kw in role_lower for kw in _TECH_TITLE_KEYWORDS)


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
# Coveo REST API
# ---------------------------------------------------------------------------

# Coveo aq expression: filter to contacts with a tech-related job title
_TECH_AQ = (
    "(@sfjob_title__c=\"software\" OR @sfjob_title__c=\"engineer\" OR "
    "@sfjob_title__c=\"developer\" OR @sfjob_title__c=\"data scientist\" OR "
    "@sfjob_title__c=\"data engineer\" OR @sfjob_title__c=\"machine learning\" OR "
    "@sfjob_title__c=\"scientist\" OR @sfjob_title__c=\"product manager\" OR "
    "@sfjob_title__c=\"devops\" OR @sfjob_title__c=\"platform engineer\" OR "
    "@sfjob_title__c=\"architect\" OR @sfjob_title__c=\"quantitative\" OR "
    "@sfjob_title__c=\"quant\" OR @sfjob_title__c=\"cloud engineer\" OR "
    "@sfjob_title__c=\"security engineer\" OR @sfjob_title__c=\"systems engineer\" OR "
    "@sfjob_title__c=\"infrastructure\" OR @sfjob_title__c=\"backend\" OR "
    "@sfjob_title__c=\"frontend\" OR @sfjob_title__c=\"mobile engineer\" OR "
    "@sfjob_title__c=\"deep learning\" OR @sfjob_title__c=\"nlp\" OR "
    "@sfjob_title__c=\"robotics\" OR @sfjob_title__c=\"embedded\" OR "
    "@sfjob_title__c=\"blockchain\" OR @sfjob_title__c=\"cto\" OR "
    "@sfjob_title__c=\"engineering manager\" OR @sfjob_title__c=\"sre\")"
)

# Coveo aq expression for the Computer/IT industry facet (discovered via browser capture)
_INDUSTRY_AQ = '@sfaffiliations__rfield_of_work__c=="Computer/Information Technology"'


def _build_aq(browser_aq: str | None = None) -> str:
    """Return the aq to use: industry filter is the primary filter."""
    if browser_aq and browser_aq != _INDUSTRY_AQ:
        # Browser gave us something different (e.g. different filter) — use it alone
        return browser_aq
    return _INDUSTRY_AQ

def _coveo_payload(offset: int = 0, rows: int = 100, aq: str = _TECH_AQ) -> dict:
    return {
        "q": "",
        "aq": aq,
        "firstResult": offset,
        "numberOfResults": rows,
        "sortCriteria": "relevancy",
        "fieldsToInclude": [
            "sfid",
            "sfname",
            "sffirstname",
            "sflastname",
            "sfemployer_name__c",
            "sfjob_title__c",
            "sfeducation__rschool__c",
            "sfeducation__rdegree_year__c",
            "sflinkedin_profile_link__c",
            "sfcontactuni__c",
            "clickableUri",
            "uri",
        ],
        "pipeline": "Alumni Community",
    }


def _discover_industry_aq(headers: dict) -> str | None:
    """
    Discover the Coveo field name for the Industry facet by inspecting raw result
    fields, then probe candidate aq expressions.
    """
    # Step 1: Fetch one result with ALL fields to find industry-related fields.
    try:
        resp = requests.post(
            COVEO_ENDPOINT,
            headers=headers,
            json={"q": "", "aq": "", "numberOfResults": 1, "pipeline": "Alumni Community"},
            timeout=15,
        )
        if resp.ok:
            results = resp.json().get("results", [])
            if results:
                raw_fields = results[0].get("raw", {})
                for key, val in raw_fields.items():
                    if "industr" in key.lower():
                        print(f"[columbia] Industry-like field: {key!r} = {val!r}")
                    elif isinstance(val, str) and ("technology" in val.lower() or "computer" in val.lower()):
                        print(f"[columbia] Tech-value field: {key!r} = {val!r}")
    except Exception as e:
        print(f"[columbia] Field discovery probe failed: {e}")

    # Step 2: Try facet request to see if an 'Industry' facet field exists
    try:
        resp = requests.post(
            COVEO_ENDPOINT,
            headers=headers,
            json={
                "q": "", "aq": "", "numberOfResults": 0,
                "pipeline": "Alumni Community",
                "facets": [
                    {"facetId": "Industry", "field": "Industry", "type": "specific", "numberOfValues": 10},
                    {"facetId": "sfIndustry", "field": "sfIndustry", "type": "specific", "numberOfValues": 10},
                    {"facetId": "sfindustry__c", "field": "sfindustry__c", "type": "specific", "numberOfValues": 10},
                ],
            },
            timeout=15,
        )
        if resp.ok:
            facet_results = resp.json().get("facets", [])
            for facet in facet_results:
                vals = [v.get("value") for v in facet.get("values", [])]
                if vals:
                    print(f"[columbia] Facet {facet.get('facetId')!r} has values: {vals}")
        else:
            print(f"[columbia] Facet probe HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[columbia] Facet probe failed: {e}")

    # Step 3: Try candidate aq expressions
    candidates = [
        '@Industry=="Computer/Information Technology"',
        '@sfIndustry=="Computer/Information Technology"',
        '@sfindustry=="Computer/Information Technology"',
        '@sfcontactindustry__c=="Computer/Information Technology"',
        '@sfindustry__c=="Computer/Information Technology"',
        '@sfcontact_industry__c=="Computer/Information Technology"',
    ]
    for candidate in candidates:
        try:
            resp = requests.post(
                COVEO_ENDPOINT,
                headers=headers,
                json={"q": "", "aq": candidate, "numberOfResults": 1, "pipeline": "Alumni Community"},
                timeout=15,
            )
            if resp.ok:
                total = resp.json().get("totalCount", 0)
                if total > 0:
                    print(f"[columbia] Industry filter found: {candidate} ({total:,} results)")
                    return candidate
                else:
                    print(f"[columbia] Probe: {candidate!r} → 0 results")
            else:
                print(f"[columbia] Probe HTTP {resp.status_code}: {candidate!r}")
        except Exception as e:
            print(f"[columbia] Probe error for {candidate!r}: {e}")
    print("[columbia] Could not find industry field — using title filter only")
    return None


def bulk_fetch(token: str, max_results: int = DEFAULT_MAX_RESULTS, browser_aq: str = None) -> list:
    """
    Paginate through Coveo results and return a list of parsed contact dicts.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Use the hardcoded industry filter; no browser capture or probing needed
    aq = _build_aq(browser_aq)
    print(f"[columbia] Using aq filter: {aq[:80]}...")

    contacts = []
    offset = 0
    rows = 100

    while offset < max_results:
        payload = _coveo_payload(offset=offset, rows=min(rows, max_results - offset), aq=aq)
        resp = requests.post(COVEO_ENDPOINT, headers=headers, json=payload, timeout=30)

        if resp.status_code == 401:
            raise RuntimeError(
                "Coveo token expired or invalid (401). Re-run to capture a fresh token."
            )
        resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])
        total = data.get("totalCount", 0)

        if not results:
            break

        for r in results:
            parsed = _parse(r)
            if parsed:
                contacts.append(parsed)

        print(f"[columbia] Fetched {offset + len(results)}/{min(total, max_results)} alumni...")
        offset += len(results)

        if offset >= total:
            break

        # Polite delay
        time.sleep(random.uniform(0.5, 1.5))

    return contacts


def _parse(result: dict) -> dict:
    """Extract contact fields from a Coveo result."""
    raw = result.get("raw", {})

    first = (raw.get("sffirstname") or "").strip()
    last = (raw.get("sflastname") or "").strip()

    # Require actual Salesforce person fields — rejects Trailhead/content records
    # which have sfname but no sffirstname/sflastname
    if not first or not last:
        return None

    company = (raw.get("sfemployer_name__c") or "").strip()
    role = (raw.get("sfjob_title__c") or "").strip()

    priority = _priority(role, company)
    school_raw = raw.get("sfeducation__rschool__c") or ""
    school = (school_raw[0] if isinstance(school_raw, list) else school_raw).strip()
    year_raw = raw.get("sfeducation__rdegree_year__c") or ""
    grad_year = str(year_raw[0] if isinstance(year_raw, list) else year_raw).strip()
    linkedin_url = (raw.get("sflinkedin_profile_link__c") or "").strip()
    sf_id = (raw.get("sfid") or "").strip()

    # Build placeholder email (will be enriched later or via Apollo)
    if sf_id:
        placeholder_email = f"{sf_id}@columbia-alumni-pending.local"
    else:
        placeholder_email = f"{first.lower()}.{last.lower()}@columbia-alumni-pending.local"

    # Build profile path — prefer sfid (always available), fall back to clickUri
    profile_path = f"/s/profile/{sf_id}" if sf_id else ""
    if not profile_path:
        click_uri = result.get("clickUri", "") or result.get("uri", "")
        if click_uri:
            m = re.search(r"/s/profile/[^?#\s]+", click_uri)
            if m:
                profile_path = m.group(0)

    return {
        "first_name": first,
        "last_name": last,
        "email": placeholder_email,
        "company": company,
        "role": role,
        "linkedin_url": linkedin_url,
        "portal_profile_url": profile_path,
        "columbia_alumni": 1,
        "grad_year": grad_year,
        "source": "columbia_scraper",
        "notes": f"School: {school}" if school else "",
        "priority": priority,
        # Internal fields (stripped before DB upsert)
        "_sf_id": sf_id,
        "_profile_path": profile_path,
    }


# ---------------------------------------------------------------------------
# LinkedIn URL enrichment from profile pages (optional, slow)
# ---------------------------------------------------------------------------

async def fetch_linkedin_from_profiles(
    profile_paths: list,
    max_profiles: int = 50,
    headless: bool = True,
) -> dict:
    """
    Visit up to max_profiles Columbia alumni profile pages and extract LinkedIn URLs.
    Returns dict: profile_path -> linkedin_url.
    """
    results = {}
    PLAYWRIGHT_PROFILE.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            str(PLAYWRIGHT_PROFILE),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page: Page = await context.new_page()

        for path in profile_paths[:max_profiles]:
            url = PROFILE_BASE + path
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(random.uniform(1.5, 3.0))

                # Walk Shadow DOM to find LinkedIn links
                linkedin = await page.evaluate("""() => {
                    function findLinks(root) {
                        const links = [];
                        root.querySelectorAll('a[href*="linkedin.com/in/"]').forEach(a => links.push(a.href));
                        root.querySelectorAll('*').forEach(el => {
                            if (el.shadowRoot) links.push(...findLinks(el.shadowRoot));
                        });
                        return links;
                    }
                    return findLinks(document);
                }""")

                if linkedin:
                    results[path] = linkedin[0]
                    print(f"[columbia] LinkedIn found for {path}: {linkedin[0]}")
                else:
                    results[path] = ""

            except Exception as e:
                print(f"[columbia] Profile fetch error for {path}: {e}")
                results[path] = ""

            time.sleep(random.uniform(1, 2))

        await context.close()

    return results


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_scrape(
    max_results: int = DEFAULT_MAX_RESULTS,
    headless: bool = False,
    enrich_linkedin: bool = False,
):
    """
    Full pipeline:
    1. Capture Coveo token via browser
    2. Fetch alumni contacts via REST API
    3. Optionally enrich LinkedIn URLs by visiting profiles
    4. Import into DB
    """
    from database import init_db, upsert_contact

    init_db()

    print(f"[columbia] Starting Columbia alumni scrape (max {max_results} results)...")

    # Step 1: Get Coveo Bearer token via browser
    token = await get_coveo_token(headless=headless)

    # Step 2: Fetch contacts via API (industry filter is hardcoded)
    contacts = bulk_fetch(token, max_results=max_results)
    print(f"[columbia] Fetched {len(contacts)} alumni from Coveo API")

    # Step 3: Optional LinkedIn enrichment via profile pages
    if enrich_linkedin:
        profiles_needing_linkedin = [
            c["_profile_path"]
            for c in contacts
            if not c["linkedin_url"] and c.get("_profile_path")
        ]
        if profiles_needing_linkedin:
            print(
                f"[columbia] Enriching LinkedIn URLs for "
                f"{len(profiles_needing_linkedin)} profiles (max 50)..."
            )
            linkedin_map = await fetch_linkedin_from_profiles(
                profiles_needing_linkedin,
                max_profiles=50,
                headless=headless,
            )
            for c in contacts:
                path = c.get("_profile_path", "")
                if not c["linkedin_url"] and path in linkedin_map:
                    c["linkedin_url"] = linkedin_map[path]

    # Step 4: Import to DB (strip internal fields first)
    imported = 0
    skipped = 0
    for c in contacts:
        c.pop("_sf_id", None)
        c.pop("_profile_path", None)

        try:
            upsert_contact(c)
            imported += 1
        except Exception as e:
            print(
                f"[columbia] DB upsert error for "
                f"{c.get('first_name')} {c.get('last_name')}: {e}"
            )
            skipped += 1

    print(f"[columbia] Done! Imported: {imported}, Errors: {skipped}")
    return imported


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Columbia Alumni Scraper")
    parser.add_argument(
        "--max", type=int, default=DEFAULT_MAX_RESULTS, help="Max alumni to fetch"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run browser headlessly"
    )
    parser.add_argument(
        "--enrich-linkedin",
        action="store_true",
        help="Visit profiles to collect LinkedIn URLs",
    )
    args = parser.parse_args()

    asyncio.run(
        run_scrape(
            max_results=args.max,
            headless=args.headless,
            enrich_linkedin=args.enrich_linkedin,
        )
    )
