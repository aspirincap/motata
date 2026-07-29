from __future__ import annotations

import unittest

from motata_cli._vendor.tiktok_business_api_sdk.python_sdk.business_api_client import models
from motata_cli._vendor.tiktok_business_api_sdk.python_sdk.business_api_client.api_client import ApiClient


class TikTokVendoredSdkTests(unittest.TestCase):
    def test_api_client_deserializes_model_string_without_global_package_symbol(self) -> None:
        client = ApiClient()

        payload = {
            "code": 0,
            "message": "OK",
            "request_id": "req-1",
            "data": {"hello": "world"},
        }

        result = client._ApiClient__deserialize(payload, "InlineResponse200")

        self.assertIsInstance(result, models.InlineResponse200)
        self.assertEqual(result.code, 0)
        self.assertEqual(result.message, "OK")
        self.assertEqual(result.request_id, "req-1")


if __name__ == "__main__":
    unittest.main()
