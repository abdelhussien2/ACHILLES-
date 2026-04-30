"""
sourhunter3.py - Amazon scraper via Playwright + BrightData Scraping Browser.

Connects over websocket (port 9222) which is BrightData's primary,
most stable integration. No more HTTP timeout deaths.
"""

import os, re, json, time, random, logging, asyncio
from datetime import datetime
from playwright.async_api import async_playwright

log = logging.getLogger("tracker")


def pause(lo=0.5, hi=2.5):
    return random.uniform(lo, hi) * 1000  # Playwright uses milliseconds


async def create_browser():
    """Connect to BrightData Scraping Browser via websocket."""
    t = time.time()
    log.info("  [DRIVER] Connecting via websocket...")

    user = os.getenv("SBR_USERNAME", "")
    pw = os.getenv("SBR_PASSWORD", "")
    if not user or not pw:
        raise ValueError("SBR_USERNAME and SBR_PASSWORD not set")

    url = f"wss://{user}:{pw}@brd.superproxy.io:9222"

    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp(url)
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = context.pages[0] if context.pages else await context.new_page()

    # Block ONLY large product images — keeps tracking pixels + scripts intact.
    # Product images are ~200-500KB each, 50+ per search page = 90% of bandwidth.
    # Amazon tracking pixels (fls-na, unagi-na) are tiny and use different URLs.
    async def block_heavy(route):
        url = route.request.url
        # Block product listing images (the big ones)
        if "m.media-amazon.com/images/I/" in url:
            return await route.abort()
        # Block fonts
        if any(url.endswith(ext) for ext in [".woff", ".woff2", ".ttf", ".eot"]):
            return await route.abort()
        # Block video
        if any(url.endswith(ext) for ext in [".mp4", ".webm"]):
            return await route.abort()
        await route.continue_()

    await page.route("**/*", block_heavy)

    # Set longer timeouts
    page.set_default_timeout(60000)  # 60s
    page.set_default_navigation_timeout(90000)  # 90s

    elapsed_t = round(time.time() - t, 1)
    log.info(f"  [DRIVER] Connected ({elapsed_t}s)")
    return playwright, browser, page


async def handle_interstitial(page):
    """Handle Amazon's 'Continue shopping' bot check."""
    for _ in range(3):
        try:
            content = await page.content()
            if "continue shopping" in content.lower() and "/s?" not in page.url:
                log.info("  [INTERSTITIAL] Bot check detected...")
                btn = page.locator("text=Continue shopping").first
                if await btn.is_visible():
                    await btn.click()
                    log.info("  [INTERSTITIAL] Clicked 'Continue shopping'")
                    await page.wait_for_timeout(3000)
            else:
                return
        except:
            return


async def popups(page):
    """Dismiss Amazon popups."""
    await handle_interstitial(page)
    for sel in ["#sp-cc-accept", "input[data-action-type='DISMISS']", ".a-modal-close"]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1000):
                await el.click()
                await page.wait_for_timeout(500)
        except:
            pass


async def search(page, query):
    """Search Amazon by typing in search box like a real user."""
    t = time.time()
    log.info(f"  [SEARCH] \"{query}\"")

    # Go to Amazon homepage first
    try:
        await page.goto("https://www.amazon.com/", wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        log.warning(f"  [SEARCH] Page load issue: {str(e)[:60]}")

    await page.wait_for_timeout(int(pause(2, 4)))
    await handle_interstitial(page)
    await popups(page)

    # Random scroll on homepage like a real user browsing
    try:
        await page.evaluate(f"window.scrollTo(0, {random.randint(100, 400)})")
        await page.wait_for_timeout(int(pause(1, 2)))
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(int(pause(0.5, 1.5)))
    except:
        pass

    # Find search box, click it with mouse movement
    try:
        search_box = page.locator("#twotabsearchtextbox").first
        await search_box.wait_for(state="visible", timeout=15000)

        # Move mouse to search box with realistic motion
        box = await search_box.bounding_box()
        if box:
            # Click at slightly random position within box
            target_x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
            target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await page.mouse.move(target_x, target_y, steps=random.randint(15, 30))
            await page.wait_for_timeout(int(pause(0.3, 0.8)))

        await search_box.click()
        await page.wait_for_timeout(int(pause(0.5, 1.2)))

        # Type with human-like delays per character
        for char in query:
            await search_box.type(char, delay=random.randint(60, 180))
            # Occasional micro-pauses between words
            if char == " " and random.random() < 0.3:
                await page.wait_for_timeout(random.randint(150, 400))

        # Brief pause before pressing enter (like reading the suggestion)
        await page.wait_for_timeout(int(pause(0.4, 1.2)))
        await page.keyboard.press("Enter")
        log.info(f"  [SEARCH] Typed and submitted")
    except Exception as e:
        log.warning(f"  [SEARCH] Typing failed, falling back to URL: {str(e)[:60]}")
        url = f"https://www.amazon.com/s?k={query.replace(' ', '+')}"
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)

    await page.wait_for_timeout(int(pause(2, 4)))
    await handle_interstitial(page)
    await popups(page)

    # Wait for results
    try:
        await page.wait_for_selector(
            "div.s-main-slot div[data-component-type='s-search-result']",
            timeout=30000)
    except:
        # Maybe interstitial appeared
        await handle_interstitial(page)
        try:
            await page.wait_for_selector(
                "div.s-main-slot div[data-component-type='s-search-result']",
                timeout=15000)
        except:
            elapsed_t = round(time.time() - t, 1)
            log.error(f"  [SEARCH] No results ({elapsed_t}s)")
            return []

    await page.wait_for_timeout(int(pause(1, 2)))
    await popups(page)

    # Wait for sponsored labels
    for _ in range(16):
        try:
            text = await page.locator("div.s-main-slot").inner_text()
            if "sponsored" in text.lower():
                log.info("  [SEARCH] Sponsored labels present")
                break
        except:
            pass
        await page.wait_for_timeout(500)

    # Parse results
    cards = await page.locator(
        "div.s-main-slot div[data-component-type='s-search-result'], "
        "div.s-main-slot div[data-component-type='s-impression-counter']"
    ).all()

    seen = set()
    results = []
    for card in cards:
        asin = await card.get_attribute("data-asin") or ""
        if not asin:
            try:
                inner = card.locator("[data-asin]").first
                asin = await inner.get_attribute("data-asin") or ""
            except:
                pass
        if asin and asin in seen:
            continue
        if asin:
            seen.add(asin)
        try:
            title = await card.locator("h2 span").first.inner_text()
        except:
            title = "?"
        results.append({"i": len(results)+1, "asin": asin, "title": title, "el": card})

    elapsed_t = round(time.time() - t, 1)
    log.info(f"  [SEARCH] {len(results)} results ({elapsed_t}s)")
    for r in results[:5]:
        log.info(f"    {r['i']}. [{r['asin']}] {r['title'][:55]}")
    return results


async def is_sponsored(page, asin):
    """Check for Sponsored markers inside the ASIN card. With full debug."""
    js = """
    (asin) => {
        const el = document.querySelector('[data-asin="'+asin+'"]');
        if(!el) return {ok:false, why:'element not found', debug:{}};

        const debug = {
            classes: (el.className||'').substring(0, 200),
            hasAdHolder: (el.className||'').includes('AdHolder'),
            hasSponsoredSpan: false,
            hasSponsoredClass: !!el.querySelector('.puis-sponsored-label-text'),
            allSpanTexts: [],
            innerHTMLsnippet: el.innerHTML.substring(0, 500)
        };

        // Collect all aria-hidden spans to see what's there
        const spans = el.querySelectorAll('span[aria-hidden="true"].a-color-base');
        for(const s of spans){
            debug.allSpanTexts.push(s.textContent.trim().substring(0, 30));
            if(s.textContent.trim()==='Sponsored'){
                debug.hasSponsoredSpan = true;
                return {ok:true, why:'Sponsored span found', debug:debug};
            }
        }

        if(debug.hasAdHolder)
            return {ok:true, why:'AdHolder class', debug:debug};

        if(debug.hasSponsoredClass)
            return {ok:true, why:'puis-sponsored-label-text', debug:debug};

        // Check if "Sponsored" appears ANYWHERE in the card text
        const allText = el.innerText || '';
        debug.hasSponsoredAnywhere = allText.includes('Sponsored');

        return {ok:false, why:'no sponsored markers', debug:debug};
    }
    """
    try:
        r = await page.evaluate(js, asin)
        log.info(f"  [SPONSORED] {r.get('why','')}")
        d = r.get("debug", {})
        log.info(f"  [SPONSORED] classes: {d.get('classes','')[:100]}")
        log.info(f"  [SPONSORED] AdHolder: {d.get('hasAdHolder')} | SponsoredSpan: {d.get('hasSponsoredSpan')} | SponsoredClass: {d.get('hasSponsoredClass')}")
        log.info(f"  [SPONSORED] Spans found: {d.get('allSpanTexts', [])}")
        log.info(f"  [SPONSORED] 'Sponsored' anywhere in card text: {d.get('hasSponsoredAnywhere', '?')}")
        return r.get("ok", False)
    except Exception as e:
        log.warning(f"  [SPONSORED] Error: {e}")
        return False


async def click_product(page, card, asin):
    """Click product with realistic mouse movement and position."""
    for name, sel in [
        ("title", "h2 a"), ("image", ".s-product-image-container a"),
        ("asin-link", f"a[href*='/dp/{asin}']"), ("any-dp", "a[href*='/dp/']"),
    ]:
        try:
            el = card.locator(sel).first
            if await el.is_visible(timeout=2000):
                log.info(f"  [CLICK] {name}")

                # Get element bounding box for realistic click positioning
                box = await el.bounding_box()
                if box:
                    # Random position within element (not always center)
                    target_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
                    target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)

                    # Move mouse with multiple steps (curved path)
                    await page.mouse.move(target_x, target_y, steps=random.randint(20, 40))
                    # Hover briefly like a human reading
                    await page.wait_for_timeout(random.randint(300, 1200))
                    # Click at the position
                    await page.mouse.click(target_x, target_y)
                else:
                    await el.click()

                return True, name
        except:
            continue
    return False, None


async def screenshot(page, name):
    """Save screenshot."""
    try:
        os.makedirs("results", exist_ok=True)
        path = f"results/{datetime.utcnow().strftime('%H%M%S')}_{name}.png"
        await page.screenshot(path=path, full_page=False)
        log.info(f"  [SS] {path}")
        return path
    except Exception as e:
        log.warning(f"  [SS] Failed: {str(e)[:50]}")
        return None
