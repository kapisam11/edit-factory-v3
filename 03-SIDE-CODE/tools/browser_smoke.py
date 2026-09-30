"""Real browser smoke for the Edit Factory dashboard.

Usage:
    python browser_smoke.py http://127.0.0.1:5000 TOKEN
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main(base_url: str, token: str) -> None:
    failures: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        page_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        await page.goto(base_url.rstrip("/") + "/", wait_until="networkidle")
        if "/login" not in page.url:
            failures.append(f"unauthenticated root did not redirect to login: {page.url}")

        await page.locator('input[name="token"]').fill(token)
        await page.get_by_role("button", name="Sign in").click()
        await page.wait_for_load_state("networkidle")

        if "/login" in page.url:
            failures.append("login did not reach dashboard")

        await page.locator("#workflow").select_option("v3")
        if not await page.locator("#v3Controls").is_visible():
            failures.append("V3 controls remained hidden after selecting V3")

        await page.locator("#platform").select_option("tiktok")
        if await page.locator("#target_seconds").get_attribute("max") != "180":
            failures.append("Tiktok duration range was not updated")

        await page.locator("#topic").fill("Browser smoke test")
        await page.locator("#audience").fill("short-form viewers")
        await page.locator("#context").fill("UI smoke only")

        await page.screenshot(path=str(Path("browser-smoke.png")), full_page=True)
        await browser.close()

    if page_errors:
        failures.append("page errors: " + " | ".join(page_errors))
    if console_errors:
        failures.append("console errors: " + " | ".join(console_errors))

    if failures:
        raise SystemExit("\n".join(failures))
    print("Browser smoke passed")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: browser_smoke.py BASE_URL TOKEN")
    asyncio.run(main(sys.argv[1], sys.argv[2]))