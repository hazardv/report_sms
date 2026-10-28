#!/usr/bin/env python3
"""Show the latest text from a phone number and optionally report it to the FCC.

Usage:
    python3 report_text.py 555-555-5555

Prints the same output as last_message.py, asks whether to report the message, then
opens Chrome and fills in the FCC unwanted calls/texts complaint form using
fcc_config.json. Nothing is submitted until you confirm in the terminal.
"""

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"

try:
    from playwright.sync_api import sync_playwright, expect, Error as PlaywrightError
except ImportError:
    # Re-run under the project's virtualenv, where Playwright is installed.
    venv_python = VENV / "bin" / "python"
    if venv_python.exists() and Path(sys.prefix).resolve() != VENV.resolve():
        os.execv(venv_python, [str(venv_python), __file__, *sys.argv[1:]])
    sys.exit("Playwright is not installed. Run:\n"
             f"  python3 -m venv {VENV} && {VENV}/bin/pip install playwright")

sys.path.insert(0, str(HERE))
from last_message import (DEFAULT_DB, find_last_message, format_date, format_time,  # noqa: E402
                          print_result)

FORM_URL = "https://consumercomplaints.fcc.gov/hc/en-us/requests/new?ticket_form_id=39744"
DEFAULT_CONFIG = HERE / "fcc_config.json"
LOG_FILE = HERE / "fcc_reports.csv"

SUBJECT_LIMIT = 70
DESCRIPTION_LIMIT = 2000

# Text inputs on the form, by their Zendesk field name.
FIELDS = {
    "email": "request[anonymous_requester_email]",
    "subject": "request[subject]",
    "number_received_on": "request[custom_fields][22625614]",
    "time": "request[custom_fields][22732340]",
    "caller_id_number": "request[custom_fields][22659864]",
    "advertiser_name": "request[custom_fields][22659904]",
    "additional_info": "request[custom_fields][22664784]",
    "first_name": "request[custom_fields][22539594]",
    "last_name": "request[custom_fields][22704720]",
    "address1": "request[custom_fields][22554824]",
    "address2": "request[custom_fields][22718270]",
    "city": "request[custom_fields][22554844]",
    "zip": "request[custom_fields][22540124]",
    "phone": "request[custom_fields][22615094]",
    "attest": "request[custom_fields][22625624]",
}

POLITICAL_TYPE = "Political (including campaign calls/elections)"
GOODS_TYPES = [
    "Amazon",
    "Auto Warranties",
    "Business Loans",
    "Credit Cards (including debt or interest rates)",
    "Criminal Charges/Threats of Legal Action",
    "Debt Relief/Debt Consolidation",
    "Donations/Charities",
    "Fraudulent Charges/Orders",
    "Google Listings",
    "Healthcare (including Medicare/Medicaid)",
    "Home Improvement (including solar, roofing, siding)",
    "Home Security/Alarms",
    "IRS/Tax Relief",
    "Mortgage Loans",
    "Pay Day Loans",
    "Prizes/You've Won Something",
    "Social Security",
    "Student Loans",
    "Vacations (including cruises, timeshares)",
    "Unknown",
    "Other",
]

ATTEST_TEXT = (
    "I declare under penalty of perjury that (1) I am over 18 years old, (2) I am "
    "authorized to make decisions regarding the telephone number listed below, and "
    "(3) the information I have provided today on this Federal Communications "
    "Commission electronic form is, to the best of my knowledge, true and correct."
)


def ask_yes_no(question):
    while True:
        answer = input(f"{question} [y/n] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def ask_goods_type():
    print("What was it selling or promoting?")
    for i, name in enumerate(GOODS_TYPES, 1):
        print(f"  {i:2}. {name}")
    while True:
        answer = input(f"Choose 1-{len(GOODS_TYPES)}: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(GOODS_TYPES):
            return GOODS_TYPES[int(answer) - 1]


def load_config(path):
    try:
        config = json.loads(Path(path).read_text())
    except FileNotFoundError:
        sys.exit(f"Config file not found: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"Config file {path} is not valid JSON: {e}")

    for key in ("subjects", "political_subjects"):
        for subject in config[key]:
            if len(subject) > SUBJECT_LIMIT:
                sys.exit(f"Subject is over {SUBJECT_LIMIT} characters in {key}: {subject!r}")
    if "example.com" in config["contact"]["email"]:
        sys.exit(f"Fill in your details in {path} before reporting.")
    return config


def already_reported(sender, sent_date, sent_time):
    if not LOG_FILE.exists():
        return False
    with LOG_FILE.open(newline="") as f:
        return any(row["sender"] == sender and row["date"] == sent_date and row["time"] == sent_time
                   for row in csv.DictReader(f))


def log_report(sender, sent_date, sent_time, subject, url):
    new_file = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["reported_at", "sender", "date", "time", "subject", "url"])
        if new_file:
            writer.writeheader()
        writer.writerow({"reported_at": datetime.now().isoformat(timespec="seconds"),
                         "sender": sender, "date": sent_date, "time": sent_time,
                         "subject": subject, "url": url})


def build_report(config, result):
    sender, sent, content = result
    sent_date, sent_time = format_date(sent), format_time(sent)

    # The FCC form files political messages as advertising, with type "Political".
    political = ask_yes_no("Was this a political message?")
    if political:
        advertised, goods_type = True, POLITICAL_TYPE
    else:
        advertised = ask_yes_no("Was it soliciting for a business (advertising property, goods, or services)?")
        goods_type = ask_goods_type() if advertised else None
    business_name = input("Business or organization named in the message (blank if none): ").strip()
    named_at_start = ask_yes_no("Was that name given at the beginning of the message?") if business_name else False

    prefix = "political_" if political else ""
    placeholders = {"sender": sender, "date": sent_date, "time": sent_time,
                    "received_on": config["answers"]["number_received_on"]}
    subject = random.choice(config[f"{prefix}subjects"])
    description = random.choice(config[f"{prefix}descriptions"]).format(**placeholders)[:DESCRIPTION_LIMIT]

    return {
        "sender": sender, "date": sent_date, "time": sent_time, "content": content,
        "advertised": advertised, "goods_type": goods_type, "business_name": business_name, "named_at_start": named_at_start,
        "subject": subject, "description": description,
    }


SPAM_SHORT_CODE = "7726"
NUMBER_DELAY = 5  # seconds between sending the message and the sender's number

SEND_SMS_SCRIPT = """
on run {targetNumber, msg}
    tell application "Messages"
        set smsAccount to 1st account whose service type = SMS
        send msg to participant targetNumber of smsAccount
    end tell
end run
"""


def send_sms(number, text):
    result = subprocess.run(["osascript", "-e", SEND_SMS_SCRIPT, number, text],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())


def report_to_7726(sender, content):
    """Send the message to 7726, followed by the sender's number."""
    if content in ("[attachment]", "[no text content]"):
        print("Skipping 7726: the message has no text to forward.")
        return

    print(f"\nReporting to {SPAM_SHORT_CODE}...")
    try:
        send_sms(SPAM_SHORT_CODE, content)
        print("Sent the message.")
        time.sleep(NUMBER_DELAY)  # so the number arrives after the message
        send_sms(SPAM_SHORT_CODE, sender)
        print(f"Sent the sender's number ({sender}).")
    except RuntimeError as e:
        print(f"Could not finish reporting to {SPAM_SHORT_CODE}: {e}")


def choose(page, label, option):
    """Pick an option from one of the form's dropdowns."""
    # Playwright's selector syntax can't contain an unescaped "/" inside a regex.
    pattern = "^" + re.escape(label).replace("/", r"\/")
    combo = page.get_by_role("combobox", name=re.compile(pattern))
    for _ in range(3):
        if combo.get_attribute("aria-expanded") != "true":
            combo.click()
        page.get_by_role("option", name=option, exact=True).click()
        try:
            expect(combo).to_have_text(option, timeout=3000)
            return
        except AssertionError:
            pass
    raise PlaywrightError(f"Could not select {option!r} for {label!r}")


def fill(page, field, value):
    page.locator(f'[name="{FIELDS[field]}"]').fill(value)


def fill_form(page, config, report):
    contact, answers = config["contact"], config["answers"]
    yes_no = lambda flag: "Yes" if flag else "No"

    page.goto(FORM_URL)
    email = page.locator(f'[name="{FIELDS["email"]}"]')
    try:
        email.wait_for(timeout=10_000)
    except PlaywrightError:
        print("If the browser shows a security check, complete it there. Waiting up to 5 minutes.")
        email.wait_for(timeout=300_000)
    email.fill(contact["email"])
    fill(page, "subject", report["subject"])
    page.locator(".ck-editor__editable").fill(report["description"])

    choose(page, "Phone Issues", "Unwanted Calls/Texts (including do not call and spoofing)")
    choose(page, "Unwanted Calls/Texts Sub Issue", "All Other Unwanted Calls/Texts")
    choose(page, "Did the call/message that you are reporting advertise", yes_no(report["advertised"]))
    if report["advertised"]:
        choose(page, "Type of Property, Goods, or Services", report["goods_type"])
    choose(page, "Did the caller claim to be from an organization", answers["claimed_false_organization"])
    choose(page, "Your Phone Method", answers["phone_method"])
    choose(page, "Your Phone Type/Location", answers["phone_type"])
    fill(page, "number_received_on", answers["number_received_on"])
    choose(page, "Please indicate if the telephone number subject to this complaint is registered",
           answers["registered_on_do_not_call"])

    date_input = page.get_by_label("Date of Your Issue/Problem")
    date_input.fill(report["date"])
    date_input.press("Escape")
    fill(page, "time", report["time"])

    choose(page, "Type of Call or Message", "Text Message")
    choose(page, "Have you or anyone else in your household or business given", answers["gave_permission"])
    if report["advertised"]:
        choose(page, "Have you or anyone else in your household done any business",
               answers["did_business_with_sender"])
        choose(page, "Have you or anyone else in your household made any inquiry",
               answers["made_inquiry_or_application"])
        choose(page, "Do you or anyone in your household have a personal relationship",
               answers["personal_relationship_with_sender"])
    choose(page, "Did you receive Caller ID Information", "Yes")
    fill(page, "caller_id_number", report["sender"])

    choose(page, "Was the caller's business name provided", yes_no(report["business_name"]))
    if report["business_name"]:
        choose(page, "Was the business name provided at the beginning", yes_no(report["named_at_start"]))
        fill(page, "advertiser_name", report["business_name"])

    fill(page, "additional_info", f"Full text of the message I received:\n\n{report['content']}")

    fill(page, "first_name", contact["first_name"])
    fill(page, "last_name", contact["last_name"])
    fill(page, "address1", contact["address1"])
    fill(page, "address2", contact.get("address2", ""))
    fill(page, "city", contact["city"])
    choose(page, "State", contact["state"])
    fill(page, "zip", contact["zip"])
    fill(page, "phone", contact["phone"])
    choose(page, "Filing on Behalf of Someone", answers["filing_on_behalf_of_someone"])


def submitted_in_browser(page):
    """The form page redirects away from /requests/new once a complaint is submitted."""
    try:
        return "/requests/new" not in page.url
    except PlaywrightError:  # browser window was closed
        return False


def submit(page):
    # A hidden input shares the checkbox's name, so target the checkbox itself. The
    # checkbox is invisible and covered by its label, so click the label like a person would.
    attest = page.locator(f'input[type="checkbox"][name="{FIELDS["attest"]}"]')
    if not attest.is_checked():
        page.locator(f'label[for="{attest.get_attribute("id")}"]').click()
    expect(attest).to_be_checked(timeout=5000)
    page.get_by_role("button", name="Submit", exact=True).click()
    print("Submitting... If a CAPTCHA or form error appears, deal with it in the browser "
          "and click Submit there. Waiting up to 10 minutes.")
    page.wait_for_url(lambda url: "/requests/new" not in url, timeout=600_000)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("number", nargs="*",
                        help="Phone number, e.g. 555-555-5555 or '+1 (555) 555-5555'. "
                             "Omit to be prompted, so you can paste it as-is.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to chat.db (default: %(default)s)")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to config (default: %(default)s)")
    args = parser.parse_args()

    number = " ".join(args.number) or input("Phone number: ")

    result = find_last_message(args.db, number)
    if not result:
        sys.exit(f"No messages found from {number.strip()}.")
    print_result(result)
    print()

    if not ask_yes_no("Do you want to report this?"):
        return

    config = load_config(args.config)
    sender, sent, _ = result
    if already_reported(sender, format_date(sent), format_time(sent)) and \
            not ask_yes_no("You have already reported this message. Report it again?"):
        return

    report = build_report(config, result)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=False)
        except PlaywrightError:
            browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        print("\nFilling in the FCC complaint form...")
        try:
            fill_form(page, config, report)
        except PlaywrightError as e:
            print(f"\nCould not fill in the form automatically: {e}\n"
                  "Finish it in the browser window if you like.")
            input("Press Enter to close the browser.")
            return

        print(f"\nSubject:     {report['subject']}")
        print(f"Description: {report['description']}\n")
        print("Review the form in the browser window. Submitting ticks the Attest box, which states:")
        print(f"  \"{ATTEST_TEXT}\"\n")

        wants_submit = ask_yes_no("Attest and submit this complaint?")

        if submitted_in_browser(page):
            print("It looks like you already submitted the form in the browser.")
        elif not wants_submit:
            input("Not submitted. You can edit or submit it in the browser yourself. "
                  "Press Enter when you're done to close the browser.")
            if not submitted_in_browser(page):
                return
        else:
            try:
                submit(page)
            except (PlaywrightError, AssertionError) as e:
                print(f"\nThe form did not finish submitting: {str(e).splitlines()[0]}")
                input("Check the browser (you can tick Attest and click Submit there), "
                      "then press Enter to close it.")
                if not submitted_in_browser(page):
                    return

        log_report(sender, report["date"], report["time"], report["subject"], page.url)
        print(f"Submitted. Confirmation page: {page.url}")

        if config.get("report_to_7726"):
            report_to_7726(sender, report["content"])
        input("Press Enter to close the browser.")
        browser.close()


if __name__ == "__main__":
    main()
