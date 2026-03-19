#!/usr/bin/env python3
"""
Run this ONCE locally to generate a Telegram session string.
Paste the printed TELEGRAM_SESSION value into Railway Variables.

Usage:
    pip install pyrogram tgcrypto
    python scripts/gen_session.py
"""

import asyncio


async def main():
    try:
        from pyrogram import Client
    except ImportError:
        print("ERROR: pyrogram not installed.")
        print("Run: pip install pyrogram tgcrypto")
        return

    print("Telegram Session Generator")
    print("=" * 40)
    api_id   = input("Enter your api_id (37923283): ").strip()
    api_hash = input("Enter your api_hash: ").strip()
    phone    = input("Enter your phone number (with country code, e.g. +12055794604): ").strip()

    async with Client(
        name="temp_session",
        api_id=int(api_id),
        api_hash=api_hash,
        phone_number=phone,
        in_memory=True,
    ) as app:
        session_string = await app.export_session_string()

    print("\n" + "=" * 60)
    print("SUCCESS! Add this to Railway Variables:")
    print("  Key:   TELEGRAM_SESSION")
    print("  Value: (the string below)")
    print("=" * 60)
    print(session_string)
    print("=" * 60)
    print("\nAlso add these Railway Variables if not already set:")
    print(f"  TELEGRAM_API_ID   = {api_id}")
    print(f"  TELEGRAM_API_HASH = {api_hash}")
    print("  TELEGRAM_MONITOR_CHANNELS = mad_apes_call,AnimeGems,tombstonecalls,x666calls,TrenchesbyAK,GibaTrades,Kingdom_X100_CALLS")


if __name__ == "__main__":
    asyncio.run(main())
