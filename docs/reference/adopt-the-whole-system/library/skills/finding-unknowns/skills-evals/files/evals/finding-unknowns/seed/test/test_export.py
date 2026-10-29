"""Acceptance tests for the proposed export."""

import unittest

from src.service import proposed_export_response


class ExportAcceptanceTests(unittest.TestCase):
    def test_empty_export_keeps_header(self):
        content_type, body = proposed_export_response([])
        self.assertEqual(content_type, "text/csv; charset=utf-8")
        self.assertEqual("".join(body), "id,label\r\n")


if __name__ == "__main__":
    unittest.main()
