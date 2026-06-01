"""Reset specific contacts' linkedin_conn_status back to pending."""
import sqlite3

conn = sqlite3.connect("outreach.db")
conn.row_factory = sqlite3.Row

# Show current state
rows = conn.execute(
    "SELECT id, first_name, last_name, linkedin_conn_status FROM contacts "
    "WHERE first_name LIKE '%Rohit%' OR first_name LIKE '%Yannick%'"
).fetchall()
print("Before reset:")
for r in rows:
    print(f"  id={r['id']}  name={r['first_name']} {r['last_name']}  status={r['linkedin_conn_status']}")

# Reset to pending
conn.execute(
    "UPDATE contacts SET linkedin_conn_status='pending', linkedin_conn_sent_at=NULL "
    "WHERE first_name LIKE '%Rohit%' OR first_name LIKE '%Yannick%'"
)
conn.commit()

rows = conn.execute(
    "SELECT id, first_name, last_name, linkedin_conn_status FROM contacts "
    "WHERE first_name LIKE '%Rohit%' OR first_name LIKE '%Yannick%'"
).fetchall()
print("\nAfter reset:")
for r in rows:
    print(f"  id={r['id']}  name={r['first_name']} {r['last_name']}  status={r['linkedin_conn_status']}")

conn.close()
