from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from motata_cli.__main__ import build_parser
from motata_cli.meta.output import configure_output, print_output


class MetaParserOutputTests(unittest.TestCase):
    def test_meta_parser_accepts_global_output_and_debug_flags(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "meta",
                "--output",
                "plain",
                "--debug",
                "campaigns",
                "list",
                "--account-id",
                "123",
                "--access-token",
                "demo-token",
            ]
        )

        self.assertEqual(args.output, "plain")
        self.assertTrue(args.debug)


class MetaOutputTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_output(None)

    def test_default_json_output_keeps_indented_json(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            configure_output(None)
            print_output({"id": "123"}, as_json=True)

        self.assertEqual(buffer.getvalue(), '{\n  "id": "123"\n}\n')

    def test_plain_output_overrides_legacy_json_mode(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            configure_output("plain")
            print_output({"id": "123"}, as_json=True)

        self.assertEqual(buffer.getvalue(), '{"id":"123"}\n')

    def test_table_output_renders_row_headers(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            configure_output("table")
            print_output([{"id": "123", "name": "demo"}], as_json=True)

        text = buffer.getvalue()
        self.assertIn("id", text)
        self.assertIn("name", text)
        self.assertIn("123", text)
        self.assertIn("demo", text)


if __name__ == "__main__":
    unittest.main()
