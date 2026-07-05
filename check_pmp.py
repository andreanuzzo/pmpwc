#!/usr/bin/env python3
"""
Pick My Postcode - daily Main Draw checker (Playwright).
Logs in (postcode + email), reads today's Main Draw result, emails via Gmail.
Env: GMAIL_USER, GMAIL_APP_PASS, MAIL_TO, PMP_POSTCODE, PMP_EMAIL,
     MY_POSTCODE (optional), DEBUG (optional).
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

DEBUG = os.environ.get("DEBUG", "").strip() not in ("", "0", "false", "False")

PMP_POSTCODE = os.environ.get("PMP_POSTCODE", "").strip()
PMP_EMAIL = os.environ.get("PMP_EMAIL", "").strip()


def log_in(page):
    """Sign in to PMP using postcode + email, and clear the T&C gate."""
    if not PMP_POSTCODE or not PMP_EMAIL:
        raise RuntimeError(
            "PMP_POSTCODE and PMP_EMAIL must be set (repo secrets) to log in."
        )

    for label in ("Accept T&Cs", "Accept", "I Accept", "Agree", "Got it"):
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                page.wait_for_timeout(500)
                break
        except Exception:
            pass

    def fill_first(selectors, value):
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    loc.first.fill(value, timeout=3000)
                    return True
            except Exception:
                continue
        return False

    filled_pc = fill_first(
        [
            "input[placeholder*='postcode' i]",
            "input[name*='postcode' i]",
            "input[id*='postcode' i]",
        ],
        PMP_POSTCODE,
    )
    filled_email = fill_first(
        [
            "input[type='email']",
            "input[placeholder*='email' i]",
            "input[name*='email' i]",
            "input[id*='email' i]",
        ],
        PMP_EMAIL,
    )

    if not (filled_pc and filled_email):
        if DEBUG:
            print(
                f"[login] fields filled? postcode={filled_pc} email={filled_email}",
                flush=True,
            )
        return

    for label in ("Sign in", "Sign In", "Log in", "Login"):
        try:
            btn = page.get_by_role("button", name=re.compile(f"^{label}$", re.I))
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                break
        except Exception:
            pass
    else:
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass


def scrape_main_draw():
    """Return dict with postcode/prize/drawn text, or {} if not found."""
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
        page.wait_for_timeout(3000)
         
        log_in(page)

        # Give client-side JS time to inject the draw values post-login.
        page.wait_for_timeout(8000)

        # Read the full rendered page text and parse the Main Draw block by its
        # exact content sequence. The DOM has two "Main Draw" labels (a nav item
        # and the real widget), so structural climbing is unreliable; this
        # content pattern is stable:
        #     Main Draw / Drawn <date> / <postcode> / <prize>
        result = page.evaluate("() => ({ full: document.body.innerText || '' })")

        try:
            page.screenshot(path="page.png", full_page=True)
        except Exception:
            pass

        full_text = (result or {}).get("full", "") or ""

        if DEBUG:
            print("----- FULL PAGE TEXT (first 3000 chars) -----", flush=True)
            print(full_text[:3000], flush=True)
            print("----- END DEBUG -----", flush=True)

        browser.close()

    out = {}

    m = re.search(
        r"Main Draw\s+Drawn\s+"
        r"(?P<date>\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+\d{4})?)\s+"
        r"(?P<postcode>[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})\s+"
        r"(?P<prize>£[\d,]+(?:\.\d{2})?)",
        full_text,
        re.I,
    )
    if m:
        out["drawn"] = m.group("date").strip()
        out["postcode"] = re.sub(r"\s+", " ", m.group("postcode").strip().upper())
        out["prize"] = m.group("prize").replace(" ", "")
        return out

    m2 = re.search(
        r"Drawn\s+(?P<date>\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+\d{4})?)\s+"
        r"(?P<postcode>[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2})",
        full_text,
        re.I,
    )
    if m2:
        out["drawn"] = m2.group("date").strip()
        out["postcode"] = re.sub(r"\s+", " ", m2.group("postcode").strip().upper())
        after = full_text[m2.end():]
        pm = re.search(r"£[\d,]+(?:\.\d{2})?", after)
        if pm:
            out["prize"] = pm.group(0)

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
            "Login may have failed or the layout changed.\n\n" + HOME_URL,
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

    # Which postcode to check against the winner. Defaults to the login
    # postcode (PMP_POSTCODE) so you only need to set one secret; set
    # MY_POSTCODE only if you want to check a different postcode.
    my_pc = (os.environ.get("MY_POSTCODE", "").strip() or PMP_POSTCODE).upper()
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
