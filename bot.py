import os
import re
import time
import asyncio
import requests
from playwright.async_api import async_playwright

TIKTOK_USERNAME = os.environ["TIKTOK_USERNAME"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
HISTORY_FILE = "reposts_history.txt"
HEADLESS = True


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line for line in f.read().splitlines() if line)
    return set()


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(history)))


def send_discord_notification(video_url, username):
    payload = {
        "content": f"🚨 **Nowy repost od @{username}!**\n{video_url}"
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        if response.status_code in (200, 204):
            print("[Discord] Powiadomienie wysłane.")
        else:
            print(f"[Discord] Błąd: {response.status_code} {response.text[:200]}")
    except Exception as e:
        print(f"[Discord] Błąd sieci: {e}")


async def check_tiktok_reposts():
    url = f"https://www.tiktok.com/@{TIKTOK_USERNAME}"
    print(f"[{time.strftime('%X')}] Sprawdzam profil: {url}")

    api_reposts = []
    dom_reposts = []
    pending = []

    async def handle_response(response):
        if "repost/item_list" not in response.url:
            return
        try:
            data = await response.json()
        except Exception:
            return
        for item in data.get("itemList") or []:
            vid = item.get("id")
            author = item.get("author")
            if isinstance(author, dict):
                author = author.get("uniqueId")
            if vid and author:
                link = f"https://www.tiktok.com/@{author}/video/{vid}"
                if link not in api_reposts:
                    api_reposts.append(link)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()
        page.on("response", lambda r: pending.append(asyncio.create_task(handle_response(r))))

        tab_clicked = False
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)

            tab_candidates = [
                page.get_by_role("tab", name=re.compile(r"repost", re.I)),
                page.locator("[data-e2e='repost-tab']"),
                page.locator("[role='tab']:has-text('Repost')"),
                page.locator("p:has-text('Reposts')"),
            ]
            for tab in tab_candidates:
                try:
                    if await tab.count() > 0:
                        await tab.first.click(force=True, timeout=5000)
                        tab_clicked = True
                        print("[Bot] Kliknięto zakładkę repostów.")
                        break
                except Exception:
                    continue

            if not tab_clicked:
                print("[Ostrzeżenie] Nie znaleziono zakładki repostów.")
            else:
                await page.wait_for_timeout(5000)
                await page.mouse.wheel(0, 1500)
                await page.wait_for_timeout(2000)

            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if tab_clicked and not api_reposts:
                hrefs = await page.locator("a[href*='/video/']").evaluate_all(
                    "els => els.map(e => e.href)"
                )
                for href in hrefs:
                    clean = href.split("?")[0]
                    if clean not in dom_reposts:
                        dom_reposts.append(clean)

            if not api_reposts and not dom_reposts:
                await page.screenshot(path="debug.png")
                print("[Debug] Zapisano debug.png")

        except Exception as e:
            print(f"[Błąd] {e}")
        finally:
            await browser.close()

    return api_reposts or dom_reposts


async def main():
    history = load_history()
    is_first_run = len(history) == 0

    current_reposts = await check_tiktok_reposts()
    if not current_reposts:
        print("[System] Nic nie znaleziono - koniec.")
        return

    new_videos = [v for v in current_reposts if v not in history]

    if is_first_run:
        history.update(new_videos)
        save_history(history)
        print(f"[System] Baza startowa: {len(new_videos)} filmów, bez powiadomień.")
        return

    for v in reversed(new_videos):
        print(f"[System] Nowy repost: {v}")
        await asyncio.to_thread(send_discord_notification, v, TIKTOK_USERNAME)
        history.add(v)
    if new_videos:
        save_history(history)
    else:
        print("[System] Brak nowych repostów.")


if __name__ == "__main__":
    asyncio.run(main())
