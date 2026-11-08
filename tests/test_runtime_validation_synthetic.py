"""Synthetic Stage 2 rework checks: typed hard refusals, cleanup-before-fallback, protocol.

STATIC REWORK: these tests are prepared but not executed until the Owner approves a
permitted fixture strategy. No real interpreter, supervisor, WindowsJob, child
process, Win32 call, network call or model is used here; fakes are explicit.
"""
import contextlib
import hashlib
import importlib.machinery
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from web_content_fetcher import extraction_runtime, extractors, runtime_entry, windows_job


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _raise_runtime_error():
    raise RuntimeError('synthetic supervisor create failure')


def _raise_attribute_error():
    raise AttributeError('synthetic supervisor attribute failure')


class TinyRuntime:
    """A disposable configured runtime tree; nothing in it is ever executed."""

    def __init__(self, tmp: Path, *, config_overrides=None, receipt_overrides=None):
        self.root = tmp / 'repo'
        (self.root / 'config').mkdir(parents=True)
        (self.root / 'web_content_fetcher').mkdir()
        self.python = self.root / 'interp' / 'python.exe'
        self.python.parent.mkdir()
        self.python.write_bytes(b'synthetic interpreter, never executed')
        self.supervisor = self.root / 'windows_job.py'
        self.supervisor.write_text('class WindowsJob:\n    pass\n', encoding='utf-8')
        self.receipt = self.root / 'validation.json'
        receipt = {
            'python': str(self.python.resolve()),
            'pip_check_exit_code': 0,
            'entrypoint_tests_passed': True,
            'fingerprints': {
                str(self.python.resolve()): _sha(self.python),
                str(self.supervisor.resolve()): _sha(self.supervisor),
            },
        }
        receipt.update(receipt_overrides or {})
        self.receipt_data = receipt
        self.receipt.write_text(json.dumps(receipt), encoding='utf-8')
        config = {
            'enabled': True,
            'mode': 'extraction',
            'python': str(self.python.resolve()),
            'validation_receipt': str(self.receipt.resolve()),
            'validation_sha256': _sha(self.receipt),
            'process_supervisor': str(self.supervisor.resolve()),
            'attachment_model_directory': str(self.root / 'model'),
            'attachments_enabled': True,
            'timeout_seconds': 15,
        }
        config.update(config_overrides or {})
        self.config = config
        self.config_path = self.root / 'config' / 'local-runtime.json'
        self.config_path.write_text(json.dumps(config), encoding='utf-8')


class FakeResume:
    def __init__(self, return_value=0, error=None):
        self.return_value = return_value
        self.error = error
        self.argtypes = None
        self.restype = None
        self.calls = []

    def __call__(self, handle):
        self.calls.append(handle)
        if self.error is not None:
            raise self.error
        return self.return_value


class FakeProcess:
    def __init__(self, *, stdout=b'', stderr=b'', returncode=0, timeout_first=False,
                 timeout_second=False, communicate_error=None, poll_error=None,
                 returncode_error=None, kill_error=None, wait_error=None, poll_alive=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.timeout_first = timeout_first
        self.timeout_second = timeout_second
        self.communicate_error = communicate_error
        self.poll_error = poll_error
        self.returncode_error = returncode_error
        self.kill_error = kill_error
        self.wait_error = wait_error
        self.poll_alive = poll_alive
        self._handle = 1
        self.communicate_calls = []
        self.killed = False
        self.waited = False

    def communicate(self, data=None, timeout=None):
        self.communicate_calls.append({'data': data, 'timeout': timeout})
        if self.timeout_first and len(self.communicate_calls) == 1:
            raise subprocess.TimeoutExpired(['synthetic'], timeout)
        if self.timeout_second and len(self.communicate_calls) == 2:
            raise subprocess.TimeoutExpired(['synthetic'], timeout)
        if self.communicate_error is not None:
            raise self.communicate_error
        return self._stdout, self._stderr

    def poll(self):
        if self.poll_error is not None:
            raise self.poll_error
        if self.poll_alive and not self.killed:
            return None
        return self.returncode

    def kill(self):
        if self.kill_error is not None:
            raise self.kill_error
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.wait_error is not None:
            raise self.wait_error
        self.waited = True
        return self.returncode


class FakeJob:
    def __init__(self, *, assign_error=None, terminate_error=None, close_error=None):
        self.assign_error = assign_error
        self.terminate_error = terminate_error
        self.close_error = close_error
        self.assigned = []
        self.terminated = []
        self.closed = False

    def assign(self, process):
        if self.assign_error is not None:
            raise self.assign_error
        self.assigned.append(process)

    def terminate(self, code=124):
        if self.terminate_error is not None:
            raise self.terminate_error
        self.terminated.append(code)

    def close(self):
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class FakeLoader:
    def __init__(self, factory):
        self.factory = factory

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.WindowsJob = self.factory


def fake_spec(factory):
    return importlib.machinery.ModuleSpec('_wcf_extraction_supervisor', FakeLoader(factory), origin='<synthetic-supervisor>')


class EndToEndHarness(unittest.TestCase):
    """Runs the real extract_content -> _extract_trafilatura -> route -> run_worker path."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        self.rt = TinyRuntime(self.tmp)
        self.local = None
        self.popen = None
        self.process = None
        self.job = None

    def execute(self, *, runtime=None, process=None, job=None, job_factory=None, spec_error=None,
                resume=None, popen_error=None, ntdll=None, extractor='trafilatura', html='<p>synthetic</p>'):
        runtime = runtime or self.rt
        self.job = job if job is not None else FakeJob()
        self.process = process if process is not None else FakeProcess()
        resume = resume if resume is not None else FakeResume()
        win32 = ntdll if ntdll is not None else types.SimpleNamespace(NtResumeProcess=resume)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(extraction_runtime, '__file__', str(runtime.root / 'web_content_fetcher' / 'extraction_runtime.py')))
        stack.enter_context(mock.patch.dict(os.environ, {'WCF_USE_SYSTEM_PYTHON': '0', 'WCF_EXTRACTION_WORKER': '0'}))
        stack.enter_context(mock.patch.object(extraction_runtime.ctypes, 'WinDLL', return_value=win32))
        if spec_error is not None:
            self.spec = stack.enter_context(mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location', side_effect=spec_error))
        else:
            self.spec = stack.enter_context(mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location', return_value=fake_spec(job_factory or (lambda: self.job))))
        if popen_error is not None:
            self.popen = stack.enter_context(mock.patch.object(extraction_runtime.subprocess, 'Popen', side_effect=popen_error))
        else:
            self.popen = stack.enter_context(mock.patch.object(extraction_runtime.subprocess, 'Popen', return_value=self.process))
        local_result = extractors.ExtractionResult('LOCAL', 'trafilatura')
        self.local = stack.enter_context(mock.patch.object(extractors, '_extract_trafilatura_local', return_value=local_result))
        if extractor == 'auto':
            stack.enter_context(mock.patch.object(extractors, '_extract_site_specific', return_value=None))
        return extractors.extract_content(html, url='https://example.test', extractor=extractor)

    def assert_refused(self, exception=extraction_runtime.RuntimeRefused, **kwargs):
        with self.assertRaises(exception):
            self.execute(**kwargs)
        self.local.assert_not_called()

    def assert_operational_fallback(self, reason, **kwargs):
        result = self.execute(**kwargs)
        self.local.assert_called_once()
        self.assertEqual(result.metadata['extraction_runtime']['route'], 'local_fallback')
        self.assertEqual(result.metadata['extraction_runtime']['reason'], reason)
        return result

    def mutated_runtime(self, name, *, receipt_payload=None, config_mutator=None):
        runtime = TinyRuntime(self.tmp / name)
        config = dict(runtime.config)
        if receipt_payload is not None:
            if isinstance(receipt_payload, str):
                runtime.receipt.write_text(receipt_payload, encoding='utf-8')
            else:
                runtime.receipt.write_text(json.dumps(receipt_payload), encoding='utf-8')
            config['validation_sha256'] = _sha(runtime.receipt)
        if config_mutator is not None:
            config_mutator(config)
        runtime.config = config
        runtime.config_path.write_text(json.dumps(config), encoding='utf-8')
        return runtime


class HardRefusalEndToEndTests(EndToEndHarness):
    def test_unsupported_os_refuses_end_to_end(self):
        with mock.patch.object(extraction_runtime.os, 'name', 'posix'):
            self.assert_refused()

    def test_oversize_request_refuses_end_to_end(self):
        self.assert_refused(html='a' * (9 * 1024 * 1024))

    def test_receipt_drift_refuses_end_to_end(self):
        drift = TinyRuntime(self.tmp / 'drift', config_overrides={'validation_sha256': '0' * 64})
        self.assert_refused(runtime=drift)

    def test_supervisor_import_failure_refuses_end_to_end(self):
        self.assert_refused(spec_error=ImportError('synthetic import failure'))

    def test_supervisor_create_failure_refuses_end_to_end(self):
        self.assert_refused(job_factory=_raise_runtime_error)

    def test_nonzero_resume_refuses_end_to_end(self):
        self.assert_refused(resume=FakeResume(return_value=1))

    def test_assign_valueerror_refuses_end_to_end(self):
        self.assert_refused(job=FakeJob(assign_error=ValueError('synthetic assign failure')))

    def test_assign_runtimeerror_refuses_end_to_end(self):
        self.assert_refused(job=FakeJob(assign_error=RuntimeError('synthetic assign failure')))

    def test_spawn_oserror_refuses_end_to_end(self):
        self.assert_refused(popen_error=OSError('synthetic spawn failure'))
        self.assertTrue(self.job.closed)

    def test_communicate_oserror_refuses_end_to_end(self):
        self.assert_refused(process=FakeProcess(communicate_error=OSError('synthetic pipe failure')))
        self.assertTrue(self.job.closed)

    def test_close_failure_refuses_and_still_kills_child(self):
        process = FakeProcess(poll_alive=True)
        self.assert_refused(job=FakeJob(close_error=OSError('synthetic close failure')), process=process)
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)

    def test_kill_failure_refuses_end_to_end(self):
        process = FakeProcess(poll_alive=True, kill_error=OSError('synthetic kill failure'))
        self.assert_refused(process=process)
        self.assertTrue(process.waited)

    def test_wait_failure_refuses_end_to_end(self):
        process = FakeProcess(poll_alive=True, wait_error=OSError('synthetic wait failure'))
        self.assert_refused(process=process)
        self.assertTrue(process.killed)

    def test_secondary_timeout_refuses_end_to_end(self):
        self.assert_refused(process=FakeProcess(timeout_first=True, timeout_second=True))
        self.assertEqual(self.job.terminated, [124])

    def test_terminate_failure_refuses_end_to_end(self):
        self.assert_refused(job=FakeJob(terminate_error=OSError('synthetic terminate failure')), process=FakeProcess(timeout_first=True))

    def test_unknown_child_state_refuses_end_to_end(self):
        process = FakeProcess(poll_alive=True)
        process.returncode = None
        self.assert_refused(process=process)
        self.assertTrue(process.killed)

    def test_known_exit_with_cleanup_failure_refuses_instead_of_fallback(self):
        self.assert_refused(process=FakeProcess(returncode=1), job=FakeJob(close_error=OSError('synthetic close failure')))

    def test_timeout_with_cleanup_failure_refuses_instead_of_fallback(self):
        self.assert_refused(process=FakeProcess(timeout_first=True, returncode=124), job=FakeJob(close_error=OSError('synthetic close failure')))

    def test_invalid_result_with_cleanup_failure_refuses_instead_of_fallback(self):
        self.assert_refused(process=FakeProcess(stdout=b'not json'), job=FakeJob(close_error=OSError('synthetic close failure')))

    def test_invalid_timeout_refuses_before_import_and_spawn(self):
        runtime = self.mutated_runtime('bad-timeout', config_mutator=lambda config: config.update({'timeout_seconds': 'soon'}))
        self.assert_refused(runtime=runtime)
        self.spec.assert_not_called()
        self.popen.assert_not_called()

    def test_auto_mode_refusal_does_not_downgrade_engines(self):
        with self.assertRaises(extraction_runtime.RuntimeRefused):
            self.execute(extractor='auto', spec_error=ImportError('synthetic import failure'))
        self.local.assert_not_called()


class UnexpectedNativeRouteTests(EndToEndHarness):
    """G1/G3: unexpected route/config/receipt/supervisor/cleanup errors are refusals."""

    def assert_unexpected_route_refused(self, error, extractor='trafilatura'):
        with mock.patch.object(extraction_runtime, 'route', side_effect=error):
            with self.assertRaises(extraction_runtime.RuntimeRefused):
                self.execute(extractor=extractor)
        self.local.assert_not_called()

    def test_route_keyerror_is_refusal_without_fallback(self):
        self.assert_unexpected_route_refused(KeyError('synthetic route key error'))

    def test_route_typeerror_is_refusal_without_fallback(self):
        self.assert_unexpected_route_refused(TypeError('synthetic route type error'))

    def test_route_attributeerror_is_refusal_without_fallback(self):
        self.assert_unexpected_route_refused(AttributeError('synthetic route attribute error'))

    def test_route_permissionerror_is_refusal_without_fallback(self):
        self.assert_unexpected_route_refused(PermissionError('synthetic permission refusal'))

    def test_unexpected_route_error_not_downgraded_in_auto_mode(self):
        self.assert_unexpected_route_refused(KeyError('synthetic route key error'), extractor='auto')

    def test_nonobject_receipt_refuses_end_to_end(self):
        for index, payload in enumerate(('[]', 'null', '"text"', '42', 'true')):
            with self.subTest(payload=payload):
                runtime = self.mutated_runtime(f'nonobject-receipt-{index}', receipt_payload=payload)
                self.assert_refused(runtime=runtime)

    def test_empty_object_receipt_refuses_end_to_end(self):
        runtime = self.mutated_runtime('empty-receipt', receipt_payload={})
        self.assert_refused(runtime=runtime)

    def test_nonobject_config_refuses_end_to_end(self):
        runtime = TinyRuntime(self.tmp / 'nonobject-config')
        runtime.config_path.write_text('[]', encoding='utf-8')
        self.assert_refused(runtime=runtime)

    def test_config_bad_receipt_path_type_refuses_end_to_end(self):
        runtime = self.mutated_runtime('bad-receipt-type', config_mutator=lambda config: config.update({'validation_receipt': 123}))
        self.assert_refused(runtime=runtime)

    def test_config_missing_receipt_key_refuses_end_to_end(self):
        def remove(config):
            config.pop('validation_receipt', None)
        runtime = self.mutated_runtime('missing-receipt-key', config_mutator=remove)
        self.assert_refused(runtime=runtime)

    def test_config_missing_python_key_refuses_end_to_end(self):
        def remove(config):
            config.pop('python', None)
        runtime = self.mutated_runtime('missing-python-key', config_mutator=remove)
        self.assert_refused(runtime=runtime)

    def test_supervisor_without_windowsjob_refuses_end_to_end(self):
        self.assert_refused(job_factory=object())

    def test_supervisor_attribute_failure_refuses_end_to_end(self):
        self.assert_refused(job_factory=_raise_attribute_error)

    def test_supervisor_object_without_assign_refuses_end_to_end(self):
        self.assert_refused(job_factory=lambda: types.SimpleNamespace())

    def test_unexpected_cleanup_errors_refuse_end_to_end(self):
        cases = [
            ('close-attribute', FakeJob(close_error=AttributeError('synthetic')), FakeProcess()),
            ('poll-type', FakeJob(), FakeProcess(poll_error=TypeError('synthetic'))),
            ('kill-attribute', FakeJob(), FakeProcess(poll_alive=True, kill_error=AttributeError('synthetic'))),
            ('wait-type', FakeJob(), FakeProcess(poll_alive=True, wait_error=TypeError('synthetic'))),
        ]
        for name, job, process in cases:
            with self.subTest(case=name):
                self.assert_refused(job=job, process=process)


class OperationalFallbackTests(EndToEndHarness):
    def test_known_nonzero_exit_permits_explicit_fallback(self):
        self.assert_operational_fallback('RuntimeError', process=FakeProcess(returncode=1, stderr=b'failed'))

    def test_malformed_stdout_permits_explicit_fallback(self):
        self.assert_operational_fallback('RuntimeError', process=FakeProcess(stdout=b'not json'))

    def test_nonobject_stdout_permits_explicit_fallback(self):
        for payload in (b'[]', b'null', b'"text"', b'42'):
            with self.subTest(payload=payload):
                self.assert_operational_fallback('RuntimeError', process=FakeProcess(stdout=payload))

    def test_invalid_result_payload_permits_explicit_fallback(self):
        for payload in ({'text': 1, 'engine': 'trafilatura'}, {'text': 'ok', 'engine': 'builtin'}):
            with self.subTest(payload=payload):
                self.assert_operational_fallback('RuntimeError', process=FakeProcess(stdout=json.dumps(payload).encode('utf-8')))

    def test_confirmed_timeout_permits_explicit_fallback(self):
        process = FakeProcess(timeout_first=True, returncode=124)
        self.assert_operational_fallback('TimeoutError', process=process)
        self.assertEqual(self.job.terminated, [124])
        self.assertTrue(self.job.closed)

    def test_successful_worker_result_is_used_without_fallback(self):
        process = FakeProcess(stdout=json.dumps({'text': 'REMOTE', 'engine': 'trafilatura'}).encode('utf-8'))
        result = self.execute(process=process)
        self.local.assert_not_called()
        self.assertEqual(result.text, 'REMOTE')
        self.assertNotIn('extraction_runtime', result.metadata)
        self.assertTrue(self.job.closed)


class RouteAndEntrypointValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

    def test_route_without_config_returns_none(self):
        rt = TinyRuntime(self.tmp)
        rt.config_path.unlink()
        with mock.patch.object(extraction_runtime, '__file__', str(rt.root / 'web_content_fetcher' / 'extraction_runtime.py')):
            self.assertIsNone(extraction_runtime.route('<p>x</p>', '', ''))

    def test_route_supervisor_drift_refuses_without_worker(self):
        rt = TinyRuntime(self.tmp)
        rt.supervisor.write_text('class WindowsJob:\n    pass\n# drift\n', encoding='utf-8')
        with mock.patch.object(extraction_runtime, '__file__', str(rt.root / 'web_content_fetcher' / 'extraction_runtime.py')), mock.patch.object(extraction_runtime, 'run_worker') as worker:
            with self.assertRaises(extraction_runtime.RuntimeValidationError):
                extraction_runtime.route('<p>x</p>', '', '')
        worker.assert_not_called()

    def test_route_permission_refusal_is_typed(self):
        rt = TinyRuntime(self.tmp)
        with mock.patch.object(extraction_runtime, '__file__', str(rt.root / 'web_content_fetcher' / 'extraction_runtime.py')), mock.patch.object(extraction_runtime.Path, 'read_text', side_effect=PermissionError('synthetic denial')), mock.patch.object(extraction_runtime, 'run_worker') as worker:
            with self.assertRaises(extraction_runtime.RuntimeRefused):
                extraction_runtime.route('<p>x</p>', '', '')
        worker.assert_not_called()

    def test_reentry_mismatch_refuses_before_spawn(self):
        rt = TinyRuntime(self.tmp, config_overrides={'mode': 'private'})
        env = {'WCF_USE_SYSTEM_PYTHON': '0', 'WCF_RUNTIME_REENTRY': '1'}
        with mock.patch.object(runtime_entry, '__file__', str(rt.root / 'web_content_fetcher' / 'runtime_entry.py')), mock.patch.dict(os.environ, env), mock.patch.object(runtime_entry.subprocess, 'call') as call:
            with self.assertRaises(RuntimeError):
                runtime_entry.maybe_reexec('web_content_fetcher.cli', ['--help'])
        call.assert_not_called()

    def test_validated_runtime_returns_child_exit_and_argv(self):
        rt = TinyRuntime(self.tmp, config_overrides={'mode': 'private'})
        env = {'WCF_USE_SYSTEM_PYTHON': '0', 'WCF_RUNTIME_REENTRY': '0'}
        with mock.patch.object(runtime_entry, '__file__', str(rt.root / 'web_content_fetcher' / 'runtime_entry.py')), mock.patch.dict(os.environ, env), mock.patch.object(runtime_entry.subprocess, 'call', return_value=23) as call:
            self.assertEqual(runtime_entry.maybe_reexec('web_content_fetcher.cli', ['--help']), 23)
        self.assertEqual(call.call_args.args[0], [str(Path(rt.python).resolve()), '-m', 'web_content_fetcher.cli', '--help'])


class DirectWorkerContractTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        self.rt = TinyRuntime(self.tmp)

    def tearDown(self):
        sys.modules.pop('_wcf_extraction_supervisor', None)

    def test_invalid_timeout_refused_before_validation_import_spawn(self):
        for bad in ('soon', None, [], {}, object()):
            with self.subTest(bad=type(bad).__name__):
                config = {'enabled': True, 'timeout_seconds': bad}
                with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
                    with self.assertRaises(extraction_runtime.RuntimeValidationError):
                        extraction_runtime.run_worker(self.rt.python, config, {}, self.rt.root)
                spec.assert_not_called()
                popen.assert_not_called()

    def test_timeout_parse_clamp_and_default_preserved(self):
        self.assertEqual(extraction_runtime._timeout_seconds({}), 15)
        self.assertEqual(extraction_runtime._timeout_seconds({'timeout_seconds': 0.001}), 0.05)
        self.assertEqual(extraction_runtime._timeout_seconds({'timeout_seconds': 999}), 60)
        self.assertEqual(extraction_runtime._timeout_seconds({'timeout_seconds': '7.5'}), 7.5)
        for bad in ('soon', None, [], {}, object()):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(extraction_runtime.RuntimeValidationError):
                    extraction_runtime._timeout_seconds({'timeout_seconds': bad})

    def test_unsupported_worker_still_valueerror(self):
        with mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(ValueError):
                extraction_runtime.run_worker(self.rt.python, self.rt.config, {}, self.rt.root, worker_module='untrusted.worker')
        popen.assert_not_called()

    def test_disabled_config_refused_before_validation(self):
        config = {'enabled': False}
        with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(extraction_runtime.RuntimeRefused):
                extraction_runtime.run_worker(self.rt.python, config, {}, self.rt.root)
        spec.assert_not_called()
        popen.assert_not_called()

    def test_missing_enabled_refused_before_validation(self):
        config = {'python': str(self.rt.python), 'process_supervisor': str(self.rt.supervisor)}
        with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(extraction_runtime.RuntimeRefused):
                extraction_runtime.run_worker(self.rt.python, config, {}, self.rt.root)
        spec.assert_not_called()
        popen.assert_not_called()

    def test_non_boolean_enabled_refused_before_validation(self):
        for enabled in ('true', 1, 'yes', None):
            with self.subTest(enabled=enabled):
                config = {'enabled': enabled, 'python': str(self.rt.python)}
                with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
                    with self.assertRaises(extraction_runtime.RuntimeRefused):
                        extraction_runtime.run_worker(self.rt.python, config, {}, self.rt.root)
                spec.assert_not_called()
                popen.assert_not_called()

    def test_non_mapping_config_refused(self):
        for config in (None, [], 'config', 3):
            with self.subTest(config=type(config).__name__):
                with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
                    with self.assertRaises(extraction_runtime.RuntimeRefused):
                        extraction_runtime.run_worker(self.rt.python, config, {}, self.rt.root)
                spec.assert_not_called()
                popen.assert_not_called()

    def test_attachment_worker_requires_both_policy_flags(self):
        cases = [
            {'enabled': True},
            {'enabled': True, 'attachments_enabled': False},
            {'enabled': False, 'attachments_enabled': True},
            {'attachments_enabled': True},
            {'enabled': 'true', 'attachments_enabled': True},
        ]
        for config in cases:
            with self.subTest(config=config):
                with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
                    with self.assertRaises(extraction_runtime.RuntimeRefused):
                        extraction_runtime.run_worker(self.rt.python, config, {}, self.rt.root, worker_module='web_content_fetcher.attachment_worker')
                spec.assert_not_called()
                popen.assert_not_called()

    def test_non_windows_is_hard_refusal(self):
        with mock.patch.object(extraction_runtime.os, 'name', 'posix'), mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(extraction_runtime.RuntimeRefused):
                extraction_runtime.run_worker(self.rt.python, self.rt.config, {}, self.rt.root)
        popen.assert_not_called()

    def test_oversize_is_hard_refusal(self):
        with mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(extraction_runtime.RuntimeRefused):
                extraction_runtime.run_worker(self.rt.python, self.rt.config, {'blob': 'a' * (9 * 1024 * 1024)}, self.rt.root)
        popen.assert_not_called()

    def test_key_presence_only_config_refused(self):
        config = {'enabled': True, 'process_supervisor': str(self.rt.supervisor)}
        with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(extraction_runtime.RuntimeValidationError):
                extraction_runtime.run_worker(self.rt.python, config, {}, self.rt.root)
        spec.assert_not_called()
        popen.assert_not_called()

    def test_supervisor_load_failure_is_hard_refusal(self):
        with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location', side_effect=ImportError('synthetic')), mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(extraction_runtime.RuntimeRefused):
                extraction_runtime.run_worker(self.rt.python, self.rt.config, {}, self.rt.root)
        popen.assert_not_called()

    def test_supervisor_not_bound_refused(self):
        self.rt = TinyRuntime(self.tmp / 'second')
        fingerprints = {str(self.rt.python.resolve()): _sha(self.rt.python)}
        self.rt.receipt.write_text(json.dumps({**self.rt.receipt_data, 'fingerprints': fingerprints}), encoding='utf-8')
        config = {**self.rt.config, 'validation_sha256': _sha(self.rt.receipt)}
        self.rt.config_path.write_text(json.dumps(config), encoding='utf-8')
        with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(extraction_runtime.RuntimeValidationError):
                extraction_runtime.run_worker(self.rt.python, config, {}, self.rt.root)
        spec.assert_not_called()
        popen.assert_not_called()

    def test_worker_interpreter_mismatch_refused(self):
        other = self.tmp / 'other-python.exe'
        other.write_bytes(b'other')
        with mock.patch.object(extraction_runtime.importlib.util, 'spec_from_file_location') as spec, mock.patch.object(extraction_runtime.subprocess, 'Popen') as popen:
            with self.assertRaises(extraction_runtime.RuntimeValidationError):
                extraction_runtime.run_worker(other, self.rt.config, {}, self.rt.root)
        spec.assert_not_called()
        popen.assert_not_called()


class FakeCloseHandle:
    def __init__(self, result):
        self.result = result
        self.argtypes = None
        self.restype = None
        self.calls = []

    def __call__(self, handle):
        self.calls.append(handle)
        return 1 if self.result else 0


def _make_job(handle=123):
    job = windows_job.WindowsJob.__new__(windows_job.WindowsJob)
    job.handle = handle
    job.assigned = True
    return job


class WindowsJobCloseTests(EndToEndHarness):
    """Directed synthetic controls for the owner-authorized fail-closed close()."""

    def test_close_success_binds_signature_checks_bool_and_clears_handle(self):
        close = FakeCloseHandle(True)
        kernel32 = types.SimpleNamespace(CloseHandle=close)
        job = _make_job()
        with mock.patch.object(windows_job.ctypes, 'WinDLL', return_value=kernel32) as windll:
            job.close()
        windll.assert_called_once_with('kernel32', use_last_error=True)
        self.assertEqual(close.calls, [123])
        self.assertEqual(close.argtypes, [windows_job.wintypes.HANDLE])
        self.assertEqual(close.restype, windows_job.wintypes.BOOL)
        self.assertIsNone(job.handle)

    def test_close_no_handle_is_idempotent_noop(self):
        close = FakeCloseHandle(True)
        kernel32 = types.SimpleNamespace(CloseHandle=close)
        job = _make_job(handle=None)
        with mock.patch.object(windows_job.ctypes, 'WinDLL', return_value=kernel32) as windll:
            job.close()
            job.close()
        windll.assert_not_called()
        self.assertEqual(close.calls, [])
        self.assertIsNone(job.handle)

    def test_close_false_raises_oserror_and_retains_handle(self):
        close = FakeCloseHandle(False)
        kernel32 = types.SimpleNamespace(CloseHandle=close)
        job = _make_job()
        with mock.patch.object(windows_job.ctypes, 'WinDLL', return_value=kernel32), \
             mock.patch.object(windows_job.ctypes, 'get_last_error', return_value=6):
            with self.assertRaises(OSError) as caught:
                job.close()
        self.assertEqual(close.calls, [123])
        self.assertEqual(caught.exception.errno, 6)
        self.assertEqual(job.handle, 123)

    def test_close_loader_failure_retains_handle(self):
        job = _make_job()
        with mock.patch.object(windows_job.ctypes, 'WinDLL', side_effect=OSError('synthetic kernel32 load failure')):
            with self.assertRaises(OSError):
                job.close()
        self.assertEqual(job.handle, 123)

    def test_close_false_flows_into_cleanup_problem(self):
        job = _make_job(456)
        with mock.patch.object(windows_job.ctypes, 'WinDLL', return_value=types.SimpleNamespace(CloseHandle=FakeCloseHandle(False))), \
             mock.patch.object(windows_job.ctypes, 'get_last_error', return_value=6):
            problem = extraction_runtime._cleanup(job, None)
        self.assertEqual(problem, 'close:OSError')
        self.assertEqual(job.handle, 456)

    def test_close_false_in_run_worker_refuses_without_fallback(self):
        job = _make_job(789)
        job.assign = lambda process: None
        ntdll = types.SimpleNamespace(NtResumeProcess=FakeResume(), CloseHandle=FakeCloseHandle(False))
        with mock.patch.object(windows_job.ctypes, 'get_last_error', return_value=6):
            with self.assertRaises(extraction_runtime.RuntimeRefused):
                self.execute(job=job, process=FakeProcess(returncode=1), ntdll=ntdll)
        self.local.assert_not_called()
        self.assertEqual(job.handle, 789)


if __name__ == '__main__':
    unittest.main()
