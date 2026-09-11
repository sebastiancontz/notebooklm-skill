"""
Browser Utilities for NotebookLM Skill
Handles browser launching, stealth features, and common interactions
"""

import json
import time
import random
from typing import Optional, List

from patchright.sync_api import Playwright, BrowserContext, Page
from config import BROWSER_PROFILE_DIR, STATE_FILE, BROWSER_ARGS, USER_AGENT


class BrowserFactory:
    """Factory for creating configured browser contexts"""

    @staticmethod
    def launch_persistent_context(
        playwright: Playwright,
        headless: bool = True,
        user_data_dir: str = str(BROWSER_PROFILE_DIR)
    ) -> BrowserContext:
        """
        Launch a persistent browser context with anti-detection features
        and cookie workaround.
        """
        # Launch persistent context
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",  # Use real Chrome
            headless=headless,
            no_viewport=True,
            ignore_default_args=["--enable-automation"],
            user_agent=USER_AGENT,
            args=BROWSER_ARGS
        )

        # Cookie Workaround for Playwright bug #36139
        # Session cookies (expires=-1) don't persist in user_data_dir automatically
        BrowserFactory._inject_cookies(context)

        return context

    @staticmethod
    def _inject_cookies(context: BrowserContext):
        """Inject cookies from state.json if available"""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    if 'cookies' in state and len(state['cookies']) > 0:
                        context.add_cookies(state['cookies'])
                        # print(f"  🔧 Injected {len(state['cookies'])} cookies from state.json")
            except Exception as e:
                print(f"  ⚠️  Could not load state.json: {e}")


class StealthUtils:
    """Human-like interaction utilities"""

    @staticmethod
    def random_delay(min_ms: int = 100, max_ms: int = 500):
        """Add random delay"""
        time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    @staticmethod
    def human_type(page: Page, selector: str, text: str, wpm_min: int = 320, wpm_max: int = 480) -> bool:
        """Set a query value and confirm that Angular observed the change."""
        deadline = time.time() + 30
        attempt = 0
        last_error = None
        last_state = None

        while time.time() < deadline:
            attempt += 1
            try:
                loc = page.locator(selector).first
                loc.wait_for(state="visible", timeout=5000)
                # Focusing in page context cannot be intercepted by Angular's overlay.
                loc.evaluate("""(element, value) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    element.focus();
                    setter.call(element, value);
                    element.dispatchEvent(new InputEvent('input', {
                        bubbles: true, inputType: 'insertText', data: value
                    }));
                    element.dispatchEvent(new Event('change', {bubbles: true}));
                }""", text)
                last_state = loc.evaluate("""element => ({
                    value: element.value,
                    classes: Array.from(element.classList),
                })""")
                if (
                    last_state["value"].strip() == text.strip()
                    and "ng-dirty" in last_state["classes"]
                ):
                    return True
            except Exception as e:
                last_error = e
            time.sleep(0.6)

        print(
            f"⚠️ human_type failed after {attempt} attempts: {last_error}; "
            f"input state={last_state}"
        )
        return False

    @staticmethod
    def realistic_click(page: Page, selector: str):
        """Click with realistic movement"""
        element = page.query_selector(selector)
        if not element:
            return

        # Optional: Move mouse to element (simplified)
        box = element.bounding_box()
        if box:
            x = box['x'] + box['width'] / 2
            y = box['y'] + box['height'] / 2
            page.mouse.move(x, y, steps=5)

        StealthUtils.random_delay(100, 300)
        element.click()
        StealthUtils.random_delay(100, 300)
