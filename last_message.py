#!/usr/bin/env python3
"""Print the most recent message received from a phone number in Apple Messages.

Usage:
    python3 last_message.py 555-555-5555

Requires Full Disk Access for the app running this script (Terminal, iTerm, etc.):
System Settings > Privacy & Security > Full Disk Access.
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DEFAULT_DB = os.path.expanduser("~/Library/Messages/chat.db")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def last_ten_digits(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else None


def format_number(ten_digits):
    return f"{ten_digits[:3]}-{ten_digits[3:6]}-{ten_digits[6:]}"


def apple_time_to_local(raw):
    # Newer databases store nanoseconds since 2001-01-01; very old ones store seconds.
    seconds = raw / 1_000_000_000 if raw > 1_000_000_000_000 else raw
    return (APPLE_EPOCH + timedelta(seconds=seconds)).astimezone()


def decode_attributed_body(blob):
    """Pull the plain text out of an NSAttributedString typedstream blob."""
    if not blob:
        return None
    start = blob.find(b"NSString")
    if start == -1:
        return None
    marker = blob.find(b"\x01+", start)
    if marker == -1:
        return None
    i = marker + 2
    length = blob[i]
    i += 1
    if length == 0x81:
        length = int.from_bytes(blob[i:i + 2], "little")
        i += 2
    elif length == 0x82:
        length = int.from_bytes(blob[i:i + 4], "little")
        i += 4
    return blob[i:i + length].decode("utf-8", errors="replace")


def find_last_message(db_path, number):
    target = last_ten_digits(number)
    if not target:
        sys.exit(f"Error: '{number}' does not look like a 10-digit phone number.")

    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        sys.exit(f"Error opening {db_path}: {e}\n"
                 "Grant Full Disk Access to your terminal app and try again.")

    with db:
        try:
            handles = db.execute("SELECT ROWID, id FROM handle").fetchall()
        except sqlite3.DatabaseError as e:
            sys.exit(f"Error reading {db_path}: {e}\n"
                     "Grant Full Disk Access to your terminal app and try again.")

        handle_ids = [rowid for rowid, hid in handles if last_ten_digits(hid) == target]
        if not handle_ids:
            return None

        placeholders = ",".join("?" * len(handle_ids))
        row = db.execute(f"""
            SELECT date, text, attributedBody, cache_has_attachments
            FROM message
            WHERE is_from_me = 0 AND handle_id IN ({placeholders})
            ORDER BY date DESC
            LIMIT 1
        """, handle_ids).fetchone()

    if not row:
        return None

    date, text, body, has_attachments = row
    content = text or decode_attributed_body(body)
    if not content or not content.strip("￼"):
        content = "[attachment]" if has_attachments else "[no text content]"

    return format_number(target), apple_time_to_local(date), content


def format_date(sent):
    return f"{sent:%B} {sent.day}, {sent.year}"


def format_time(sent):
    hour = sent.hour % 12 or 12
    meridiem = "am" if sent.hour < 12 else "pm"
    return f"{hour}:{sent.minute:02d} {meridiem}"


def print_result(result):
    number, sent, content = result
    print(number)
    print(format_date(sent))
    print(format_time(sent))
    print(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("number", nargs="*",
                        help="Phone number, e.g. 555-555-5555 or '+1 (555) 555-5555'. "
                             "Omit to be prompted, so you can paste it as-is.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to chat.db (default: %(default)s)")
    args = parser.parse_args()

    # Spaces in an unquoted number split it into several arguments; rejoin them.
    number = " ".join(args.number) or input("Phone number: ")

    result = find_last_message(args.db, number)
    if not result:
        sys.exit(f"No messages found from {number.strip()}.")

    print_result(result)


if __name__ == "__main__":
    main()
