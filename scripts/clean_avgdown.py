import sqlite3

import os
db = os.path.join(os.environ.get('DATA_DIR', '.'), 'trades.db')
with sqlite3.connect(db) as conn:
    rows = conn.execute(
        "SELECT id, type, token, reason FROM trades WHERE token LIKE '%AVG-DOWN%'"
    ).fetchall()
    print(f"Found {len(rows)} avg-down trade records:")
    for r in rows:
        print(f"  id={r[0]} type={r[1]} token={r[2]} reason={r[3][:60]}")

    if rows:
        conn.execute("DELETE FROM trades WHERE token LIKE '%AVG-DOWN%'")
        conn.commit()
        print(f"Deleted {len(rows)} records.")
    else:
        print("Nothing to delete.")
