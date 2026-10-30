import unittest

from slugify import slugify


class SlugifyTests(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(slugify("Hello"), "hello")

    def test_spaces_become_dashes(self):
        self.assertEqual(slugify("rate limiter"), "rate-limiter")

    def test_leading_punctuation(self):
        self.assertEqual(slugify("...release notes"), "release-notes")

    def test_trailing_punctuation(self):
        self.assertEqual(slugify("Ship it!"), "ship-it")
