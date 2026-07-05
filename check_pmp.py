#!/usr/bin/env python3
"""
Pick My Postcode - daily Main Draw checker (Playwright).
Renders the homepage in a headless browser and emails today's Main Draw result.
Env: GMAIL_USER, GMAIL_APP_PASS, MAIL_TO, MY_POSTCODE (optional), DEBUG (optional).
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

# Set DEBUG=1 in the environment to print the rendered page text to the log.
DEBUG = os.environ.get("DEBUG", "").strip() not in ("", "0", "false", "False")


def scrape_main_draw():
    """Return dict with block text (+ whether it was the targeted block)."""
    with sync_playwright() as p:
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
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)  # let client-side JS inject the draw values

        result = page.evaluate(
            """
            () => {
              const out = { block: null, full: (document.body.innerText || '') };
              const all = Array.from(document.querySelectorAll('body *'));
              const label = all.find(el => {
                const t = (el.textContent || '').trim();
                return /^main draw\\b/i.test(t) && t.length < 40;
              });
              if (label) {
                let node = label;
                for (let i = 0; i < 8 && node && node.parentElement; i++) {
                  node = node.parentElement;
                  if (/Drawn/i.test(node.textContent) &&
                      /[A-Z]{1,2}\\d{1,2}[A-Z]?\\s?\\d[A-Z]{2}/i.test(node.textContent)) {
                    break;
                  }
                }
                if (node) out.block = node.innerText || node.textContent || '';
              }
              return out;
            }
            """
        )

        try:
            page.screenshot(path="page.png", full_page=True)
        except Exception:
            pass

        full_text = (result or {}).get("full", "") or ""
        block_text = (result or {}).get("block")

        if DEBUG:
            print("----- FULL PAGE TEXT (first 3000 chars) -----", flush=True)
            print(full_text[:3000], flush=True)
            print("----- TARGETED BLOCK -----", flush=True)
            print(block_text, flush=True)
            print("----- END DEBUG -----", flush=True)

        browser.close()

    result = {"block": block_text or full_text, "targeted": bool(block_text)}

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
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.stderr.flush()
        try:
            send_email(
                "PMP check FAILED",
                "The checker errored while rendering the page:\n\n"
                + traceback.format_exc() + "\n" + HOME_URL,
            )
        except Exception:  # noqa: BLE001
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
