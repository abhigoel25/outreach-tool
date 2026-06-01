"""
database.py
SQLite database for tracking outreach contacts and sent emails.

Priority scale (higher = emailed first):
  5 = big-tech exec/staff-level Columbia alumni — top priority
  4 = Columbia alumni, or senior roles at big tech
  3 = LinkedIn connections, general tech contacts
  2 = Apollo-sourced, lower-confidence
  1 = weak signal / catch-all
"""

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "outreach.db"


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safer concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name          TEXT NOT NULL,
                last_name           TEXT,
                email               TEXT UNIQUE NOT NULL,
                company             TEXT,
                role                TEXT,
                linkedin_url        TEXT,
                portal_profile_url  TEXT,
                columbia_alumni     INTEGER DEFAULT 0,
                grad_year           TEXT,
                source              TEXT,
                status              TEXT DEFAULT 'pending',
                columbia_msg_status TEXT DEFAULT 'pending',
                linkedin_conn_status TEXT DEFAULT 'pending',
                priority            INTEGER DEFAULT 3,
                notes               TEXT,
                created_at          TEXT DEFAULT (date('now')),
                updated_at          TEXT DEFAULT (date('now'))
            );

            CREATE TABLE IF NOT EXISTS emails_sent (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id  INTEGER REFERENCES contacts(id),
                to_email    TEXT,
                subject     TEXT,
                body        TEXT,
                sent_at     TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_contacts_status   ON contacts(status);
            CREATE INDEX IF NOT EXISTS idx_contacts_priority ON contacts(priority DESC);
            CREATE INDEX IF NOT EXISTS idx_contacts_linkedin ON contacts(linkedin_url);
        """)
        # Safe migrations for existing databases (ignored if column already exists)
        for col_sql in [
            "ALTER TABLE contacts ADD COLUMN portal_profile_url   TEXT",
            "ALTER TABLE contacts ADD COLUMN columbia_msg_status  TEXT DEFAULT 'pending'",
            "ALTER TABLE contacts ADD COLUMN linkedin_conn_status TEXT DEFAULT 'pending'",
            # BUG FIX: dedicated sent-at timestamps so daily counts aren't corrupted
            # by updated_at changes from scraper upserts
            "ALTER TABLE contacts ADD COLUMN columbia_msg_sent_at  TEXT",
            "ALTER TABLE contacts ADD COLUMN linkedin_conn_sent_at TEXT",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass  # column already exists
        # Indexes for new columns — run after migrations so columns exist
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_contacts_columbia_msg  ON contacts(columbia_msg_status)",
            "CREATE INDEX IF NOT EXISTS idx_contacts_linkedin_conn ON contacts(linkedin_conn_status)",
        ]:
            try:
                conn.execute(idx_sql)
            except Exception:
                pass
        # View: unified log of everything sent across all channels
        conn.execute("""
            CREATE VIEW IF NOT EXISTS sent_log AS
            SELECT
                c.id,
                c.first_name || ' ' || c.last_name  AS name,
                c.company,
                c.role,
                'linkedin'                          AS channel,
                c.linkedin_conn_status              AS status,
                c.linkedin_conn_sent_at             AS sent_at
            FROM contacts c
            WHERE c.linkedin_conn_status IN ('sent', 'messaged', 'already_connected')
            UNION ALL
            SELECT
                c.id,
                c.first_name || ' ' || c.last_name,
                c.company,
                c.role,
                'columbia',
                c.columbia_msg_status,
                c.columbia_msg_sent_at
            FROM contacts c
            WHERE c.columbia_msg_status = 'sent'
            UNION ALL
            SELECT
                c.id,
                c.first_name || ' ' || c.last_name,
                c.company,
                c.role,
                'email',
                'sent',
                e.sent_at
            FROM emails_sent e
            JOIN contacts c ON c.id = e.contact_id
            ORDER BY sent_at DESC
        """)


def upsert_contact(c: dict) -> int:
    """Insert or update a contact. Returns the row id."""
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO contacts
                (first_name, last_name, email, company, role, linkedin_url,
                 portal_profile_url, columbia_alumni, grad_year, source, priority, notes)
            VALUES
                (:first_name, :last_name, :email, :company, :role, :linkedin_url,
                 :portal_profile_url, :columbia_alumni, :grad_year, :source, :priority, :notes)
            ON CONFLICT(email) DO UPDATE SET
                company            = COALESCE(NULLIF(excluded.company, ''), company),
                role               = COALESCE(NULLIF(excluded.role, ''), role),
                linkedin_url       = COALESCE(NULLIF(excluded.linkedin_url, ''), linkedin_url),
                portal_profile_url = COALESCE(NULLIF(excluded.portal_profile_url, ''), portal_profile_url),
                columbia_alumni    = MAX(columbia_alumni, excluded.columbia_alumni),
                grad_year          = COALESCE(NULLIF(excluded.grad_year, ''), grad_year),
                priority           = MAX(priority, excluded.priority),
                notes              = COALESCE(NULLIF(excluded.notes, ''), notes),
                updated_at         = date('now')
        """, {
            "first_name":         c.get("first_name", ""),
            "last_name":          c.get("last_name", ""),
            "email":              c["email"],
            "company":            c.get("company", ""),
            "role":               c.get("role", ""),
            "linkedin_url":       c.get("linkedin_url", ""),
            "portal_profile_url": c.get("portal_profile_url", ""),
            "columbia_alumni":    int(c.get("columbia_alumni", 0)),
            "grad_year":          c.get("grad_year", ""),
            "source":             c.get("source", "manual"),
            "priority":           int(c.get("priority", 3)),
            "notes":              c.get("notes", ""),
        })
        return cur.lastrowid


def get_pending_contacts(limit: int = 15) -> list:
    """Return contacts ready to email — real emails only, pending status, highest priority first."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE status = 'pending'
              AND email NOT LIKE '%-pending.local'
              AND email NOT LIKE '%-invalid.local'
              AND email NOT LIKE '%-skipped.local'
              AND email NOT LIKE '%-duplicate.local'
            ORDER BY priority DESC, columbia_alumni DESC, id ASC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def mark_emailed(contact_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET status='emailed', updated_at=date('now') WHERE id=?",
            (contact_id,)
        )


def mark_replied(contact_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET status='replied', updated_at=date('now') WHERE id=?",
            (contact_id,)
        )


def mark_skipped(contact_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET status='skipped', updated_at=date('now') WHERE id=?",
            (contact_id,)
        )


def log_email(contact_id: int, to_email: str, subject: str, body: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO emails_sent (contact_id, to_email, subject, body)
            VALUES (?, ?, ?, ?)
        """, (contact_id, to_email, subject, body))


def already_emailed_today(contact_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT 1 FROM emails_sent
            WHERE contact_id = ? AND date(sent_at) = date('now')
        """, (contact_id,)).fetchone()
    return row is not None


def how_many_sent_today() -> int:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as n FROM emails_sent
            WHERE date(sent_at) = date('now')
        """).fetchone()
    return row["n"] if row else 0


def get_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as n FROM contacts").fetchone()["n"]
        columbia = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE columbia_alumni=1"
        ).fetchone()["n"]

        # Email readiness breakdown
        real_emails = conn.execute("""
            SELECT COUNT(*) as n FROM contacts
            WHERE email NOT LIKE '%-pending.local'
              AND email NOT LIKE '%-invalid.local'
              AND email NOT LIKE '%-skipped.local'
              AND email NOT LIKE '%-duplicate.local'
        """).fetchone()["n"]

        placeholder = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE email LIKE '%-pending.local'"
        ).fetchone()["n"]

        ready_to_send = conn.execute("""
            SELECT COUNT(*) as n FROM contacts
            WHERE status = 'pending'
              AND email NOT LIKE '%-pending.local'
              AND email NOT LIKE '%-invalid.local'
              AND email NOT LIKE '%-skipped.local'
              AND email NOT LIKE '%-duplicate.local'
        """).fetchone()["n"]

        by_status = conn.execute(
            "SELECT status, COUNT(*) as n FROM contacts GROUP BY status"
        ).fetchall()

        sent_today = how_many_sent_today()
        total_sent = conn.execute("SELECT COUNT(*) as n FROM emails_sent").fetchone()["n"]

    stats = {
        "total_contacts":    total,
        "columbia_alumni":   columbia,
        "real_emails":       real_emails,
        "placeholder_emails": placeholder,
        "ready_to_send":     ready_to_send,
        "sent_today":        sent_today,
        "total_emails_sent": total_sent,
    }
    for row in by_status:
        stats[f"status_{row['status']}"] = row["n"]
    return stats


def list_contacts(status: str = None) -> list:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE status=? ORDER BY priority DESC, id", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM contacts ORDER BY priority DESC, id"
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Columbia portal messaging
# ---------------------------------------------------------------------------

def get_pending_columbia_contacts(limit: int = 15) -> list:
    """Contacts with a portal profile URL that haven't been messaged yet."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE columbia_alumni = 1
              AND portal_profile_url IS NOT NULL AND portal_profile_url != ''
              AND columbia_msg_status = 'pending'
            ORDER BY priority DESC, id ASC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def mark_columbia_sent(contact_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET columbia_msg_status='sent', columbia_msg_sent_at=datetime('now'), updated_at=date('now') WHERE id=?",
            (contact_id,)
        )


def mark_columbia_failed(contact_id: int, reason: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET columbia_msg_status='failed', notes=COALESCE(notes||' | ','')|| ?, updated_at=date('now') WHERE id=?",
            (f"portal_fail: {reason}" if reason else "portal_fail", contact_id)
        )


def how_many_columbia_sent_today() -> int:
    """Count Columbia portal messages sent today using dedicated sent-at timestamp."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as n FROM contacts
            WHERE columbia_msg_status = 'sent'
              AND date(columbia_msg_sent_at) = date('now')
        """).fetchone()
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# LinkedIn connection requests
# ---------------------------------------------------------------------------

def get_pending_linkedin_contacts(limit: int = 15) -> list:
    """Contacts with a LinkedIn URL that haven't had a connection request sent yet."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM contacts
            WHERE linkedin_url IS NOT NULL AND linkedin_url != ''
              AND linkedin_conn_status = 'pending'
            ORDER BY priority DESC, columbia_alumni DESC, id ASC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def mark_linkedin_sent(contact_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET linkedin_conn_status='sent', linkedin_conn_sent_at=datetime('now'), updated_at=date('now') WHERE id=?",
            (contact_id,)
        )


def mark_linkedin_failed(contact_id: int, reason: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET linkedin_conn_status='failed', notes=COALESCE(notes||' | ','')|| ?, updated_at=date('now') WHERE id=?",
            (f"linkedin_fail: {reason}" if reason else "linkedin_fail", contact_id)
        )


def mark_linkedin_messaged(contact_id: int):
    """Mark a contact as reached via LinkedIn message/InMail (fallback when Connect unavailable)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET linkedin_conn_status='messaged', linkedin_conn_sent_at=datetime('now'), updated_at=date('now') WHERE id=?",
            (contact_id,)
        )


def how_many_linkedin_sent_today() -> int:
    """Count LinkedIn outreach (connections + messages) sent today."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as n FROM contacts
            WHERE linkedin_conn_status IN ('sent', 'messaged')
              AND date(linkedin_conn_sent_at) = date('now')
        """).fetchone()
    return row["n"] if row else 0


def backfill_portal_urls() -> int:
    """Populate portal_profile_url from the sfid embedded in placeholder emails."""
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE contacts
            SET portal_profile_url = '/s/profile/' || SUBSTR(email, 1, INSTR(email, '@') - 1),
                updated_at = date('now')
            WHERE columbia_alumni = 1
              AND (portal_profile_url IS NULL OR portal_profile_url = '')
        """)
        return cur.rowcount


def enrich_email_by_linkedin(linkedin_url: str, email: str) -> bool:
    """Match an existing contact by LinkedIn URL slug and update their placeholder email."""
    slug = linkedin_url.rstrip("/").split("/in/")[-1].split("/")[0].split("?")[0].lower()
    if not slug:
        return False
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE contacts SET email=?, updated_at=date('now') "
            "WHERE lower(linkedin_url) LIKE ? AND email LIKE '%-pending.local'",
            (email, f"%/in/{slug}%")
        )
        return cur.rowcount > 0