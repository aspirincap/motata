import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("tiktok_doc_watch", Path(__file__).resolve().parents[1] / "scripts/check_tiktok_docs.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ContractWatchTests(unittest.TestCase):
    def test_portal_shell_and_login_do_not_count_as_contracts(self):
        for raw in ('<html>Login</html>', '<script>advertiser_id = "a";</script>', '{"error":"login required"}'):
            with self.assertRaises(ValueError):
                module.extract_contract(raw)

    def test_structured_contract_normalizes_key_order(self):
        a = module.extract_contract('{"paths":{}, "parameters":[]}')
        b = module.extract_contract('{"parameters":[], "paths":{}}')
        self.assertEqual(a, b)

    def test_unavailable_source_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'test.txt'
            target.write_text('previous-readable-contract')
            response = Mock()
            response.status_code = 200
            response.iter_content.return_value = [b'<html>Loading</html>']
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            session = Mock()
            session.get.return_value = context
            with patch.object(module, 'SOURCES', {'test': 'https://business-api.tiktok.com/portal/docs/test'}):
                result = module.watch(directory, session=session)
            self.assertEqual(result['sources'][0]['status'], 'source_unavailable')
            self.assertEqual(target.read_text(), 'previous-readable-contract')
