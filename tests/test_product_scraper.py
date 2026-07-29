from __future__ import annotations

import unittest
from unittest.mock import patch

from motata_cli.product.scraper import clean_appstore_name, scrape_appstore, scrape_product


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        encoding: str = "iso-8859-1",
        apparent_encoding: str = "utf-8",
        url: str = "https://example.com",
    ) -> None:
        self._body = body
        self.encoding = encoding
        self.apparent_encoding = apparent_encoding
        self.url = url
        self.headers = {}

    @property
    def text(self) -> str:
        return self._body.decode(self.encoding, errors="replace")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        raise NotImplementedError


class _FakeJsonResponse(_FakeResponse):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(b"{}", encoding="utf-8", apparent_encoding="utf-8")
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class ProductScraperTests(unittest.TestCase):
    def test_clean_appstore_name_removes_store_suffix(self) -> None:
        self.assertEqual(clean_appstore_name("\u200eChatGPT App - App Store"), "ChatGPT")
        self.assertEqual(clean_appstore_name("ChatGPT on the App Store"), "ChatGPT")

    @patch("motata_cli.product.scraper.requests.get")
    def test_scrape_appstore_prefers_store_title_over_review_heading(self, mock_get) -> None:
        html = b"""
        <html>
          <head>
            <title>\xe2\x80\x8eChatGPT App - App Store</title>
            <meta property="og:title" content="ChatGPT App - App Store" />
            <meta property="og:image" content="https://example.com/icon.png" />
          </head>
          <body>
            <section>
              <h1>Amazing for college</h1>
              <h2>Ratings &amp; Reviews</h2>
            </section>
          </body>
        </html>
        """
        mock_get.side_effect = [
            _FakeJsonResponse(
                {
                    "resultCount": 1,
                    "results": [
                        {
                            "trackName": "ChatGPT",
                            "artworkUrl512": "https://example.com/icon.png",
                            "screenshotUrls": [
                                "https://example.com/shot-1.png",
                                "https://example.com/shot-2.png",
                            ],
                        }
                    ],
                }
            ),
            _FakeResponse(html),
        ]

        result = scrape_appstore("https://apps.apple.com/us/app/chatgpt/id6448311069")

        self.assertEqual(result["name"], "ChatGPT")
        self.assertEqual(
            result["images"],
            [
                "https://example.com/icon.png",
                "https://example.com/shot-1.png",
                "https://example.com/shot-2.png",
            ],
        )

    @patch("motata_cli.product.scraper.requests.get")
    def test_itunes_url_uses_appstore_lookup_and_keeps_genres(self, mock_get) -> None:
        mock_get.return_value = _FakeJsonResponse(
            {
                "resultCount": 1,
                "results": [
                    {
                        "trackName": "X-Clash: Survival Challenge",
                        "description": "A casual survival game.",
                        "primaryGenreName": "Games",
                        "genres": ["Games", "Casual", "Strategy"],
                        "sellerName": "9Z Games HK",
                        "artworkUrl512": "https://example.com/icon.png",
                    }
                ],
            }
        )

        result = scrape_product("http://itunes.apple.com/app/id6744812012")

        self.assertEqual(result["url_type"], "appstore")
        self.assertEqual(result["name"], "X-Clash: Survival Challenge")
        self.assertEqual(result["primary_genre"], "Games")
        self.assertEqual(result["genres"], ["Games", "Casual", "Strategy"])
        called_url = mock_get.call_args.args[0]
        self.assertIn("country=us", called_url)

    @patch("motata_cli.product.scraper.requests.get")
    def test_w2a_redirect_to_appstore_is_scraped_as_appstore(self, mock_get) -> None:
        mock_get.side_effect = [
            _FakeResponse(b"<a>Found</a>", url="https://apps.apple.com/app/id1269972832?mt=8"),
            _FakeJsonResponse(
                {
                    "resultCount": 1,
                    "results": [
                        {
                            "trackName": "Femometer Fertility Tracker",
                            "primaryGenreName": "Health & Fitness",
                            "genres": ["Health & Fitness", "Medical"],
                            "artworkUrl512": "https://example.com/femometer.png",
                        }
                    ],
                }
            ),
        ]

        result = scrape_product("https://app.adjust.com/1lwap8c2")

        self.assertEqual(result["url_type"], "appstore")
        self.assertEqual(result["url"], "https://apps.apple.com/app/id1269972832?mt=8")
        self.assertEqual(result["name"], "Femometer Fertility Tracker")


if __name__ == "__main__":
    unittest.main()
