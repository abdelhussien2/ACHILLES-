"""
scheduler.py - BrightData self-test. FOOLPROOF.

3 clicks on B0GG8F355W, each with a DIFFERENT keyword.
60-90 min gaps between clicks (different hours in Amazon report).
Emails proof when done.
"""

import os, sys, json, time, random, logging, asyncio, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from datetime import datetime, timezone, timedelta
from sourhunter4 import (
    create_browser, search, is_sponsored, click_product,
    handle_interstitial, popups, pause, screenshot
)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("tracker")

TARGETS = {
    "B0GG8F355W": "KozyKraft SF-Style Starter",
    "B0FCPFH2WR": "KozyKraft Dehydrated Starter",
    "B0GBWB83NM": "KozyKraft Live Wet Starter",
}

# 3 clicks - any of the 3 KozyKraft ASINs counts
CLICK_KEYWORDS = [
    "sourdough starter culture",
    "sourdough starter culture",
    "sourdough starter culture",
]

TALLY_FILE = "results/daily_tally.json"


def now():
    return datetime.now(timezone.utc)


def save_tally(t):
    os.makedirs("results", exist_ok=True)
    with open(TALLY_FILE, "w") as f:
        json.dump(t, f, indent=2)


def record(tally, status, detail="", keyword="", screenshots=None, asin="", name=""):
    tally["cycles"] += 1
    entry = {"time": now().strftime("%Y-%m-%d %H:%M UTC"), "status": status,
             "detail": detail, "keyword": keyword,
             "asin": asin, "name": name,
             "screenshots": screenshots or []}
    tally["log"].append(entry)
    if status == "clicked":
        tally["clicks"] += 1
    elif status == "organic":
        tally["organic"] += 1
    else:
        tally["errors"] += 1
    save_tally(tally)


def send_email(tally):
    sender = os.getenv("EMAIL_SENDER", "")
    pw = os.getenv("EMAIL_PASSWORD", "")
    to = os.getenv("EMAIL_RECIPIENT", "")
    if not all([sender, pw, to]):
        log.warning("No email creds")
        return

    lines = [
        f"BRIGHTDATA SELF-TEST — KOZYKRAFT MULTI-TARGET",
        f"Targets: {', '.join([f'{n} ({a})' for a, n in TARGETS.items()])}",
        f"3 clicks, any sponsored KozyKraft counts",
        "=" * 60, "",
        f"Clicks: {tally['clicks']}",
        f"Organic: {tally['organic']}",
        f"Errors: {tally['errors']}",
        f"Total cycles: {tally['cycles']}", "",
        "DETAILED LOG:",
        "-" * 60,
    ]
    for e in tally["log"]:
        lines.append(f"  Time:      {e['time']}")
        lines.append(f"  Status:    {e['status']}")
        lines.append(f"  Keyword:   {e.get('keyword', '?')}")
        if e["status"] == "clicked":
            lines.append(f"  ASIN:      {e.get('asin', '?')}")
            lines.append(f"  Listing:   {e.get('name', '?')}")
        lines.append(f"  Detail:    {e.get('detail', '-')}")
        lines.append(f"  {'─' * 40}")

    msg = MIMEMultipart()
    msg["From"], msg["To"] = sender, to
    msg["Subject"] = f"BrightData Test | {tally['clicks']}/3 KozyKraft clicks"
    msg.attach(MIMEText("\n".join(lines), "plain"))

    attached = 0
    for entry in tally["log"]:
        for ss_path in entry.get("screenshots", []):
            try:
                with open(ss_path, "rb") as f:
                    img = MIMEImage(f.read(), name=os.path.basename(ss_path))
                    img.add_header("Content-Disposition", "attachment",
                                   filename=os.path.basename(ss_path))
                    msg.attach(img)
                    attached += 1
            except:
                pass

    log.info(f"[EMAIL] Attaching {attached} screenshots")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as srv:
            srv.starttls(); srv.login(sender, pw); srv.send_message(msg)
        log.info(f"[EMAIL] Sent to {to}")
    except Exception as e:
        log.error(f"[EMAIL] {e}")


async def run_cycle(tally, keyword):
    """One cycle: search with specific keyword, click first sponsored KozyKraft."""
    playwright = None
    browser = None

    try:
        playwright, browser, page = await create_browser()
        log.info(f"  Keyword: \"{keyword}\"")

        results = await search(page, keyword)
        if not results:
            record(tally, "error", "no results", keyword)
            return False

        # Find ALL KozyKraft listings on the page
        kk_matches = [r for r in results if r["asin"] in TARGETS]
        if not kk_matches:
            record(tally, "error", "no KozyKraft on page", keyword)
            log.warning(f"  No KozyKraft listings on page")
            return False

        log.info(f"  ✓ Found {len(kk_matches)} KozyKraft listing(s):")
        for m in kk_matches:
            log.info(f"    #{m['i']} [{m['asin']}] {TARGETS[m['asin']]}")

        # Check each for sponsored status, click first sponsored
        match = None
        for m in kk_matches:
            log.info(f"  Checking #{m['i']} [{m['asin']}] for sponsored...")
            if await is_sponsored(page, m["asin"]):
                match = m
                log.info(f"  ✓ SPONSORED: {TARGETS[m['asin']]} ({m['asin']}) at #{m['i']}")
                break
            else:
                log.info(f"  Not sponsored: {TARGETS[m['asin']]}")

        if not match:
            record(tally, "organic", f"all {len(kk_matches)} KozyKraft listings organic", keyword)
            log.info(f"  All KozyKraft listings organic — will retry")
            return False

        ASIN = match["asin"]
        NAME = TARGETS[ASIN]

        click_screenshots = []

        # Pre-click: browse the results page like a real shopper
        log.info(f"  [BROWSE] Scrolling results page...")
        await page.evaluate(f"window.scrollTo({{top: {random.randint(200, 500)}, behavior: 'smooth'}})")
        await page.wait_for_timeout(int(pause(1, 2.5)))
        await page.evaluate(f"window.scrollTo({{top: {random.randint(600, 1000)}, behavior: 'smooth'}})")
        await page.wait_for_timeout(int(pause(1, 2)))
        # Scroll back up toward target
        await page.evaluate(
            f'document.querySelector(\'[data-asin="{ASIN}"]\')?.scrollIntoView({{block:"center", behavior:"smooth"}})')
        await page.wait_for_timeout(int(pause(1.5, 3)))

        ss = await screenshot(page, f"click_{ASIN}")
        if ss: click_screenshots.append(ss)

        await page.wait_for_timeout(int(pause(1, 3)))
        ok, method = await click_product(page, match["el"], ASIN)

        if ok:
            try:
                await page.wait_for_selector("#productTitle", timeout=30000)
                await page.wait_for_timeout(int(pause(2, 4)))
                await popups(page)
                ss = await screenshot(page, f"product_{ASIN}")
                if ss: click_screenshots.append(ss)

                log.info(f"  [DWELL] Browsing product page...")
                await page.wait_for_timeout(int(pause(2, 4)))
                await page.evaluate("window.scrollTo(0, 400)")
                await page.wait_for_timeout(int(pause(1, 3)))
                await page.evaluate("window.scrollTo(0, 800)")
                await page.wait_for_timeout(int(pause(2, 4)))
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
                await page.wait_for_timeout(int(pause(1, 3)))

                if random.random() < 0.3:
                    try:
                        review_link = page.locator("#acrCustomerReviewLink, a[data-hook='see-all-reviews-link-foot']").first
                        if await review_link.is_visible(timeout=2000):
                            await review_link.click()
                            await page.wait_for_timeout(int(pause(3, 6)))
                            log.info("  [DWELL] Checked reviews")
                    except:
                        pass

                await page.evaluate("window.scrollTo(0, 200)")
                await page.wait_for_timeout(int(pause(1, 2)))
                log.info(f"  [DWELL] Done")
            except:
                pass

            log.info(f"  ✓ Clicked {NAME} via {method}")
            record(tally, "clicked", method, keyword, click_screenshots, asin=ASIN, name=NAME)
            return True
        else:
            log.warning(f"  Click failed")
            record(tally, "error", "click failed", keyword)
            return False

    except Exception as e:
        log.error(f"  ERROR: {str(e)[:100]}")
        record(tally, "error", str(e)[:80], keyword)
        return False

    finally:
        if browser:
            try: await browser.close()
            except: pass
        if playwright:
            try: await playwright.stop()
            except: pass


async def main():
    log.info(f"\n{'='*60}")
    log.info(f"  BRIGHTDATA SELF-TEST — KOZYKRAFT MULTI-TARGET")
    log.info(f"  Targets:")
    for asin, name in TARGETS.items():
        log.info(f"    {asin}: {name}")
    log.info(f"  3 clicks, any sponsored KozyKraft counts")
    log.info(f"  Keywords:")
    for i, kw in enumerate(CLICK_KEYWORDS):
        log.info(f"    Click {i+1}: \"{kw}\"")
    log.info(f"  Gap: 90-120 min between successful clicks")
    log.info(f"{'='*60}\n")

    tally = {"clicks": 0, "organic": 0, "errors": 0, "cycles": 0, "log": []}

    for i, keyword in enumerate(CLICK_KEYWORDS):
        log.info(f"\n{'='*60}")
        log.info(f"  TARGET CLICK {i+1}/3 | Keyword: \"{keyword}\"")
        log.info(f"  {now().strftime('%H:%M UTC')} | Clicks so far: {tally['clicks']}/3")
        log.info(f"{'='*60}")

        # Keep retrying this keyword until we get a sponsored click
        while True:
            clicked = await run_cycle(tally, keyword)
            if clicked:
                break
            # Retry in 5-7 min if organic/error
            wait = random.randint(8, 15) * 60
            log.info(f"  Retrying \"{keyword}\" in {wait//60}m...")
            await asyncio.sleep(wait)

        # Wait 60-90 min before next keyword (land in different hours)
        if i < len(CLICK_KEYWORDS) - 1:
            wait = random.randint(90, 120) * 60
            next_run = now() + timedelta(seconds=wait)
            log.info(f"  ✓ Click {i+1} done. Waiting {wait//60}m for next keyword.")
            log.info(f"  Next click at {next_run.strftime('%H:%M UTC')}")
            await asyncio.sleep(wait)

    log.info(f"\n{'='*60}")
    log.info(f"  ✓ ALL 3 CLICKS DONE")
    log.info(f"  Clicks: {tally['clicks']} | Cycles: {tally['cycles']}")
    log.info(f"{'='*60}")

    send_email(tally)
    log.info("  Keeping container alive.")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
