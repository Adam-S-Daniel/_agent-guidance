import os
import unittest

from src.changelog import render, subjects


class ChangelogTests(unittest.TestCase):
    def test_sections_from_a_real_history(self):
        repo = os.environ["CHANGELOG_REPO"]
        self.assertEqual(render(subjects(repo)),
                         "## Features\n- add the export command\n\n"
                         "## Fixes\n- keep empty sections out\n")


if __name__ == "__main__":
    unittest.main()
