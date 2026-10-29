import unittest

from src.sequence import next_value


class SequenceTests(unittest.TestCase):
    def test_next_value(self):
        self.assertEqual(next_value(41), 42)


if __name__ == "__main__":
    unittest.main()
