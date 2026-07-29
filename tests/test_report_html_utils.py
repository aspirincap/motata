from __future__ import annotations

import unittest

from motata_cli.report.html_utils import preview_cell, report_table, report_table_css


class ReportHtmlUtilsTests(unittest.TestCase):
    def test_table_supports_named_classes_for_width_rules(self) -> None:
        html = report_table(["Preview", "Name"], [["<b>x</b>", "demo"]], class_name="creative-table")

        self.assertIn('class="creative-table"', html)
        self.assertIn("<th>Preview</th>", html)

    def test_css_contains_global_overflow_and_creative_width_rules(self) -> None:
        css = report_table_css()

        self.assertIn(".table-wrap { overflow:auto;", css)
        self.assertIn("overflow-wrap:anywhere", css)
        self.assertIn("word-break:break-word", css)
        self.assertIn(".creative-table { min-width:1980px; }", css)
        self.assertIn("white-space:nowrap", css)

    def test_preview_unavailable_keeps_fixed_preview_box(self) -> None:
        html = preview_cell("", "")

        self.assertIn("preview-cell", html)
        self.assertIn("preview-img preview-placeholder", html)
        self.assertIn("Unavailable", html)


if __name__ == "__main__":
    unittest.main()
