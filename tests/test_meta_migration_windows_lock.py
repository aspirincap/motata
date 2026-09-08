"""Exercise the Windows backend on every host; recovery tests also use real locks."""
from __future__ import annotations

import errno
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from motata_cli.meta.services import migration_state


class WindowsMigrationStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "job.json"
        self.msvcrt = Mock(LK_NBLCK=2, LK_UNLCK=0)
        spec = importlib.util.spec_from_file_location("windows_migration_state", migration_state.__file__)
        self.state = importlib.util.module_from_spec(spec)
        # No fcntl available, just as on Windows. Do not alter os.name/Path's host type.
        with patch.object(sys, "platform", "win32"), patch.dict(
            sys.modules, {"fcntl": None, "msvcrt": self.msvcrt}
        ):
            spec.loader.exec_module(self.state)

    def test_checkpoint_uses_explicit_utf8_for_unicode(self):
        payload = {'message': '中文广告迁移 🌍'}
        with patch.object(self.state.os, 'fdopen', wraps=os.fdopen) as opening:
            self.state.atomic_json(self.path, payload)
        self.assertEqual(opening.call_args.kwargs['encoding'], 'utf-8')
        self.assertEqual(json.loads(self.path.read_bytes().decode('utf-8')), payload)

    def test_import_without_fcntl(self):
        self.assertTrue(self.state._WINDOWS)
        self.assertIs(self.state.msvcrt, self.msvcrt)
        self.assertFalse(hasattr(self.state, "fcntl"))

    def test_lock_and_unlock_same_byte_even_for_existing_file(self):
        self.path.with_suffix(".lock").write_bytes(b"existing lock file")
        calls = []

        def locking(fd, mode, count):
            calls.append((fd, mode, count, os.lseek(fd, 0, os.SEEK_CUR)))

        self.msvcrt.locking.side_effect = locking
        with self.state.job_lock(self.path):
            self.assertEqual(len(calls), 1)
        self.assertEqual(calls, [(calls[0][0], 2, 1, 0), (calls[0][0], 0, 1, 0)])
        self.assertEqual(self.path.with_suffix(".lock").read_bytes(), b"existing lock file")
        with self.assertRaises(OSError):
            os.fstat(calls[0][0])  # Descriptor is closed after unlocking.

    def test_contention_fails_closed_without_unlocking(self):
        for code in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            with self.subTest(errno=code):
                self.msvcrt.locking.reset_mock()
                self.msvcrt.locking.side_effect = OSError(code, "locked")
                with self.assertRaisesRegex(self.state.MigrationStateError, "already running"):
                    with self.state.job_lock(self.path):
                        self.fail("A competing writer entered")
                self.msvcrt.locking.assert_called_once()

    def test_unexpected_lock_error_is_not_reported_as_contention(self):
        failure = OSError(errno.EBADF, "bad descriptor")
        self.msvcrt.locking.side_effect = failure
        with self.assertRaises(OSError) as caught:
            with self.state.job_lock(self.path):
                self.fail("Invalid lock entered")
        self.assertIs(caught.exception, failure)
        self.msvcrt.locking.assert_called_once()

    def test_interruption_unlocks_and_allows_reacquisition(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.state.job_lock(self.path):
                raise KeyboardInterrupt()
        with self.state.job_lock(self.path):
            pass
        self.assertEqual([call.args[1:] for call in self.msvcrt.locking.call_args_list],
                         [(2, 1), (0, 1), (2, 1), (0, 1)])

    def test_atomic_replace_fsyncs_file_but_never_opens_directory(self):
        real_open, real_fsync, real_replace = os.open, os.fsync, os.replace
        events = []

        def open_file(path, *args, **kwargs):
            self.assertNotEqual(Path(path), self.path.parent)
            return real_open(path, *args, **kwargs)

        def fsync(fd):
            events.append("fsync")
            return real_fsync(fd)

        def replace(src, dst):
            events.append("replace")
            self.assertEqual(Path(src).parent, self.path.parent)
            return real_replace(src, dst)

        with patch.object(os, "open", side_effect=open_file), patch.object(os, "fsync", side_effect=fsync), patch.object(os, "replace", side_effect=replace):
            self.state.atomic_json(self.path, {"old": True})
            self.state.atomic_json(self.path, {"old": False})
        self.assertEqual(events, ["fsync", "replace", "fsync", "replace"])
        self.assertEqual(json.loads(self.path.read_text()), {"old": False})
        self.assertEqual(list(self.path.parent.glob(".job.json.*")), [])

    def test_failed_flush_or_replace_preserves_checkpoint(self):
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation):
                self.state.atomic_json(self.path, {"old": True})
                with patch.object(os, operation, side_effect=OSError("disk failure")):
                    with self.assertRaises(OSError):
                        self.state.atomic_json(self.path, {"old": False})
                self.assertEqual(json.loads(self.path.read_text()), {"old": True})
                self.assertEqual(list(self.path.parent.glob(".job.json.*")), [])


if __name__ == "__main__":
    unittest.main()
