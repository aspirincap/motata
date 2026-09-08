from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from motata_cli.__main__ import build_parser
from motata_cli.common.errors import CliError
from motata_cli.report.commands import command_render_gmv_max


class RenderCliTests(unittest.TestCase):
    def test_packaged_renderer_is_offline_and_displays_degraded_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'current_gmv_max_account.json').write_text(json.dumps({'rows': []}))
            (root / 'manifest.json').write_text(json.dumps({'sources': [{'status': 'success'}, {'status': 'failed', 'error': 'denied'}]}))
            args = build_parser().parse_args(['report', 'render-gmv-max', '--run-dir', directory])
            with patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')), patch(
                'motata_cli.report.gmv_max_html.cache_remote_images', side_effect=AssertionError('No image downloads')
            ), patch('motata_cli.report.gmv_max_html.subprocess.run', side_effect=AssertionError('No external resolver')):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = command_render_gmv_max(args)
            self.assertEqual(code, 3)
            html = (root / 'report.html').read_text()
            self.assertIn('partial_success', html)
            self.assertIn('role="alert"', html)

    def test_refuses_non_gmv_run_and_source_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(['report', 'render-gmv-max', '--run-dir', directory])
            with self.assertRaises(CliError):
                command_render_gmv_max(args)
            source = Path(directory) / 'current_gmv_max_account.json'
            source.write_text('{"rows": []}')
            args.out = str(source)
            with self.assertRaises(CliError):
                command_render_gmv_max(args)
            self.assertEqual(source.read_text(), '{"rows": []}')


if __name__ == '__main__':
    unittest.main()
