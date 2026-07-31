#!/usr/bin/env python3
"""
Simple NotebookLM Question Interface
Based on MCP server implementation - simplified without sessions

Implements hybrid auth approach:
- Persistent browser profile (user_data_dir) for fingerprint consistency
- Manual cookie injection from state.json for session cookies (Playwright bug workaround)
See: https://github.com/microsoft/playwright/issues/36139
"""

import argparse
import sys
import time
import re
from collections import Counter
from pathlib import Path

from patchright.sync_api import sync_playwright

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from auth_manager import AuthManager
from notebook_manager import NotebookLibrary
from config import (
    DEFAULT_HEADLESS,
    DEFAULT_NOTEBOOK_ID,
    NOTEBOOKLM_DEBUG,
    NOTEBOOKLM_URL_PATTERN,
    QUERY_INPUT_SELECTORS,
    SHOW_BROWSER_DEFAULT,
)
from browser_utils import BrowserFactory, StealthUtils


# Follow-up reminder (adapted from MCP server for stateless operation)
# Since we don't have persistent sessions, we encourage comprehensive questions
FOLLOW_UP_REMINDER = (
    "\n\nEXTREMELY IMPORTANT: Is that ALL you need to know? "
    "You can always ask another question! Think about it carefully: "
    "before you reply to the user, review their original request and this answer. "
    "If anything is still unclear or missing, ask me another comprehensive question "
    "that includes all necessary context (since each question opens a new browser session)."
)


def normalize_text(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def preview(text, max_len=90):
    text = normalize_text(text)
    return text if len(text) <= max_len else text[:max_len - 3] + "..."


def count_turn_parts(turns):
    prompts = sum(1 for turn in turns if turn.get("prompt"))
    answers = sum(1 for turn in turns if turn.get("answer"))
    return prompts, answers


def find_current_turn(turns, before_counts, before_prompt_counter, target_prompt):
    """Associate the submitted prompt with its rendered turn without browser state."""
    before_prompts, before_answers = before_counts
    prompt_count, answer_count = count_turn_parts(turns)
    target_prompt = normalize_text(target_prompt)

    if prompt_count == before_prompts:
        return None, "not_sent"

    current_prompt_counter = Counter(
        normalize_text(turn.get("prompt"))
        for turn in turns
        if turn.get("prompt")
    )
    matching_turns = [
        turn for turn in turns
        if normalize_text(turn.get("prompt")) == target_prompt
    ]
    if (
        matching_turns
        and current_prompt_counter[target_prompt] > before_prompt_counter[target_prompt]
    ):
        turn = matching_turns[-1]
        return (turn, "text") if turn.get("answer") else (None, "pending")

    if (
        prompt_count == before_prompts + 1
        and answer_count == before_answers + 1
        and turns[-1].get("answer")
    ):
        return turns[-1], "position"

    if prompt_count == before_prompts + 1 and answer_count == before_answers:
        return None, "pending"

    return None, "unassociated"


def ask_notebooklm(
    question: str,
    notebook_url: str,
    headless: bool = True,
    debug: bool = False
) -> str:
    """
    Ask a question to NotebookLM

    Args:
        question: Question to ask
        notebook_url: NotebookLM notebook URL
        headless: Run browser in headless mode

    Returns:
        Answer text from NotebookLM
    """
    debug_enabled = debug or NOTEBOOKLM_DEBUG

    def debug_log(message: str):
        if debug_enabled:
            print(message)

    auth = AuthManager()

    if not auth.is_authenticated():
        print("⚠️ Not authenticated. Run: python auth_manager.py setup")
        return None

    debug_log(f"💬 Asking: {question}")
    debug_log(f"📚 Notebook: {notebook_url}")

    playwright = None
    context = None

    try:
        # Start playwright
        playwright = sync_playwright().start()

        # Launch persistent browser context using factory
        context = BrowserFactory.launch_persistent_context(
            playwright,
            headless=headless
        )

        # Navigate to notebook
        page = context.new_page()
        debug_log("  🌐 Opening notebook...")
        page.goto(notebook_url, wait_until="domcontentloaded")

        # Wait for NotebookLM
        page.wait_for_url(NOTEBOOKLM_URL_PATTERN, timeout=10000)

        # Wait for query input (MCP approach)
        debug_log("  ⏳ Waiting for query input...")
        query_element = None
        input_selector = None

        for selector in QUERY_INPUT_SELECTORS:
            try:
                query_element = page.wait_for_selector(
                    selector,
                    timeout=10000,
                    state="visible"  # Only check visibility, not disabled!
                )
                if query_element:
                    input_selector = selector
                    debug_log(f"  ✓ Found input: {selector}")
                    break
            except:
                continue

        if not query_element:
            print("  ❌ Could not find query input")
            return None

        def extract_turns():
            return page.evaluate("""() => {
                const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                return Array.from(document.querySelectorAll('.chat-message-pair'))
                    .map((pair, index) => {
                        const promptEl = pair.querySelector('.from-user-container .message-text-content');
                        const answerEl = pair.querySelector('.to-user-container .message-text-content');
                        return {
                            index,
                            prompt: clean(promptEl ? promptEl.innerText : ''),
                            answer: clean(answerEl ? answerEl.innerText : '')
                        };
                    })
                    .filter((turn) => turn.prompt || turn.answer);
            }""")

        before_turns = extract_turns()
        before_prompts, before_answers = count_turn_parts(before_turns)
        before_prompt_counter = Counter(
            normalize_text(turn.get("prompt"))
            for turn in before_turns
            if turn.get("prompt")
        )
        settle_deadline = time.time() + 15
        while time.time() < settle_deadline:
            time.sleep(1.5)
            now_turns = extract_turns()
            now_prompts, now_answers = count_turn_parts(now_turns)
            now_prompt_counter = Counter(
                normalize_text(turn.get("prompt"))
                for turn in now_turns
                if turn.get("prompt")
            )
            if (
                now_prompt_counter == before_prompt_counter
                and now_prompts == before_prompts
                and now_answers == before_answers
            ):
                break
            before_turns = now_turns
            before_prompts = now_prompts
            before_answers = now_answers
            before_prompt_counter = now_prompt_counter

        debug_log(f"  🔎 Before submit: prompts={before_prompts}, responses={before_answers}")

        # Type question (human-like, fast)
        debug_log("  ⏳ Typing question...")

        if not StealthUtils.human_type(page, input_selector, question):
            print("  ❌ Could not confirm question text before submit")
            return None

        def click_send_button():
            find_send_button = """() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const candidates = buttons.filter((button) => {
                    const label = button.getAttribute('aria-label') || '';
                    const icon = button.querySelector('mat-icon');
                    const iconText = icon
                        ? ((icon.getAttribute('fonticon') || icon.textContent) || '').trim()
                        : '';
                    return /send|submit/i.test(label)
                        || ['send', 'arrow_forward', 'arrow_upward'].includes(iconText);
                });
                return candidates.find((button) => button.getClientRects().length)
                    || candidates[0]
                    || null;
            }"""

            disabled_once = False
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    button = page.evaluate_handle(find_send_button).as_element()
                    if button:
                        if not button.is_enabled():
                            if disabled_once:
                                return "disabled"
                            disabled_once = True
                        else:
                            button.click(timeout=2000)
                            return "clicked"
                except Exception:
                    pass

                try:
                    page.locator(input_selector).first.click(timeout=1000)
                    page.keyboard.type(" ")
                    page.keyboard.press("Backspace")
                except Exception:
                    pass
                time.sleep(0.5)

            return "missing"

        # Submit
        debug_log("  📤 Submitting...")
        send_status = click_send_button()
        if send_status == "disabled":
            print(
                f"  ❌ NotebookLM rejected the input ({len(question)} characters): "
                "it probably exceeds the chat input limit"
            )
            return None
        if send_status == "missing":
            debug_log("  ⚠️ Send button not found/enabled; falling back to Enter")
            page.keyboard.press("Enter")

        # Small pause
        StealthUtils.random_delay(500, 1500)

        # Wait for response (MCP approach: poll for stable text)
        debug_log("  ⏳ Waiting for answer...")

        answer = None
        stable_count = 0
        last_text = None
        captured_turn = None
        last_turns = before_turns
        warned_positional_fallback = False
        placeholders = ("finding relevant info", "finding sources", "analyzing")
        target_prompt = normalize_text(question)
        submit_confirmation_deadline = time.time() + 10
        deadline = time.time() + 120  # 2 minutes timeout

        while time.time() < deadline:
            # Check if NotebookLM is still thinking (most reliable indicator)
            try:
                thinking_element = page.query_selector('div.thinking-message')
                if thinking_element and thinking_element.is_visible():
                    time.sleep(1)
                    continue
            except:
                pass

            turns = extract_turns()
            last_turns = turns
            current_turn, association = find_current_turn(
                turns,
                (before_prompts, before_answers),
                before_prompt_counter,
                target_prompt
            )
            if association == "not_sent":
                if time.time() >= submit_confirmation_deadline:
                    prompt_count, response_count = count_turn_parts(turns)
                    debug_log(
                        f"  🔎 After submit attempt: prompts={prompt_count}, "
                        f"responses={response_count}"
                    )
                    print(
                        "  ❌ Question was not sent to NotebookLM "
                        "(prompt count did not change)"
                    )
                    return None
                time.sleep(1)
                continue

            if association == "position" and not warned_positional_fallback:
                print(
                    "  ⚠️ Associated the latest answer by position because the "
                    "rendered prompt did not match the submitted text"
                )
                warned_positional_fallback = True

            if current_turn:
                text = normalize_text(current_turn.get("answer"))
                if text and not any(placeholder in text.lower() for placeholder in placeholders):
                    if text == last_text:
                        stable_count += 1
                        if stable_count >= 3:  # Stable for 3 polls
                            answer = text
                            captured_turn = current_turn
                            break
                    else:
                        stable_count = 0
                        last_text = text
                elif current_turn.get("answer"):
                    debug_log(
                        "  ↩️ Discarded placeholder answer candidate: "
                        f"index={current_turn.get('index')}, preview={preview(current_turn.get('answer'))}"
                    )
            elif association == "unassociated":
                prompt_count, response_count = count_turn_parts(turns)
                if prompt_count > before_prompts:
                    last_prompt = next(
                        (turn.get("prompt") for turn in reversed(turns) if turn.get("prompt")),
                        ""
                    )
                    debug_log(
                        "  ↩️ Discarded unassociated turn candidate: "
                        f"prompts={prompt_count}, responses={response_count}, "
                        f"last_prompt={preview(last_prompt)}"
                    )

            time.sleep(1)

        if not answer:
            prompt_count, response_count = count_turn_parts(last_turns)
            debug_log(f"  🔎 After timeout: prompts={prompt_count}, responses={response_count}")
            if prompt_count == before_prompts:
                print(
                    "  ❌ Question was not sent to NotebookLM "
                    "(prompt count did not change)"
                )
                return None
            print("  ❌ Could not confidently associate an answer with the latest prompt")
            return None

        prompt_count, response_count = count_turn_parts(last_turns)
        debug_log(f"  🔎 After submit: prompts={prompt_count}, responses={response_count}")
        if captured_turn:
            debug_log(
                "  🔎 Captured turn: "
                f"index={captured_turn.get('index')}, "
                f"prompt={preview(captured_turn.get('prompt'))}, "
                f"answer={preview(answer)}"
            )
            other_answers = [
                turn for turn in last_turns
                if turn.get("answer") and turn.get("index") != captured_turn.get("index")
            ]
            if other_answers:
                longest_other = max(other_answers, key=lambda turn: len(turn.get("answer", "")))
                if len(longest_other.get("answer", "")) > len(answer):
                    debug_log(
                        "  ↩️ Discarded historical answer candidate: "
                        f"index={longest_other.get('index')}, "
                        f"preview={preview(longest_other.get('answer'))}"
                    )

        debug_log("  ✅ Got answer!")
        try:
            auth._save_browser_state(context, quiet=not debug_enabled)
        except Exception as e:
            print(f"  ⚠️ Could not refresh saved session (non-fatal): {e}")

        # Add follow-up reminder to encourage Claude to ask more questions
        return answer + FOLLOW_UP_REMINDER

    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

    finally:
        # Always clean up
        if context:
            try:
                context.close()
            except:
                pass

        if playwright:
            try:
                playwright.stop()
            except:
                pass


def main():
    parser = argparse.ArgumentParser(description='Ask NotebookLM a question')

    parser.add_argument('--question', required=True, help='Question to ask')
    parser.add_argument('--notebook-url', help='NotebookLM notebook URL')
    parser.add_argument('--notebook-id', help='Notebook ID from library')
    parser.add_argument('--show-browser', action='store_true', help='Show browser')
    parser.add_argument('--debug', action='store_true', help='Show detailed capture diagnostics')

    args = parser.parse_args()

    debug_enabled = args.debug or NOTEBOOKLM_DEBUG

    def debug_log(message: str):
        if debug_enabled:
            print(message)

    # Resolve notebook URL
    notebook_url = args.notebook_url
    notebook_id = args.notebook_id or DEFAULT_NOTEBOOK_ID
    notebook_id_used = None
    library = None

    if not notebook_url and notebook_id:
        library = NotebookLibrary()
        notebook = library.get_notebook(notebook_id)
        if notebook:
            notebook_url = notebook['url']
            notebook_id_used = notebook_id
        else:
            if args.notebook_id:
                print(f"❌ Notebook '{args.notebook_id}' not found")
            else:
                print(f"❌ DEFAULT_NOTEBOOK_ID '{notebook_id}' (from .env) not found in library")
                print("   Fix or remove DEFAULT_NOTEBOOK_ID, or pass --notebook-id / --notebook-url")
            return 1

    if not notebook_url:
        # Check for active notebook first
        if library is None:
            library = NotebookLibrary()
        active = library.get_active_notebook()
        if active:
            notebook_url = active['url']
            notebook_id_used = active['id']
            print(f"📚 Using active notebook: {active['name']}")
        else:
            # Show available notebooks
            notebooks = library.list_notebooks()
            if notebooks:
                print("\n📚 Available notebooks:")
                for nb in notebooks:
                    mark = " [ACTIVE]" if nb.get('id') == library.active_notebook_id else ""
                    print(f"  {nb['id']}: {nb['name']}{mark}")
                print("\nSpecify with --notebook-id or set active:")
                print("python scripts/run.py notebook_manager.py activate --id ID")
            else:
                print("❌ No notebooks in library. Add one first:")
                print("python scripts/run.py notebook_manager.py add --url URL --name NAME --description DESC --topics TOPICS")
            return 1

    # Ask the question
    answer = ask_notebooklm(
        question=args.question,
        notebook_url=notebook_url,
        headless=not (args.show_browser or SHOW_BROWSER_DEFAULT or not DEFAULT_HEADLESS),
        debug=args.debug
    )

    if answer:
        if notebook_id_used and library:
            try:
                library.increment_use_count(notebook_id_used)
            except Exception as e:
                debug_log(f"⚠️ Could not update notebook use count: {e}")
        print("\n" + "=" * 60)
        print(f"Question: {args.question}")
        print("=" * 60)
        print()
        print(answer)
        print()
        print("=" * 60)
        return 0
    else:
        print("\n❌ Failed to get answer")
        return 1


if __name__ == "__main__":
    sys.exit(main())
