"""
export_google_contacts.py
Export up to N uncontacted contacts per company to CSV for Apollo email enrichment.
"""

import csv
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "outreach.db"
OUT_PATH = Path(__file__).parent / "multi_company_contacts_for_apollo.csv"

COMPANIES = [
    ("Google",       "%Google%",       10),
    ("Microsoft",    "%Microsoft%",    10),
    ("Nvidia",       "%Nvidia%",       10),
    ("Apple",        "%Apple%",        10),
    ("Oracle",       "%Oracle%",       10),
    ("Goldman Sachs","%Goldman%",      10),
    ("Citadel",      "%Citadel%",      10),
    ("JPMorgan",     "%JPMorgan%",     10),
]

FIELDNAMES = [
    "company_group",
    "id",
    "first_name",
    "last_name",
    "company",
    "role",
    "linkedin_url",
    "portal_profile_url",
    "columbia_alumni",
    "grad_year",
    "source",
    "priority",
    "notes",
    "email",
    "status",
    "columbia_msg_status",
    "linkedin_conn_status",
]


def fetch_for_company(conn, pattern, limit):
    return conn.execute("""
        SELECT
            c.id,
            c.first_name,
            c.last_name,
            c.company,
            c.role,
            c.linkedin_url,
            c.portal_profile_url,
            c.columbia_alumni,
            c.grad_year,
            c.source,
            c.priority,
            c.notes,
            c.email,
            c.status,
            c.columbia_msg_status,
            c.linkedin_conn_status
        FROM contacts c
        WHERE c.company LIKE ?
          AND c.status != 'emailed'
          AND c.columbia_msg_status != 'sent'
          AND c.linkedin_conn_status NOT IN ('sent', 'messaged', 'already_connected')
          AND c.id NOT IN (
              SELECT contact_id FROM emails_sent WHERE contact_id IS NOT NULL
          )
        ORDER BY c.priority DESC, c.columbia_alumni DESC, c.grad_year DESC
        LIMIT ?
    """, (pattern, limit)).fetchall()


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    all_rows = []
    for label, pattern, limit in COMPANIES:
        rows = fetch_for_company(conn, pattern, limit)
        print(f"  {label:<15} {len(rows):>3} contacts")
        for r in rows:
            row = {k: r[k] for k in FIELDNAMES if k != "company_group"}
            row["company_group"] = label
            all_rows.append(row)

    conn.close()

    print(f"\nTotal: {len(all_rows)} contacts across {len(COMPANIES)} companies")

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"CSV written to: {OUT_PATH}")


if __name__ == "__main__":
    main()
