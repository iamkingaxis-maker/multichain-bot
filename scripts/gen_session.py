#!/usr/bin/env python3
"""
Two-step Telegram session generator (no interactive input required).

Step 1 - sends the login code to your Telegram app:
    python scripts/gen_session.py send

Step 2 - after you see the code in Telegram, run:
    python scripts/gen_session.py <code>
    e.g. python scripts/gen_session.py 12345
"""

import asyncio
import json
import sys
import os

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

API_ID   = 37923283
API_HASH = "AAF388DE0D37B5B8824A422C34FA87FA"
PHONE    = "+12055794604"
HASH_FILE = os.path.join(os.path.dirname(__file__), ".tg_code_hash.json")


async def step1_send():
    from pyrogram import Client
    app = Client("tg_session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await app.connect()
    sent = await app.send_code(PHONE)
    await app.disconnect()
    with open(HASH_FILE, "w") as f:
        json.dump({"hash": sent.phone_code_hash}, f)
    print("Code sent to your Telegram app.")
    print(f"Once you see it, run:")
    print(f"  python scripts/gen_session.py <code>")


async def step2_signin(code: str):
    from pyrogram import Client
    if not os.path.exists(HASH_FILE):
        print("ERROR: Run 'python scripts/gen_session.py send' first.")
        return
    with open(HASH_FILE) as f:
        phone_code_hash = json.load(f)["hash"]
    os.remove(HASH_FILE)

    app = Client("tg_session", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await app.connect()
    try:
        await app.sign_in(PHONE, phone_code_hash, code)
    except Exception as e:
        if "SESSION_PASSWORD_NEEDED" in str(e):
            print("Two-step verification is on.")
            print("Re-run with your password:")
            print("  python scripts/gen_session.py send")
            print("(or pass password as second arg — contact dev)")
            await app.disconnect()
            return
        raise

    session = await app.export_session_string()
    await app.disconnect()

    print("\n" + "=" * 60)
    print("Add this to Railway Variables:")
    print("  Key:   TELEGRAM_SESSION")
    print("  Value:")
    print("=" * 60)
    print(session)
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]
    if arg == "send":
        asyncio.run(step1_send())
    else:
        asyncio.run(step2_signin(arg))
