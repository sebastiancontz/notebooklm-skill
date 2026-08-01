import unittest
from collections import Counter

from scripts.ask_question import (
    find_current_turn,
    normalize_text,
    update_history_stability,
)
from scripts.config import NOTEBOOKLM_URL_PATTERN


class FindCurrentTurnTests(unittest.TestCase):
    before_turns = [
        {"index": 0, "prompt": "Historical question", "answer": "Historical answer"}
    ]
    before_counts = (1, 1)
    before_prompt_counter = Counter(
        normalize_text(turn["prompt"]) for turn in before_turns
    )

    def associate(self, turns, target_prompt="Submitted question"):
        return find_current_turn(
            turns,
            self.before_counts,
            self.before_prompt_counter,
            target_prompt,
        )

    def test_exact_prompt_match(self):
        expected = {
            "index": 1,
            "prompt": "Submitted question",
            "answer": "Current answer",
        }

        turn, association = self.associate(self.before_turns + [expected])

        self.assertEqual((turn, association), (expected, "text"))

    def test_position_fallback_when_both_counts_increase_by_one(self):
        expected = {
            "index": 1,
            "prompt": "Submitted question more_horiz",
            "answer": "Recovered answer",
        }

        turn, association = self.associate(self.before_turns + [expected])

        self.assertEqual((turn, association), (expected, "position"))

    def test_unchanged_prompt_count_means_not_sent(self):
        turn, association = self.associate(self.before_turns)

        self.assertIsNone(turn)
        self.assertEqual(association, "not_sent")

    def test_empty_response_is_pending(self):
        turns = self.before_turns + [
            {"index": 1, "prompt": "Submitted question more_horiz", "answer": ""}
        ]

        turn, association = self.associate(turns)

        self.assertIsNone(turn)
        self.assertEqual(association, "pending")


class NotebookLMUrlPatternTests(unittest.TestCase):
    def test_accepts_both_domains_and_rejects_lookalikes(self):
        for url in (
            "https://notebooklm.google.com/notebook/id",
            "https://notebook.google.com/notebook/id",
        ):
            self.assertRegex(url, NOTEBOOKLM_URL_PATTERN)

        for url in (
            "https://evil.example/?next=https://notebook.google.com/notebook/id",
            "https://notebook.google.com.evil.example/notebook/id",
        ):
            self.assertNotRegex(url, NOTEBOOKLM_URL_PATTERN)


class HistoryStabilityTests(unittest.TestCase):
    def test_transient_empty_history_does_not_settle(self):
        empty_snapshot = (Counter(), 0, 0)
        loaded_snapshot = (Counter({"Historical question": 10}), 10, 10)

        stable_polls, settled = update_history_stability(
            empty_snapshot, empty_snapshot, 0
        )
        self.assertEqual((stable_polls, settled), (1, False))

        stable_polls, settled = update_history_stability(
            empty_snapshot, loaded_snapshot, stable_polls
        )
        self.assertEqual((stable_polls, settled), (0, False))

        stable_polls, settled = update_history_stability(
            loaded_snapshot, loaded_snapshot, stable_polls
        )
        self.assertEqual((stable_polls, settled), (1, False))

        self.assertEqual(
            update_history_stability(loaded_snapshot, loaded_snapshot, stable_polls),
            (2, True),
        )


if __name__ == "__main__":
    unittest.main()
