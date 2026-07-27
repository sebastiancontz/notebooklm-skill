import unittest
from collections import Counter

from scripts.ask_question import find_current_turn, normalize_text


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


if __name__ == "__main__":
    unittest.main()
