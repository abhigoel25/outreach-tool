"""
email_generator.py
Generate personalized outreach emails using Claude (Haiku).
Falls back to hand-written templates if no API key is set.
"""

import os
import random
import logging
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
log = logging.getLogger(__name__)
_CLAUDE_DISABLED_REASON = ""


def _summarize_claude_error(exc: Exception) -> str:
    """Return the useful Anthropic error detail without exposing credentials."""
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    body = getattr(exc, "body", None)

    err_type = ""
    message = getattr(exc, "message", "") or str(exc)
    if isinstance(body, dict):
        err = body.get("error") or {}
        err_type = err.get("type") or ""
        message = err.get("message") or body.get("message") or message

    parts = []
    if status:
        parts.append(f"status={status}")
    if err_type:
        parts.append(f"type={err_type}")
    if message:
        parts.append(message)
    if request_id:
        parts.append(f"request_id={request_id}")
    return " | ".join(parts) or str(exc)


def _should_disable_claude_for_run(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    message = (getattr(exc, "message", "") or str(exc)).lower()
    if isinstance(body, dict):
        err = body.get("error") or {}
        message = f"{message} {err.get('type', '')} {err.get('message', '')}".lower()
    return (
        status in {400, 401, 402, 403}
        or "credit balance" in message
        or "billing" in message
        or "api key" in message
        or "permission" in message
    )


def _record_claude_failure(exc: Exception, context: str) -> None:
    global _CLAUDE_DISABLED_REASON
    summary = _summarize_claude_error(exc)
    if _should_disable_claude_for_run(exc):
        _CLAUDE_DISABLED_REASON = summary
        log.warning("[email_gen] Claude disabled for this run while generating %s; using templates. %s", context, summary)
    else:
        log.warning("[email_gen] Claude error while generating %s; using template. %s", context, summary)

# Rich background context injected into every Claude prompt
_ABHI_CONTEXT = """
Sender: Abhi Goel, CS + Operations Research minor, Columbia University School of Engineering and Applied Science (SEAS), Class of 2028
Background (use as a POOL to pick from — never dump more than 1 relevant detail):
- Internship experience in software engineering, Machine Learning, and AI at tech companies
- Research experience in Machine Learning and AI at Columbia
- Interested in software engineering, Machine Learning/AI, data science, quantitative work, product, backend systems, devops, full stack
- GPA: 4.0
- Email: abhinavgoel225@gmail.com
- Portfolio: https://abhigoel25.github.io/

Do NOT mention (unless it specifically applies to the recipient's field or background):
- Professor names or research supervisor names
- Specific company names from Abhi's internship background
- Conference names (KDD, NeurIPS, etc.)
- Lab names
""".strip()

_COLUMBIA_TEMPLATES = [
    (
        "Fellow Lion reaching out - {company}",
        (
            "Hi {first_name},\n\n"
            "I came across your profile and saw you're a fellow Lion now at {company} as {role}. "
            "I'm a CS student at Columbia Engineering (SEAS, Class of '28) and I'd love to hear about your path and what your work at {company} is like. "
            "Would you be open to a quick 15-20 minute chat sometime?\n\n"
            "Thanks,\nhttps://abhigoel25.github.io/abhiportfolio/ | https://www.linkedin.com/in/abhinav-goel-041ba8266/\nAbhi Goel\nColumbia Engineering (SEAS) '28\nabhinavgoel225@gmail.com"
        ),
    ),
    (
        "Columbia CS student - curious about your work at {company}",
        (
            "Hi {first_name},\n\n"
            "I'm a CS student at Columbia Engineering (SEAS, Class of '28) and found your profile while looking at what alumni are building in tech. "
            "Your work as {role} at {company} sounds interesting and I'd love to learn more about it. "
            "Would you have 15-20 minutes for a quick chat?\n\n"
            "Best,\nhttps://abhigoel25.github.io/abhiportfolio/ | https://www.linkedin.com/in/abhinav-goel-041ba8266/\nAbhi Goel\nColumbia SEAS '28 | abhinavgoel225@gmail.com"
        ),
    ),
    (
        "Quick hello from a fellow Lion",
        (
            "Hi {first_name},\n\n"
            "I'm Abhi, a CS student at Columbia Engineering (SEAS, Class of '28). "
            "I've been exploring what alumni are doing in tech and your role at {company} caught my attention. "
            "I'd love to hear more about your experience there. Would you be open to a short 15-minute chat?\n\n"
            "Appreciate it,\nhttps://abhigoel25.github.io/abhiportfolio/ | https://www.linkedin.com/in/abhinav-goel-041ba8266/\nAbhi Goel\nabhinavgoel225@gmail.com"
        ),
    ),
]

_GENERAL_TEMPLATES = [
    (
        "Columbia CS student - interested in your work at {company}",
        (
            "Hi {first_name},\n\n"
            "My name is Abhi, a CS student at Columbia Engineering (SEAS, Class of '28). I came across your profile and was "
            "genuinely interested in your work as {role} at {company}. "
            "Would you have 15-20 minutes for a casual chat about your path and what you're working on?\n\n"
            "Thanks,\nhttps://abhigoel25.github.io/abhiportfolio/ | https://www.linkedin.com/in/abhinav-goel-041ba8266/\nAbhi Goel\nColumbia Engineering (SEAS) '28\nabhinavgoel225@gmail.com"
        ),
    ),
    (
        "Quick ask from a Columbia CS student",
        (
            "Hi {first_name},\n\n"
            "I'm Abhi, a CS student at Columbia Engineering (SEAS). I found your profile and your work at {company} stood out to me. "
            "I'd love to hear about your experience and what you're building there. "
            "Would you be open to a quick 15-minute call?\n\n"
            "Really appreciate it,\nhttps://abhigoel25.github.io/abhiportfolio/ | https://www.linkedin.com/in/abhinav-goel-041ba8266/\nAbhi Goel\nabhinavgoel225@gmail.com"
        ),
    ),
    (
        "Reaching out - {company}",
        (
            "Hi {first_name},\n\n"
            "My name is Abhi Goel, a CS student at Columbia Engineering (SEAS). "
            "I came across your profile and was curious about your path to {role} at {company}. "
            "Would you be up for a 15-20 minute chat sometime?\n\n"
            "Thanks so much,\nhttps://abhigoel25.github.io/abhiportfolio/ | https://www.linkedin.com/in/abhinav-goel-041ba8266/\nAbhi\nabhinavgoel225@gmail.com"
        ),
    ),
]


def _strip_em_dashes(text: str) -> str:
    """Replace any em dashes that slip through with a comma. Belt-and-suspenders safety net."""
    return text.replace("—", ",").replace(" -- ", ", ")


def generate_email(contact: dict) -> tuple:
    """
    Generate (subject, body) for a contact.
    Uses Claude Haiku if API key available, otherwise falls back to templates.
    """
    if ANTHROPIC_API_KEY and not _CLAUDE_DISABLED_REASON:
        try:
            subject, body = _generate_with_claude(contact)
            # Guard: if Claude returned a meta-prompt instead of a real subject, fall back
            bad_subject = (
                len(subject) > 80
                or subject.lower().startswith("i need")
                or subject.lower().startswith("could you")
                or subject.lower().startswith("please provide")
                or "?" in subject and len(subject) > 50
            )
            if bad_subject:
                log.warning("[email_gen] Claude returned bad subject, falling back to template")
                raise ValueError("bad subject")
            return _strip_em_dashes(subject), _strip_em_dashes(body)
        except Exception as e:
            _record_claude_failure(e, "email")

    subject, body = _generate_from_template(contact)
    return _strip_em_dashes(subject), _strip_em_dashes(body)


def _generate_with_claude(contact: dict) -> tuple:
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    is_columbia = bool(contact.get("columbia_alumni"))
    columbia_line = (
        "The recipient is a Columbia University alumnus. Mention the shared Columbia connection naturally."
        if is_columbia else ""
    )
    grad_year = contact.get("grad_year", "")
    grad_note = f"They graduated around {grad_year}." if grad_year else ""

    system = f"""You write very short cold outreach emails for Abhinav Goel.

{_ABHI_CONTEXT}

Rules:
- Body is exactly 3 sentences. Hard limit. Stop after sentence 3.

TWO VERSIONS depending on whether the recipient is a Columbia alum:

IF COLUMBIA ALUM:
- Sentence 1: "Hi [name], I'm Abhinav Goel, a current CS student at Columbia Engineering (SEAS, Class of 2028)." Simple intro only.
- Sentence 2: "I came across your profile and wanted to hear about your path from Columbia to [their role] at [company]." Optionally add one brief, genuine observation about the company or their work if it flows naturally. Keep it one sentence.
- Sentence 3: Direct ask for a 15-20 min chat.

IF NOT A COLUMBIA ALUM:
- Sentence 1: "Hi [name], I'm Abhinav Goel, a CS student at Columbia Engineering (SEAS, Class of 2028)" + one short interest phrase for their role:
    SWE/backend/infra/platform/DevOps: "with a strong interest in software engineering"
    ML/AI/research/data science: "with a strong interest in ML and AI"
    Quant/HFT/trading: "with a strong interest in quantitative methods and ML"
    PM/product: "with a strong interest in software engineering and building products"
    Director/VP/exec/CTO/leadership: "with a background in software engineering and ML"
    General: "with a strong interest in software engineering and ML"
- Sentence 2: One plain sentence about their journey to [role] at [company]. Optionally add a brief genuine observation about the company if it flows naturally.
- Sentence 3: Direct ask for a 15-20 min chat.

- No em dashes. No "pick your brain", "leverage", "synergy", "I'm impressed".
- Sign off (use this exact format, each item on its own line):
https://abhigoel25.github.io/abhiportfolio/ | https://www.linkedin.com/in/abhinav-goel-041ba8266/
Abhinav Goel
Columbia Engineering (SEAS) '28
abhinavgoel225@gmail.com
513-374-0503
- The 3 sentences must be in a single paragraph with no line breaks between them.
- Output: subject line, blank line, body paragraph (3 sentences, no internal line breaks), blank line, sign off. Nothing else."""

    user = (
        f"Write a cold outreach email to {contact['first_name']} {contact.get('last_name', '')}.\n"
        f"Their role: {contact.get('role', 'unknown role')}\n"
        f"Their company: {contact.get('company', 'unknown company')}\n"
        f"{columbia_line} {grad_note}"
    ).strip()

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": user}],
        system=system,
    )

    import re
    text = response.content[0].text.strip()
    # Fix missing space after comma (only commas — periods appear in email/URLs)
    text = re.sub(r",([A-Za-z])", r", \1", text)
    lines = text.split("\n", 2)

    if len(lines) >= 3:
        subject = lines[0].replace("Subject:", "").strip()
        body = lines[2].strip()
    elif len(lines) == 2:
        subject = lines[0].strip()
        body = lines[1].strip()
    else:
        subject = lines[0].strip()
        body = text

    return subject, body


def _generate_from_template(contact: dict) -> tuple:
    is_columbia = bool(contact.get("columbia_alumni"))
    pool = _COLUMBIA_TEMPLATES if is_columbia else _GENERAL_TEMPLATES

    subject_tmpl, body_tmpl = random.choice(pool)

    ctx = {
        "first_name": contact.get("first_name", "there"),
        "company":    contact.get("company", "your company"),
        "role":       contact.get("role", "your field"),
    }

    subject = subject_tmpl.format(**ctx)
    body = body_tmpl.format(**ctx)
    return subject, body


# ---------------------------------------------------------------------------
# LinkedIn connection note (300-char hard cap)
# ---------------------------------------------------------------------------

_LINKEDIN_NOTE_TEMPLATES = [
    "Hi {first_name}, I'm Abhinav, a CS student at Columbia Engineering (SEAS, '28) into SWE and ML. I've been really interested in {company} and would love to grab a 15 min chat with you sometime. Thanks!",
    "Hi {first_name}! I'm Abhinav, a CS student at Columbia SEAS ('28). I came across your profile and would love to hear about your experience at {company}. Would you be up for a quick 15 min chat?",
    "Hi {first_name}, I'm Abhinav, studying CS at Columbia Engineering ('28). I've been following what {company} is building and your work caught my eye. Would love a quick 10-15 min chat if you're open to it!",
]

_COLUMBIA_NOTE_TEMPLATES = [
    "Hi {first_name}, I'm Abhinav, a CS student at Columbia Engineering (SEAS, Class of 2028). I saw you went to Columbia as well — curious about your path to {company}. Would you be up for a quick chat?",
    "Hey {first_name}, I'm Abhinav, a CS student at Columbia SEAS (Class of 2028). Noticed you went to Columbia too, and wanted to reach out about your experience at {company}. Would love to chat for a few mins if you have time.",
]


def generate_linkedin_note(contact: dict) -> str:
    """
    Generate a LinkedIn connection request note (<= 300 chars).
    Uses Claude if available (for better personalization), otherwise falls back to templates.
    """
    if ANTHROPIC_API_KEY and not _CLAUDE_DISABLED_REASON:
        try:
            return _generate_note_with_claude(contact)
        except Exception as e:
            _record_claude_failure(e, "LinkedIn note")

    return _generate_note_from_template(contact)


def _generate_note_with_claude(contact: dict) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    is_columbia = bool(contact.get("columbia_alumni"))
    columbia_line = "They are a Columbia University alumnus. Mention the shared Columbia connection naturally." if is_columbia else ""

    system = """You write short LinkedIn connection request notes for Abhinav Goel.

Sender: Abhinav Goel, CS student at Columbia Engineering (SEAS, Class of 2028). Interested in SWE and ML.

Rules:
- Target 220-245 characters for the body text. The sign-off "\n\nAbhinav" adds ~10 characters, keeping the total well under 300. Do NOT exceed 250 characters of body text.
- 2-3 sentences.
- Tone: genuine, casual college-student voice. Not corporate. Not AI-sounding.
- Never mention "junior", "sophomore", "freshman", or any class year label. Just say "CS student at Columbia Engineering (SEAS, Class of 2028)" — always use the full form, never shorten to just "Columbia".
- The goal is eventually getting a 10-15 min chat and a referral. Don't say "referral". End with asking for a quick chat.
- Never say: "pick your brain", "leverage", "synergy", "would love to connect", "looking forward to connecting", "hope to connect", "always cool to see", "cool to see", "doing interesting work", "doing great work", "doing big things", "Lions doing", "love seeing another Columbia", "great to see a fellow", "awesome to see another".
- No em dashes. No exclamation points after every sentence.
- If Columbia alum: mention the shared Columbia connection simply and matter-of-factly, e.g. "I saw you went to Columbia as well" or "noticed you went to Columbia too". Do NOT say anything like "love seeing another Columbia person" or compliment them on their work as part of the Columbia mention.
- Sign off: just "Abhinav". No last name, no email.
- Output: just the note. No labels, no quotes."""

    user = (
        f"Write a LinkedIn connection note to {contact['first_name']} {contact.get('last_name', '')}.\n"
        f"Their role: {contact.get('role', 'unknown role')}\n"
        f"Their company: {contact.get('company', 'unknown company')}\n"
        f"{columbia_line}"
    ).strip()

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": user}],
        system=system,
    )

    note = response.content[0].text.strip()
    note = note.replace("—", ",").replace(" -- ", ", ")
    import re
    note = re.sub(r"\s+,", ",", note)          # remove space(s) before comma
    note = re.sub(r",([A-Za-z])", r", \1", note)  # add space after comma if missing
    # Truncate at word boundary so we never cut mid-word or lose the sign-off
    if len(note) > 300:
        note = note[:297].rsplit(" ", 1)[0] + "..."
    return note


def _generate_note_from_template(contact: dict) -> str:
    is_columbia = bool(contact.get("columbia_alumni"))
    pool = _COLUMBIA_NOTE_TEMPLATES if is_columbia else _LINKEDIN_NOTE_TEMPLATES

    tmpl = random.choice(pool)
    note = tmpl.format(
        first_name=contact.get("first_name", "there"),
        company=contact.get("company", "your company"),
    )
    return note[:300]
