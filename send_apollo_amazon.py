"""
send_apollo_amazon.py
Send emails to the Amazon contacts imported from the Apollo CSV.
Usage:
  python send_apollo_amazon.py --dry-run    # preview only
  python send_apollo_amazon.py              # actually send
"""

import sys
from database import init_db, get_conn, mark_emailed, log_email
from gmail_client import send_email
from email_generator import generate_email

DRY_RUN = "--dry-run" in sys.argv


def get_amazon_apollo_contacts():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, first_name, last_name, email, company, role,
                   linkedin_url, columbia_alumni, grad_year, status
            FROM contacts
            WHERE source = 'apollo'
              AND (company LIKE '%mazon%')
              AND status = 'pending'
              AND email NOT LIKE '%@%pending%'
            ORDER BY priority DESC, id ASC
        """).fetchall()
    return [dict(r) for r in rows]


def main():
    init_db()
    contacts = get_amazon_apollo_contacts()

    if not contacts:
        print("No pending Amazon Apollo contacts found.")
        return

    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Sending to {len(contacts)} Amazon contacts\n")

    for i, contact in enumerate(contacts, 1):
        try:
            subject, body = generate_email(contact)
        except Exception as e:
            print(f"[{i}] Email generation failed for {contact['first_name']}: {e}")
            continue

        print(f"[{i}/{len(contacts)}] {contact['first_name']} {contact['last_name']} <{contact['email']}>")
        print(f"  Company: {contact.get('company', 'N/A')} | Role: {contact.get('role', 'N/A')}")
        print(f"  Subject: {subject}")

        if DRY_RUN:
            print(f"  Body:\n{body}")
            print("-" * 60)
            continue

        try:
            send_email(to=contact["email"], subject=subject, body=body)
            mark_emailed(contact["id"])
            log_email(contact["id"], contact["email"], subject, body)
            print(f"  Sent!\n")
        except Exception as e:
            print(f"  Failed: {e}\n")

    print("Done.")


if __name__ == "__main__":
    main()
