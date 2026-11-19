import unittest

from generate import duration


class DurationTests(unittest.TestCase):
    def test_agent_budget(self):
        for seconds, expected in (
            (None, None),
            (0, None),
            (0.5, "0.5 seconds"),
            (1, "1 second"),
            (45, "45 seconds"),
            (60, "1 minute"),
            (90.0, "90 seconds"),
            (1800, "30 minutes"),
            (3600, "1 hour"),
            (3660, "1h 1m"),
            (7200, "2 hours"),
        ):
            with self.subTest(seconds=seconds):
                self.assertEqual(duration(seconds), expected)
