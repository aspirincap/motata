from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest
from unittest.mock import patch


spec = spec_from_file_location('npm12_install_check', Path(__file__).resolve().parents[1] / 'scripts/check_npm12_install.py')
check = module_from_spec(spec)
spec.loader.exec_module(check)


class RegistryPropagationTests(unittest.TestCase):
    def test_only_registry_visibility_errors_are_retried(self):
        for code in ('ETARGET', 'E404'):
            with self.subTest(code=code), patch.object(check, 'run', side_effect=[RuntimeError(f'npm error code {code}'), 'ok']) as run, patch.object(check.time, 'sleep') as sleep:
                check.install_package(['npm', 'install'], Path('.'), {}, registry=True)
                self.assertEqual(run.call_count, 2)
                sleep.assert_called_once_with(20)

    def test_registry_wait_is_bounded(self):
        with patch.object(check, 'run', side_effect=RuntimeError('npm error code ETARGET')) as run, patch.object(check.time, 'sleep') as sleep:
            with self.assertRaises(RuntimeError):
                check.install_package(['npm', 'install'], Path('.'), {}, registry=True)
            self.assertEqual(run.call_count, 16)
            self.assertEqual(sleep.call_count, 15)

    def test_tarball_and_unrelated_errors_fail_immediately(self):
        for registry, message in ((False, 'npm error code ETARGET'), (True, 'npm error code EACCES')):
            with self.subTest(registry=registry), patch.object(check, 'run', side_effect=RuntimeError(message)) as run, patch.object(check.time, 'sleep') as sleep:
                with self.assertRaises(RuntimeError):
                    check.install_package(['npm', 'install'], Path('.'), {}, registry=registry)
                run.assert_called_once()
                sleep.assert_not_called()
