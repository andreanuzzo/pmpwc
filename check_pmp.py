#!/usr/bin/env python3
"""
Pick My Postcode - daily Main Draw checker (Playwright).

Renders the homepage in a real headless browser (so the JavaScript-injected
winning postcode is actually present), reads TODAY'S Main Draw result from the
Main Draw widget, and emails it via Gmail SMTP.

Env vars (set as GitHub Actions secrets):
  GMAIL_USER       - the Gmail address that sends the email
  GMAIL_APP_PASS   - a Google app password (NOT your normal password)
  MAIL_TO          - where to send the result (can equal GMAIL_USER)
  MY_POSTCODE      - optional; if set, the email says whether you won
"""

import os
import re
import smtplib
import sys
import traceback
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

from playwright.sync_api import sync_playwright

HOME_URL = "https://pickmypostcode.com/"
UK_TZ = timezone(timedelta(hours=1))  # BST; display only

POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}$", re.I)


def scrape_main_draw():
    """Return dict with postcode/prize/drawn text, or {} if not found."""
    with sync_playwright() as p:
        # --no-sandbox is required on GitHub-hosted runners; without it
        # Chromium exits immediately with a sandbox error.
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            )
        )
        # Site is ad/cookie heavy and may never reach true network idle, so
        # wait for DOM content then wait for the JS widget to inject the
        # postcode, rather than blocking on networkidle (which can time out).
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_function(
                """() => {
                  const heads = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5'));
                  const h = heads.find(x => x.textContent.trim().toLowerCase() === 'main draw');
                  if (!h) return false;
                  let n = h;
                  for (let i = 0; i < 6 && n && n.parentElement; i++) {
                    n = n.parentElement;
                    if (/Drawn/i.test(n.textContent)) break;
                  }
                  return n && /[A-Z]{1,2}\\d{1,2}[A-Z]?\\s?\\d[A-Z]{2}/i.test(n.textContent);
                }""",
                timeout=30000,
            )
        except Exception:
            pass  # evaluate below will report if it's still empty

        result = page.evaluate(
            """
            () => {
              const heads = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5'));
              const mainHead = heads.find(h => h.textContent.trim().toLowerCase() === 'main draw');
              if (!mainHead) return null;
              let node = mainHead;
              for (let i = 0; i < 6 && node && node.parentElement; i++) {
                node = node.parentElement;
                if (/Drawn/i.test(node.textContent)) break;
              }
              if (!node) return null;
              const text = node.innerText || node.textContent || '';
              return { block: text };
            }
            """
        )
        browser.close()

    if not result or not result.get("block"):
        return {}

    block = result["block"]
    out = {}

    m = re.search(
        r"Drawn\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+\d{4})?)", block, re.I
    )
    if m:
        out["drawn"] = m.group(1).strip()

    search_area = block[m.end():] if m else block
    for token in re.findall(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}", search_area, re.I):
        if POSTCODE_RE.match(token.strip()):
            out["postcode"] = re.sub(r"\s+", " ", token.strip().upper())
            break

    pm = re.search(r"£\s?[\d,]+(?:\.\d{2})?", search_area)
    if pm:
        out["prize"] = pm.group(0).replace(" ", "")

    return out


def send_email(subject, body):
    user = os.environ["GMAIL_USER"]
    app_pass = os.environ["GMAIL_APP_PASS"]
    to_addr = os.environ.get("MAIL_TO", user)

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, app_pass)
        smtp.sendmail(user, [to_addr], msg.as_string())


def main():
    today = datetime.now(UK_TZ).strftime("%-d %B %Y")
    try:
        data = scrape_main_draw()
    except Exception:  # noqa: BLE001 - surface any browser/render failure by email
        traceback.print_exc()
        sys.stderr.flush()
        try:
            send_email(
                "PMP check FAILED",
                "The checker errored while rendering the page:\n\n"
                + traceback.format_exc()
                + "\n" + HOME_URL,
            )
        except Exception:  # noqa: BLE001 - email may fail too; traceback already printed
            traceback.print_exc()
            sys.stderr.flush()
        sys.exit(1)

    if not data.get("postcode"):
        send_email(
            "PMP check: Main Draw postcode not found",
            "Rendered the homepage but couldn't read the Main Draw postcode.\n"
            "The page layout may have changed.\n\n" + HOME_URL,
        )
        sys.exit(1)

    postcode = data["postcode"]
    prize = data.get("prize", "")
    drawn = data.get("drawn", today)

    lines = [
        "Pick My Postcode - Main Draw",
        f"Drawn: {drawn}",
        f"Winning postcode: {postcode}",
    ]
    if prize:
        lines.append(f"Prize: {prize}")

    my_pc = os.environ.get("MY_POSTCODE", "").strip().upper()
    won = False
    if my_pc:
        won = re.sub(r"\s+", " ", my_pc) == postcode
        lines.append("")
        lines.append("YOU WON! Claim it now." if won else "Not your postcode today.")

    lines.append("")
    lines.append(f"Check / claim: {HOME_URL}")

    prefix = "WINNER " if won else ""
    subject = f"{prefix}PMP Main Draw {drawn}: {postcode}" + (f" ({prize})" if prize else "")
    send_email(subject, "\n".join(lines))
    print(subject)


if __name__ == "__main__":
    main()
