"""
scheduler.py - BrightData 10-click test on B0FCPFH2WR (KozyKraft Dehydrated).
5 keywords × 2 modes (5 SIMPLE first, then 5 HUMANIZED).
60-90 min gaps between clicks (different hours in Amazon report).
Emails proof when done with mode + keyword logged for each click.
"""

import os, sys, json, time, random, logging, asyncio, smtplib

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from datetime import datetime, timezone, timedelta

from sourhunter4 import (
    create_browser, search, simple_search, is_sponsored,
    click_product, simple_click_product,
    handle_interstitial, popups, pause, screenshot
)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])

log = logging.getLogger("tracker")

TARGET_ASIN = "B0FCPFH2WR"
TARGET_NAME = "KozyKraft Dehydrated Starter"

# 5 keywords - will cycle through for SIMPLE, then repeat for HUMANIZED
KEYWORDS = [
    "sourdough starter culture",
    "sourdough starter dehydrated",
    "sourdough bread starter",
    "dry sourdough starter",
    "starter culture bread",
]

TALLY_FILE = "results/daily_tally.json"

def now():
    return datetime.now(timezone.utc)

def save_tally(t):
    os.makedirs("results", exist_ok=True)
    with open(TALLY_FILE, "w") as f:
        json.dump(t, f, indent=2)

def record(tally, status, detail="", keyword="", screenshots=None, mode="", click_num=0):
    tally["cycles"] += 1
    entry = {
        "click_num": click_num,
        "time": now().strftime("%Y-%m-%d %H:%M UTC"),
        "mode": mode,
        "keyword": keyword,
        "status": status,
        "detail": detail,
        "screenshots": screenshots or []
    }
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
        f"BRIGHTDATA 10-CLICK TEST — KOZYKRAFT DEHYDRATED STARTER",
        f"Target: {TARGET_NAME} ({TARGET_ASIN})",
        f"5 keywords × 2 modes: 5 SIMPLE clicks, then 5 HUMANIZED clicks",
        f"Each click checks for SPONSORED status",
        "=" * 70,
        "",
        f"SUMMARY:",
        f"  Clicks: {tally['clicks']}/10",
        f"  Organic (not sponsored): {tally['organic']}",
        f"  Errors: {tally['errors']}",
        f"  Total cycles: {tally['cycles']}",
        "",
        "DETAILED LOG:",
        "-" * 70,
    ]
    
    for e in tally["log"]:
        lines.append(f" Click #{e['click_num']}")
        lines.append(f"  Mode: {e['mode'].upper()}")
        lines.append(f"  Keyword: \"{e['keyword']}\"")
        lines.append(f"  Time: {e['time']}")
        lines.append(f"  Status: {e['status']}")
        
        if e["status"] == "clicked":
            lines.append(f"  ✓ SPONSORED click registered")
        elif e["status"] == "organic":
            lines.append(f"  Listing was organic (not sponsored) — retried")
        else:
            lines.append(f"  Error: {e['detail']}")
        
        lines.append(f"  {'─' * 66}")
    
    msg = MIMEMultipart()
    msg["From"], msg["To"] = sender, to
    msg["Subject"] = f"BrightData 10-Click Test | {tally['clicks']}/10 sponsored clicks on {TARGET_ASIN}"
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
            srv.starttls()
            srv.login(sender, pw)
            srv.send_message(msg)
        log.info(f"[EMAIL] Sent to {to}")
    except Exception as e:
        log.error(f"[EMAIL] {e}")

async def run_cycle(tally, keyword, mode, click_num):
    """One cycle: search with keyword, find B0FCPFH2WR, check SPONSORED, click.
    
    mode: 'simple' or 'humanized'
    click_num: which click this is (1-10)
    """
    playwright = None
    browser = None
    
    try:
        playwright, browser, page = await create_browser()
        
        log.info(f" Click #{click_num} | Mode: {mode.upper()} | Keyword: \"{keyword}\"")
        
        # Pick search function based on mode
        if mode == "simple":
            results = await simple_search(page, keyword)
        else:
            results = await search(page, keyword)
        
        if not results:
            record(tally, "error", "no results", keyword, mode=mode, click_num=click_num)
            return False
        
        # Find TARGET_ASIN
        target_match = None
        for r in results:
            if r["asin"] == TARGET_ASIN:
                target_match = r
                break
        
        if not target_match:
            record(tally, "error", f"{TARGET_ASIN} not on page", keyword, mode=mode, click_num=click_num)
            log.warning(f" Target ASIN not found")
            return False
        
        log.info(f" ✓ Found target: [{TARGET_ASIN}] {TARGET_NAME} at #{target_match['i']}")
        
        # CHECK FOR SPONSORED
        log.info(f" Checking if SPONSORED...")
        is_sponsored_result = await is_sponsored(page, TARGET_ASIN)
        
        if not is_sponsored_result:
            record(tally, "organic", f"{TARGET_ASIN} not sponsored", keyword, mode=mode, click_num=click_num)
            log.info(f" Listing is ORGANIC (not sponsored) — will retry")
            return False
        
        log.info(f" ✓ SPONSORED confirmed")
        
        # CLICK
        click_screenshots = []
        
        if mode == "simple":
            # March flow: minimal pre-click
            await page.evaluate(
                f'document.querySelector(\'[data-asin="{TARGET_ASIN}"]\')?.scrollIntoView({{block:"center"}})')
            await page.wait_for_timeout(1500)
            ss = await screenshot(page, f"click_{TARGET_ASIN}")
            if ss: click_screenshots.append(ss)
            await page.wait_for_timeout(int(pause(1, 3)))
            ok, method = await simple_click_product(page, target_match["el"], TARGET_ASIN)
        else:
            # Humanized: scroll naturally + wait for images + curved mouse
            log.info(f" [BROWSE] Scrolling results page...")
            await page.evaluate(f"window.scrollTo({{top: {random.randint(200, 500)}, behavior: 'smooth'}})")
            await page.wait_for_timeout(int(pause(1, 2.5)))
            await page.evaluate(f"window.scrollTo({{top: {random.randint(600, 1000)}, behavior: 'smooth'}})")
            await page.wait_for_timeout(int(pause(1, 2)))
            
            await page.evaluate(
                f'document.querySelector(\'[data-asin="{TARGET_ASIN}"]\')?.scrollIntoView({{block:"center", behavior:"smooth"}})')
            await page.wait_for_timeout(int(pause(1.5, 3)))
            
            log.info(f" [WAIT] Waiting for product image to load...")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except:
                pass
            
            try:
                img_loaded = await page.evaluate(f"""
                    () => {{
                        const card = document.querySelector('[data-asin="{TARGET_ASIN}"]');
                        if (!card) return false;
                        const img = card.querySelector('.s-product-image-container img, img.s-image');
                        if (!img) return false;
                        return img.complete && img.naturalHeight > 0;
                    }}
                """)
                if img_loaded:
                    log.info(f" [WAIT] Product image confirmed loaded")
                else:
                    log.warning(f" [WAIT] Image not fully loaded — waiting 3s more")
                    await page.wait_for_timeout(3000)
            except Exception as e:
                log.warning(f" [WAIT] Image check failed: {str(e)[:60]}")
            
            ss = await screenshot(page, f"click_{TARGET_ASIN}")
            if ss: click_screenshots.append(ss)
            await page.wait_for_timeout(int(pause(1, 3)))
            ok, method = await click_product(page, target_match["el"], TARGET_ASIN)
        
        if ok:
            try:
                await page.wait_for_selector("#productTitle", timeout=30000)
                await page.wait_for_timeout(int(pause(2, 4)))
                await popups(page)
                ss = await screenshot(page, f"product_{TARGET_ASIN}")
                if ss: click_screenshots.append(ss)
                
                log.info(f" [DWELL] Browsing product page...")
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
                            log.info(" [DWELL] Checked reviews")
                    except:
                        pass
                
                await page.evaluate("window.scrollTo(0, 200)")
                await page.wait_for_timeout(int(pause(1, 2)))
                log.info(f" [DWELL] Done")
            except:
                pass
            
            log.info(f" ✓ CLICK #{click_num} SUCCESS ({mode.upper()}, {method})")
            record(tally, "clicked", method, keyword, click_screenshots, mode=mode, click_num=click_num)
            return True
        else:
            log.warning(f" Click failed")
            record(tally, "error", "click failed", keyword, mode=mode, click_num=click_num)
            return False
    
    except Exception as e:
        log.error(f" ERROR: {str(e)[:100]}")
        record(tally, "error", str(e)[:80], keyword, mode=mode, click_num=click_num)
        return False
    
    finally:
        if browser:
            try: await browser.close()
            except: pass
        if playwright:
            try: await playwright.stop()
            except: pass

async def main():
    log.info(f"\n{'='*70}")
    log.info(f" BRIGHTDATA 10-CLICK TEST — KOZYKRAFT DEHYDRATED STARTER")
    log.info(f" Target: {TARGET_ASIN} | {TARGET_NAME}")
    log.info(f" 5 clicks SIMPLE mode (keywords 1-5)")
    log.info(f" 5 clicks HUMANIZED mode (keywords 1-5)")
    log.info(f" 60-90 min gap between successful clicks")
    log.info(f" Each click verifies SPONSORED status before clicking")
    log.info(f"{'='*70}\n")
    
    tally = {"clicks": 0, "organic": 0, "errors": 0, "cycles": 0, "log": []}
    
    # Test plan: 5 SIMPLE clicks (keywords 0-4), then 5 HUMANIZED clicks (keywords 0-4)
    test_plan = [
        ("simple", 1), ("simple", 2), ("simple", 3), ("simple", 4), ("simple", 5),
        ("humanized", 6), ("humanized", 7), ("humanized", 8), ("humanized", 9), ("humanized", 10),
    ]
    
    for plan_idx, (mode, click_num) in enumerate(test_plan):
        # Cycle through keywords (same keyword for SIMPLE and HUMANIZED at the same position)
        keyword_idx = (click_num - 1) % 5
        keyword = KEYWORDS[keyword_idx]
        
        log.info(f"\n{'='*70}")
        log.info(f" CLICK {click_num}/10 | Mode: {mode.upper()}")
        log.info(f" Keyword: \"{keyword}\"")
        log.info(f" {now().strftime('%H:%M UTC')} | Clicks so far: {tally['clicks']}/10")
        log.info(f"{'='*70}")
        
        # Retry until sponsored click lands
        while True:
            clicked = await run_cycle(tally, keyword, mode, click_num)
            if clicked:
                break
            wait = 5 * 60
            log.info(f" Retrying in {wait//60}m...")
            await asyncio.sleep(wait)
        
        # 15 min gap between clicks
        if plan_idx < len(test_plan) - 1:
            wait = 15 * 60
            next_run = now() + timedelta(seconds=wait)
            log.info(f" ✓ Click {click_num} done ({mode}). Waiting {wait//60}m.")
            log.info(f" Next click at {next_run.strftime('%H:%M UTC')}")
            await asyncio.sleep(wait)
    
    log.info(f"\n{'='*70}")
    log.info(f" ✓ ALL 10 CLICKS DONE (5 SIMPLE + 5 HUMANIZED)")
    log.info(f" Clicks: {tally['clicks']}/10 | Cycles: {tally['cycles']}")
    log.info(f"{'='*70}")
    
    send_email(tally)
    
    log.info(" Keeping container alive.")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
