"""
linkedin_connector.py
Send LinkedIn connection requests with a personalized 300-character note.

Visits each contact's LinkedIn profile URL, clicks Connect, adds a note,
and sends. Keeps a conservative daily rate (10-15/day) to avoid triggering
LinkedIn's anti-automation systems.

Usage via main.py:
  python main.py send linkedin              # send to up to LINKEDIN_DAILY_LIMIT contacts
  python main.py send linkedin --limit 10   # cap at 10
  python main.py send linkedin --dry-run    # preview without sending

Standalone:
  python linkedin_connector.py --limit 5 --dry-run
"""

import asyncio
import logging
import os
import random
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext, Page, TimeoutError as PwTimeout, Error as PwError

load_dotenv()

log = logging.getLogger(__name__)

PLAYWRIGHT_PROFILE = Path(__file__).parent / ".playwright_profile" / "linkedin"
# Keep this low — LinkedIn restricts accounts that send too many requests/day
DAILY_LIMIT = int(os.getenv("LINKEDIN_DAILY_LIMIT", "12"))
LINKEDIN_EMAIL    = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

async def _ensure_logged_in(page: Page) -> bool:
    """Navigate to LinkedIn home; log in if the session has expired."""
    try:
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)
    except PwTimeout:
        pass
    except PwError as e:
        log.info(f"[linkedin] Network error during login check: {e}")
        return False

    current = page.url
    if "linkedin.com/login" in current or "linkedin.com/uas/" in current or "linkedin.com/checkpoint" in current or "linkedin.com/authwall" in current:
        log.info("[linkedin] Session expired — logging in...")
        try:
            # Go directly to the login page if not already there
            if "linkedin.com/login" not in page.url:
                await page.goto("https://www.linkedin.com/login", wait_until="networkidle", timeout=30_000)
                await asyncio.sleep(2)

            # Use JS to fill the form — the inputs may be hidden/offscreen in React renders
            filled = await page.evaluate("""([email, password]) => {
                function setVal(selector, val) {
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    const nativeInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                    nativeInput.set.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
                const emailOk = setVal('#username, input[autocomplete="username"], input[type="email"]', email);
                const pwOk    = setVal('#password, input[autocomplete="current-password"], input[type="password"]', password);
                return {emailOk, pwOk};
            }""", [LINKEDIN_EMAIL, LINKEDIN_PASSWORD])
            log.info(f"[linkedin] Form fill result: {filled}")

            # Click the submit button via JS to bypass visibility check on SPA renders
            await page.evaluate("""() => {
                const btn = document.querySelector('button[type="submit"]') ||
                    [...document.querySelectorAll('button')].find(b =>
                        (b.textContent || '').trim().toLowerCase().includes('sign in')
                    );
                if (btn) btn.click();
            }""")
            await asyncio.sleep(4)

            # Handle any security checkpoint (email verification, CAPTCHA, etc.)
            if "checkpoint" in page.url or "challenge" in page.url or "login" in page.url:
                log.info("[linkedin] Security checkpoint or verify step detected — complete it in the browser, then press Enter...")
                await asyncio.get_event_loop().run_in_executor(None, input, "")

            await page.wait_for_url("**/linkedin.com/feed/**", timeout=60_000)
            log.info("[linkedin] Logged in successfully.")
        except Exception as e:
            log.info(f"[linkedin] Login failed: {e}")
            return False

    return True


# ---------------------------------------------------------------------------
# Core: send one connection request
# ---------------------------------------------------------------------------

async def _get_profile_name(page: Page) -> str:
    """Extract the profile owner's name from a loaded LinkedIn profile."""
    try:
        h1 = page.locator("main h1, h1").first
        await h1.wait_for(state="visible", timeout=8_000)
        text = (await h1.inner_text()).strip()
        if text and text.lower() != "linkedin":
            return text
    except Exception:
        pass

    try:
        return await page.evaluate("""() => {
            const candidates = [
                document.querySelector('main h1'),
                document.querySelector('h1'),
                document.querySelector('.text-heading-xlarge'),
            ].filter(Boolean);
            for (const el of candidates) {
                const text = (el.textContent || '').trim();
                if (text && text.toLowerCase() !== 'linkedin') return text;
            }
            const title = document.title.split('|')[0].trim();
            const name = title.split(' - ')[0].trim();
            return name && name.toLowerCase() !== 'linkedin' ? name : '';
        }""")
    except Exception:
        return ""


async def send_connection_request(
    page: Page,
    profile_url: str,
    note: str,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Navigate to a LinkedIn profile and send a connection request with a note.
    Returns (success: bool, reason: str).
    Note must be <= 300 characters.
    """
    note = note[:300]  # Hard cap — LinkedIn rejects longer notes

    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(2.5, 4.0))
    except PwTimeout:
        return False, "timeout_loading_profile"

    if dry_run:
        return True, "dry_run"

    # --- Check for "pending" invite ---
    try:
        page_text = await page.inner_text("body")
        if "Pending" in page_text:
            return False, "already_pending"
    except Exception:
        pass

    # --- Find the Connect button ---
    # Scroll to top and wait for sticky mini-header to collapse
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(1.2)

    person_name = await _get_profile_name(page)
    log.info(f"    [linkedin] Profile name: {person_name!r}")
    if not person_name:
        return False, "profile_not_loaded"
    first_name = (person_name or '').split()[0].lower() if person_name else ''

    connect_clicked = False

    # --- Step 1: Direct Connect button ---
    # aria-label is "Invite [Name] to connect" — verify first name to exclude sidebar buttons
    try:
        btn = page.locator("button[aria-label*='Invite'][aria-label*='connect' i]").first
        if await btn.count() > 0:
            lbl = (await btn.get_attribute("aria-label") or '').lower()
            if not first_name or first_name in lbl:
                await btn.click()
                connect_clicked = True
                log.info(f"    [linkedin] Direct Connect clicked ({lbl!r})")
    except Exception:
        pass

    # --- Step 2: More dropdown ---
    # Find the visible More button that appears right after the Follow button for this person
    # in DOM order. This avoids: (a) the nav More which comes before Follow buttons,
    # (b) hidden aria-label='More' buttons, (c) sidebar More buttons.
    if not connect_clicked:
        marked = await page.evaluate("""(name) => {
            if (!name) return false;
            const allBtns = [...document.querySelectorAll('button')];
            // Find the first Follow button for this specific person
            const followIdx = allBtns.findIndex(b =>
                (b.getAttribute('aria-label') || '').toLowerCase() === `follow ${name.toLowerCase()}`
            );
            if (followIdx === -1) return false;
            // Scan the next 15 buttons in DOM order for a visible More button
            for (let i = followIdx + 1; i < Math.min(followIdx + 15, allBtns.length); i++) {
                const b = allBtns[i];
                const txt = (b.innerText || b.textContent || '').trim();
                const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
                const st = getComputedStyle(b);
                const rc = b.getBoundingClientRect();
                const visible = rc.width > 0 && rc.height > 0 &&
                                st.display !== 'none' && st.visibility !== 'hidden' &&
                                parseFloat(st.opacity || '1') > 0;
                if (visible && (txt === 'More' || lbl === 'more' || lbl.includes('more action'))) {
                    b.setAttribute('data-pw-click', 'more');
                    return true;
                }
            }
            return false;
        }""", person_name)

        if marked:
            try:
                # Use dispatchEvent (mousedown+mouseup+click) for React-compatible dropdown open
                await page.evaluate("""() => {
                    const btn = document.querySelector("[data-pw-click='more']");
                    if (!btn) return;
                    btn.focus();
                    ['mousedown','mouseup','click'].forEach(t =>
                        btn.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window}))
                    );
                }""")
                log.info(f"    [linkedin] Clicked More (DOM adjacency + dispatchEvent)")
                await asyncio.sleep(1.0)

                for conn_sel in [
                    "[role='menu'] :text-is('Connect')",
                    "[role='listbox'] :text-is('Connect')",
                    ".artdeco-dropdown__content :text-is('Connect')",
                    "ul[role='menu'] li:has-text('Connect')",
                ]:
                    try:
                        conn = page.locator(conn_sel).first
                        await conn.wait_for(state="visible", timeout=2_000)
                        await conn.click()
                        connect_clicked = True
                        log.info(f"    [linkedin] Connect clicked from More dropdown ({conn_sel})")
                        break
                    except Exception:
                        pass

                if not connect_clicked:
                    # Fallback: try Playwright force-click the marked button
                    try:
                        more_btn = page.locator("button[data-pw-click='more']").first
                        await more_btn.click(force=True)
                        await asyncio.sleep(1.0)
                        for conn_sel in [
                            "[role='menu'] :text-is('Connect')",
                            "[role='listbox'] :text-is('Connect')",
                        ]:
                            try:
                                conn = page.locator(conn_sel).first
                                await conn.wait_for(state="visible", timeout=2_000)
                                await conn.click()
                                connect_clicked = True
                                break
                            except Exception:
                                pass
                    except Exception:
                        pass

                await page.evaluate("document.querySelectorAll('[data-pw-click]').forEach(e => e.removeAttribute('data-pw-click'))")

                if not connect_clicked:
                    await page.keyboard.press("Escape")
            except Exception as e:
                log.info(f"    [linkedin] More step error: {e}")

    if not connect_clicked:
        # already_connected: only if a Message BUTTON (not <a> InMail link) scoped to the
        # profile action area (near the Follow button for this person)
        try:
            is_connected = await page.evaluate("""(name) => {
                if (!name) return false;
                // Find Follow button for this person (case-insensitive)
                const followBtn = [...document.querySelectorAll('button')].find(b => {
                    const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
                    return lbl === `follow ${name.toLowerCase()}`;
                });
                if (!followBtn) return false;
                // Walk up to action container (stop at 2-6 buttons — the action bar)
                let c = followBtn.parentElement;
                for (let i = 0; i < 8; i++) {
                    if (!c) return false;
                    const n = c.querySelectorAll('button').length;
                    if (n >= 2 && n <= 6) break;
                    if (n > 6) return false;
                    c = c.parentElement;
                }
                if (!c) return false;
                // Message BUTTON (not <a>) in this small container = actually connected
                return [...c.querySelectorAll('button')].some(b => {
                    const txt = (b.innerText || b.textContent || '').trim();
                    const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
                    return txt === 'Message' || lbl === 'message';
                });
            }""", person_name)
            if is_connected:
                return False, "already_connected"
        except Exception:
            pass
        return False, "connect_button_not_found"

    await asyncio.sleep(random.uniform(1.5, 2.5))

    # --- Handle "How do you know X?" step (appears for many profiles) ---
    # Detect it by the presence of relationship-category labels unique to this modal.
    # After selecting a category the modal updates to show the note form.
    try:
        rel_labels = page.locator(
            "label:has-text('Classmate'), label:has-text('Colleague'), "
            "label:has-text('Friend'), label:has-text('Other')"
        )
        await rel_labels.first.wait_for(state="visible", timeout=4_000)
        # Select "Classmate" (most appropriate for Columbia alumni outreach)
        try:
            await page.locator("label:has-text('Classmate')").first.click()
        except Exception:
            await page.locator("label:has-text('Other')").first.click()
        await asyncio.sleep(0.8)
        for next_sel in [
            "button:has-text('Next')",
            "button:has-text('Continue')",
            "button[aria-label*='Next' i]",
            "button[aria-label*='Continue' i]",
        ]:
            try:
                next_btn = page.locator(next_sel).first
                await next_btn.wait_for(state="visible", timeout=1_500)
                await next_btn.click()
                await asyncio.sleep(0.8)
                break
            except Exception:
                pass
    except Exception:
        pass  # No "How do you know?" step — modal goes straight to the note form

    # --- Wait for the note form (unique buttons that only appear in the invite modal) ---
    _NOTE_FORM_SEL = (
        "button:has-text('Send without a note'), "
        "button:has-text('Add a note'), "
        "button:has-text('Send invitation')"
    )
    modal_appeared = False
    try:
        await page.locator(_NOTE_FORM_SEL).first.wait_for(state="visible", timeout=6_000)
        modal_appeared = True
    except Exception:
        pass

    if not modal_appeared:
        await page.keyboard.press("Escape")
        return False, "modal_not_appeared"

    # --- Click "Add a note" ---
    try:
        btn = page.locator("button:has-text('Add a note')").first
        await btn.wait_for(state="visible", timeout=3_000)
        await btn.click()
        await asyncio.sleep(random.uniform(0.8, 1.2))
    except Exception:
        pass  # Textarea may already be visible

    # --- Fill the note textarea ---
    note_filled = False
    for sel in [
        "textarea[name='message']",
        "textarea[placeholder*='note' i]",
        "textarea",
        "div[role='textbox'][contenteditable='true']",
    ]:
        try:
            ta = page.locator(sel).first
            await ta.wait_for(state="visible", timeout=3_000)
            await ta.click()
            await asyncio.sleep(0.2)
            await ta.fill(note)
            note_filled = True
            break
        except Exception:
            pass

    await asyncio.sleep(0.5)

    # --- Click "Send invitation" / "Send request" ---
    # Use only invite-modal-specific text to avoid hitting Messaging "Send" button
    sent = False
    for sel in [
        "button:has-text('Send invitation')",
        "button[aria-label*='Send invitation']",
        "button:has-text('Send request')",
        "button[aria-label*='Send request']",
    ]:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=4_000)
            await btn.click()
            sent = True
            break
        except Exception:
            pass

    if not sent:
        for modal_sel in ["[role='dialog']", ".artdeco-modal"]:
            modal = page.locator(modal_sel).last
            for sel in ["button:has-text('Send')", "button[aria-label*='Send' i]"]:
                try:
                    btn = modal.locator(sel).first
                    await btn.wait_for(state="visible", timeout=2_000)
                    await btn.click()
                    sent = True
                    break
                except Exception:
                    pass
            if sent:
                break

    if not sent:
        return False, "send_button_not_found"

    await asyncio.sleep(random.uniform(1.5, 2.5))
    return True, "sent"


# ---------------------------------------------------------------------------
# Message / InMail fallback (for already-connected or no-Connect profiles)
# ---------------------------------------------------------------------------

async def _try_send_message(
    page: Page,
    profile_url: str,
    contact: dict,
    note: str,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Send a LinkedIn direct message or InMail when Connect is unavailable.
    Used as a fallback for priority >= 4 contacts.
    `note` is the already-generated connection note — reused as the message body
    to avoid a second Claude API call.
    Returns (success: bool, reason: str).
    """
    body = note[:1900]  # InMail body cap

    if dry_run:
        return True, "dry_run_message"

    # Always re-navigate: the page state after a failed Connect attempt is dirty
    # (More dropdown may be open, JS mutations applied, etc.).
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(random.uniform(2.5, 3.5))
    except PwTimeout:
        log.info("  [msg_fallback] timeout navigating to profile")
        return False, "timeout_loading_profile_for_message"

    # Scroll to top so sticky mini-header collapses (same requirement as Connect)
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(1.2)

    # Derive person name for scoping (same as connect logic)
    person_name = await _get_profile_name(page)
    first_name = (person_name or '').split()[0].lower() if person_name else ''
    log.info(f"  [msg_fallback] profile name: {person_name!r}")
    if not person_name:
        return False, "profile_not_loaded"

    # Find Message button scoped to the profile action area.
    # Strategy: look for the Follow button for this person, then find a
    # Message button in the same action container (avoids nav bar matches).
    msg_clicked = False

    msg_clicked = await page.evaluate("""(firstName) => {
        // Try to find Message button near a Follow button for this person
        const allBtns = [...document.querySelectorAll('button')];
        const followIdx = firstName
            ? allBtns.findIndex(b => {
                const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
                return lbl.startsWith('follow') && lbl.includes(firstName);
              })
            : -1;

        let msgBtn = null;

        if (followIdx !== -1) {
            // Walk up to action container
            let c = allBtns[followIdx].parentElement;
            for (let i = 0; i < 8; i++) {
                if (!c) break;
                const btns = [...c.querySelectorAll('button')];
                if (btns.length >= 2 && btns.length <= 8) {
                    msgBtn = btns.find(b => {
                        const txt = (b.innerText || b.textContent || '').trim();
                        const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
                        return txt === 'Message' || lbl === 'message' || lbl.startsWith('message ');
                    });
                    if (msgBtn) break;
                }
                c = c.parentElement;
            }
        }

        // Broader fallback: first visible Message button on the page (not a link)
        if (!msgBtn) {
            msgBtn = allBtns.find(b => {
                const txt = (b.innerText || b.textContent || '').trim();
                const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
                const rc  = b.getBoundingClientRect();
                const vis = rc.width > 0 && rc.height > 0;
                return vis && (txt === 'Message' || lbl === 'message' || lbl.startsWith('message '));
            });
        }

        if (!msgBtn) return false;
        msgBtn.click();
        return true;
    }""", first_name)

    if not msg_clicked:
        log.info("  [msg_fallback] message_button_not_found")
        return False, "message_button_not_found"

    log.info("  [msg_fallback] Message button clicked")
    await asyncio.sleep(random.uniform(1.5, 2.5))

    # Find the compose window (overlay for direct message, dialog for InMail)
    compose = None
    for cloc in ["[role='dialog']", ".msg-overlay-conversation-bubble", ".msg-form",
                 ".msg-overlay-bubble-header", "form.msg-form"]:
        loc = page.locator(cloc)
        try:
            await loc.first.wait_for(state="visible", timeout=4_000)
            compose = loc.first
            log.info(f"  [msg_fallback] compose window found ({cloc})")
            break
        except Exception:
            pass

    if compose is None:
        log.info("  [msg_fallback] compose_window_not_found")
        return False, "compose_window_not_found"

    # Fill Subject field if present (InMail only)
    try:
        subj_loc = compose.locator(
            "input[placeholder*='Subject'], input[name='subject'], input[aria-label*='Subject']"
        ).first
        await subj_loc.wait_for(state="visible", timeout=2_000)
        # Use first line of the note as subject
        subject_text = note.split('\n')[0][:200]
        await subj_loc.fill(subject_text)
        log.info(f"  [msg_fallback] subject filled")
    except Exception:
        pass  # Regular messages have no subject

    # Fill message body — LinkedIn uses a contenteditable div
    msg_filled = False
    for sel in [
        "div[role='textbox'][contenteditable='true']",
        "div[contenteditable='true']",
        "textarea",
    ]:
        try:
            ta = compose.locator(sel).first
            await ta.wait_for(state="visible", timeout=4_000)
            await ta.click()
            await asyncio.sleep(0.3)
            await page.keyboard.press("Control+a")
            await page.keyboard.type(body)
            msg_filled = True
            log.info(f"  [msg_fallback] body filled ({sel})")
            break
        except Exception:
            pass

    if not msg_filled:
        log.info("  [msg_fallback] message_body_not_filled")
        return False, "message_body_not_filled"

    await asyncio.sleep(0.5)

    # Send
    for sel in [
        "button[type='submit']:has-text('Send')",
        "button[aria-label*='Send message']",
        "button:has-text('Send')",
        "button[aria-label*='Send']",
    ]:
        try:
            btn = compose.locator(sel).first
            await btn.wait_for(state="visible", timeout=4_000)
            await btn.click()
            log.info("  [msg_fallback] Send clicked — messaged!")
            return True, "messaged"
        except Exception:
            pass

    log.info("  [msg_fallback] message_send_failed (send button not found)")
    return False, "message_send_failed"


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

async def run_batch(limit: int = None, dry_run: bool = False, headless: bool = False):
    from database import (
        init_db, get_pending_linkedin_contacts,
        mark_linkedin_sent, mark_linkedin_failed, mark_linkedin_messaged,
        how_many_linkedin_sent_today,
    )
    from email_generator import generate_linkedin_note

    init_db()

    sent_today = how_many_linkedin_sent_today()
    cap = limit if limit else DAILY_LIMIT
    remaining = cap - sent_today

    if remaining <= 0:
        log.info(f"[linkedin] Daily limit ({cap}) already reached. Come back tomorrow!")
        return

    # Fetch a buffer larger than remaining so stale skips don't eat into the quota.
    # We re-fetch in chunks; already-processed contacts are excluded automatically
    # because mark_linkedin_sent/failed update their status before the next fetch.
    FETCH_BATCH = remaining + 20
    contacts = get_pending_linkedin_contacts(limit=FETCH_BATCH)
    if not contacts:
        log.info("[linkedin] No contacts with LinkedIn URLs pending.")
        return

    log.info(f"{'[DRY RUN] ' if dry_run else ''}[linkedin] Target: {remaining} new sends "
             f"(limit: {cap}/day, sent today: {sent_today})")

    PLAYWRIGHT_PROFILE.mkdir(parents=True, exist_ok=True)

    # Track IDs we've already touched this run to avoid re-fetching them mid-loop
    seen_ids: set[int] = set()
    actual_sends = 0   # real new connection requests / messages sent this run
    i = 0              # display counter

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            str(PLAYWRIGHT_PROFILE),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page: Page = await context.new_page()

        # Ensure the session is alive before starting the batch
        if not await _ensure_logged_in(page):
            log.info("[linkedin] Could not log in — aborting batch.")
            await context.close()
            return

        while actual_sends < remaining:
            # Replenish contacts list if exhausted
            if not contacts:
                contacts = get_pending_linkedin_contacts(limit=FETCH_BATCH)
                # Filter out any we've already touched this run
                contacts = [c for c in contacts if c["id"] not in seen_ids]
                if not contacts:
                    log.info("[linkedin] No more pending contacts available.")
                    break

            contact = contacts.pop(0)
            seen_ids.add(contact["id"])
            i += 1

            name = f"{contact['first_name']} {contact.get('last_name', '')}"
            co   = contact.get("company", "?")
            url  = contact.get("linkedin_url", "")

            if not url:
                log.info(f"[{i}] {name} — no LinkedIn URL, skipping")
                mark_linkedin_failed(contact["id"], "no_url")
                continue

            note = generate_linkedin_note(contact)
            char_count = len(note)
            role = contact.get("role") or "(no role)"

            log.info(f"[{i}] {name} | {role} @ {co}  (sends: {actual_sends}/{remaining})")
            log.info(f"  Note ({char_count}/300): {note}")

            if dry_run:
                log.info("-" * 60)
                actual_sends += 1
                continue

            try:
                ok, reason = await send_connection_request(page, url, note, dry_run=False)
            except Exception as e:
                log.info(f"  FAILED: {e}")
                mark_linkedin_failed(contact["id"], str(e))
                await asyncio.sleep(5)
                continue

            original_reason = reason

            # --- Message fallback: try for priority >= 4 when Connect isn't available ---
            FALLBACK_REASONS = {"already_connected", "connect_button_not_found", "modal_not_appeared"}
            if not ok and reason in FALLBACK_REASONS:
                priority = int(contact.get("priority") or 0)
                if priority >= 4:
                    log.info(f"  [linkedin] Connect unavailable ({reason}) — trying message fallback...")
                    ok, reason = await _try_send_message(page, url, contact, note=note, dry_run=False)

            # --- Record result ---
            TRANSIENT_REASONS = {
                "timeout_loading_profile",
                "timeout_loading_profile_for_message",
                "profile_not_loaded",
                "modal_not_appeared",
                "send_button_not_found",
                "compose_window_not_found",
                "message_send_failed",
            }
            if ok:
                actual_sends += 1
                if reason == "messaged":
                    mark_linkedin_messaged(contact["id"])
                    log.info(f"  Messaged! (InMail/direct)")
                else:
                    mark_linkedin_sent(contact["id"])
                    log.info(f"  Sent!")
            elif original_reason in ("already_connected", "already_pending"):
                # Stale DB entry — mark it off but don't count against today's quota
                mark_linkedin_sent(contact["id"])
                log.info(f"  Skipped ({original_reason}) — DB cleaned up, fetching replacement")
            elif reason in TRANSIENT_REASONS or original_reason in TRANSIENT_REASONS:
                log.info(f"  Transient failure: {reason} (left pending for a future retry)")
            else:
                mark_linkedin_failed(contact["id"], reason)
                log.info(f"  Failed: {reason}")

            # Human-paced delay (8-15s) — critical for avoiding LinkedIn detection
            await asyncio.sleep(random.uniform(8.0, 15.0))

        await context.close()

    log.info(f"[linkedin] Batch complete. New sends this run: {actual_sends}/{remaining}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LinkedIn connection request sender")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--headless", action="store_true")
    a = parser.parse_args()
    asyncio.run(run_batch(limit=a.limit, dry_run=a.dry_run, headless=a.headless))
