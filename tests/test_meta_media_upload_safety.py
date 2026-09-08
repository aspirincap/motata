"""Regression coverage for unconfirmed chunked-upload responses (no network)."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from motata_cli.common.errors import CliError
from motata_cli.meta.services import media


class ChunkedUploadResponseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "video.mp4"
        self.path.write_bytes(b"abcdef")
        self.start = {"upload_session_id": "session", "video_id": "video", "start_offset": "0", "end_offset": "3"}

    def upload(self, responses):
        self.client = Mock()
        self.client.post.side_effect = responses
        return media.chunked_upload_video(self.client, "123", self.path, name=None, title=None)

    def test_complete_upload_confirms_all_chunks_and_finish(self):
        result = self.upload([self.start, {"start_offset": "3", "end_offset": "6"},
                              {"start_offset": "6", "end_offset": "6"}, {"success": True}])
        self.assertEqual(result["id"], "video")
        calls = self.client.post.call_args_list
        self.assertEqual([call.kwargs["data"]["upload_phase"] for call in calls], ["start", "transfer", "transfer", "finish"])
        self.assertEqual([call.kwargs["files"]["video_file_chunk"][1] for call in calls[1:3]], [b"abc", b"def"])

    def test_invalid_start_cannot_skip_or_read_beyond_file(self):
        for start, end in ((1, 3), (0, 7), (0, 0)):
            with self.subTest(start=start, end=end), self.assertRaises(CliError):
                self.upload([{**self.start, "start_offset": str(start), "end_offset": str(end)}])
            self.client.post.assert_called_once()

    def test_unconfirmed_window_never_replays_or_finishes(self):
        for start, end in ((0, 3), (1, 3), (4, 6), (3, 2), (3, 7), (3, 3)):
            with self.subTest(start=start, end=end), self.assertRaises(CliError):
                self.upload([self.start, {"start_offset": str(start), "end_offset": str(end)}])
            self.assertEqual(self.client.post.call_count, 2)

    def test_start_video_id_is_not_proof_of_finish_success(self):
        for finish in ({}, {"success": False}, {"success": "true"}, {"video_id": "video"}):
            with self.subTest(finish=finish), self.assertRaises(CliError):
                self.upload([{**self.start, "end_offset": "6"}, {"start_offset": "6", "end_offset": "6"}, finish])
            self.assertEqual(self.client.post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
