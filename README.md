# report_sms

Report spam texts from macOS Messages to the FCC (and optionally to 7726).

Give it a phone number. It finds the most recent text from that number in your
Messages database, asks a few questions about it, then opens Chrome and fills in
the [FCC unwanted calls/texts complaint form](https://consumercomplaints.fcc.gov/hc/en-us/requests/new?ticket_form_id=39744).
Nothing is submitted until you confirm in the terminal.

## Requirements

- macOS with Messages syncing your texts (SMS via iPhone Text Message Forwarding)
- Python 3
- **Full Disk Access** for your terminal app, so it can read
  `~/Library/Messages/chat.db`: System Settings > Privacy & Security > Full Disk Access
- Google Chrome (falls back to Playwright's bundled Chromium)

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install playwright
.venv/bin/playwright install chromium   # only needed if Chrome isn't installed

cp fcc_config.example.json fcc_config.json
```

Edit `fcc_config.json` with your contact details and answers. The script refuses
to run while the email still contains `example.com`. `fcc_config.json` is
gitignored.

## Usage

```sh
python3 report_text.py 555-555-5555
```

Omit the number to be prompted for it, so you can paste it in any format
(`+1 (555) 555-5555` works). `report_text.py` re-runs itself under `.venv`
automatically, so you don't need to activate it.

The script will:

1. Print the sender, date, time and text of their latest message.
2. Ask whether to report it, and warn if you've already reported that message.
3. Ask whether it was political, what it was selling, and which business it named.
4. Fill in the FCC form with a randomly chosen subject and description from your config.
5. Ask you to attest and submit. You can also review, edit or submit in the browser
   yourself. If a security check or CAPTCHA appears, complete it in the browser.
6. Log the report to `fcc_reports.csv`.
7. If `report_to_7726` is `true`, forward the text to 7726 (the carrier spam
   short code) through Messages, followed by the sender's number.

Options:

- `--db PATH`: use a different `chat.db`
- `--config PATH`: use a different config file

To just look up the latest message without reporting:

```sh
python3 last_message.py 555-555-5555
```

## Config

`fcc_config.json` has these sections:

| Key | Purpose |
| --- | --- |
| `contact` | Your name, address, email and phone for the complaint |
| `answers` | Fixed answers to the form's dropdowns (phone type, Do Not Call status, consent) |
| `report_to_7726` | Also forward reported texts to 7726 |
| `subjects`, `descriptions` | Templates for non-political texts |
| `political_subjects`, `political_descriptions` | Templates for political texts |

Subjects must be 70 characters or fewer. Descriptions can use `{sender}`,
`{date}`, `{time}` and `{received_on}` placeholders.

## Files

- `report_text.py`: the reporting script
- `last_message.py`: reads the latest message from a number out of `chat.db`
- `fcc_config.example.json`: template config
- `fcc_reports.csv`: log of submitted reports (created on first report, gitignored)
