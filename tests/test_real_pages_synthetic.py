"""Synthetic, in-memory contract tests for ``scripts/check_real_pages.py``.

The suite loads the candidate script from source, then replaces every external
dependency with an explicit in-memory fake:

* ``subprocess.run`` -> a queue of recorded fake ``CompletedProcess`` objects,
* ``time.monotonic`` -> a deterministic clock,
* ``os`` -> a synthetic module whose ``environ`` mapping is the only environment
  the candidate can see (the host environment is never copied or mutated),
* ``pathlib.Path`` -> a registry-backed fake that records reads, writes and
  ``mkdir`` calls.

For the duration of the candidate import and of every test, scoped
class-preserving guards are installed on the real stdlib objects
(``subprocess.Popen`` / ``run``, ``socket.socket`` / ``connect`` /
``create_connection`` / ``getaddrinfo``, ``os.system`` / ``os.popen``) and are
always restored through ``finally``/``addCleanup``, including on exceptions.  An
accidental attempt to perform external I/O raises
:class:`UnexpectedExternalIO` and fails the test; identity controls verify the
real objects are restored afterwards.  No test writes to the real filesystem,
starts a process, opens a socket, reads a real config or host environment
variable, or imports the ``web_content_fetcher`` package.  A package-prefix
module-name snapshot taken immediately before and after the selected-script load
tolerates a package import that a combined suite already performed during
discovery and fails only if the selected script adds new package-prefix names.
The only explicit environment opt-in is ``WCF_SMOKE_TEST_SCRIPT``.  Stdlib only.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


CONFIG_PATH = "C:/fake/operator-config.json"
OUTPUT_PATH = "C:/fake/reports/smoke.json"
OUTPUT_MARKDOWN_PATH = "C:/fake/reports/smoke.md"


class UnexpectedExternalIO(AssertionError):
    """Signals an accidental real external operation from the code under test."""


_REAL_TIMEOUT_EXPIRED = subprocess.TimeoutExpired
_REAL_COMPLETED_PROCESS = subprocess.CompletedProcess
_REAL_POPEN_CLASS = subprocess.Popen
_REAL_POPEN_INIT = subprocess.Popen.__init__
_REAL_SUBPROCESS_RUN = subprocess.run
_REAL_SOCKET_CLASS = socket.socket
_REAL_SOCKET_INIT = socket.socket.__init__
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_OS_SYSTEM = os.system
_REAL_OS_POPEN = getattr(os, "popen", None)


def _blocked(name):
    def guard(*args, **kwargs):
        raise UnexpectedExternalIO(f"unexpected external operation: {name}")

    return guard


def _guard_targets():
    targets = [
        (subprocess.Popen, "__init__", "subprocess.Popen"),
        (subprocess, "run", "subprocess.run"),
        (socket.socket, "__init__", "socket.socket.__init__"),
        (socket.socket, "connect", "socket.socket.connect"),
        (socket, "create_connection", "socket.create_connection"),
        (socket, "getaddrinfo", "socket.getaddrinfo"),
        (os, "system", "os.system"),
    ]
    if _REAL_OS_POPEN is not None:
        targets.append((os, "popen", "os.popen"))
    return targets


def _install_guards():
    """Patch real stdlib entry points; return the saved identities."""

    saved = []
    for owner, attribute, label in _guard_targets():
        original = getattr(owner, attribute)
        setattr(owner, attribute, _blocked(label))
        saved.append((owner, attribute, original))
    return saved


def _restore_guards(saved):
    """Restore every saved identity in reverse order (idempotent)."""

    for owner, attribute, original in reversed(saved):
        setattr(owner, attribute, original)


class _scoped_guards:
    """Install stdlib I/O guards for one scope and always restore identities."""

    def __enter__(self):
        self._saved = _install_guards()
        return self

    def __exit__(self, exc_type, exc, traceback):
        _restore_guards(self._saved)
        return False


def _guard_identity_pairs():
    """Return (label, current, expected-original) triples for identity checks."""

    pairs = [
        ("subprocess.Popen class", subprocess.Popen, _REAL_POPEN_CLASS),
        ("subprocess.Popen.__init__", subprocess.Popen.__init__, _REAL_POPEN_INIT),
        ("subprocess.run", subprocess.run, _REAL_SUBPROCESS_RUN),
        (
            "subprocess.CompletedProcess",
            subprocess.CompletedProcess,
            _REAL_COMPLETED_PROCESS,
        ),
        ("socket.socket class", socket.socket, _REAL_SOCKET_CLASS),
        ("socket.socket.__init__", socket.socket.__init__, _REAL_SOCKET_INIT),
        ("socket.socket.connect", socket.socket.connect, _REAL_SOCKET_CONNECT),
        ("socket.create_connection", socket.create_connection, _REAL_CREATE_CONNECTION),
        ("socket.getaddrinfo", socket.getaddrinfo, _REAL_GETADDRINFO),
        ("os.system", os.system, _REAL_OS_SYSTEM),
    ]
    if _REAL_OS_POPEN is not None:
        pairs.append(("os.popen", os.popen, _REAL_OS_POPEN))
    return pairs


_PACKAGE_PREFIX = "web_content_fetcher"


def _package_module_names(module_names):
    """Return package-prefix module names from an in-memory name mapping."""

    prefix = _PACKAGE_PREFIX + "."
    return frozenset(
        name
        for name in module_names
        if name == _PACKAGE_PREFIX or name.startswith(prefix)
    )


def _new_package_modules(before_names, after_names):
    """Return package-prefix names added between two in-memory snapshots."""

    return tuple(
        sorted(_package_module_names(after_names) - _package_module_names(before_names))
    )


def _resolve_candidate_path() -> Path:
    candidate_root = Path(__file__).resolve().parents[1]
    default = candidate_root / "scripts" / "check_real_pages.py"
    override = os.environ.get("WCF_SMOKE_TEST_SCRIPT")
    if not override:
        return default
    chosen = Path(override)
    if not chosen.is_absolute():
        chosen = Path.cwd() / chosen
    resolved = chosen.resolve()
    outputs_root = candidate_root.parent.resolve()
    try:
        resolved.relative_to(outputs_root)
    except ValueError:
        raise AssertionError(
            "WCF_SMOKE_TEST_SCRIPT must point inside this task's outputs directory"
        ) from None
    if resolved.name != default.name:
        raise AssertionError("WCF_SMOKE_TEST_SCRIPT must point to check_real_pages.py")
    return resolved


def _load_candidate():
    script_path = _resolve_candidate_path()
    module_name = "wcf_check_real_pages_synthetic_target"
    spec = importlib.util.spec_from_file_location(module_name, str(script_path))
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not create an import spec for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_import_names():
    """Parse the selected candidate source (no import) and return its imports."""

    source = Path(CANDIDATE.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


_PACKAGE_MODULES_BEFORE = _package_module_names(sys.modules)
_module_guards = _install_guards()
try:
    CANDIDATE = _load_candidate()
finally:
    _restore_guards(_module_guards)
_PACKAGE_MODULES_AFTER = _package_module_names(sys.modules)
_NEW_PACKAGE_MODULES = tuple(
    sorted(_PACKAGE_MODULES_AFTER - _PACKAGE_MODULES_BEFORE)
)


class FakeCompletedProcess(subprocess.CompletedProcess):
    """Class-preserving in-memory stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, command, returncode=0, stdout="", stderr=""):
        super().__init__(command, returncode, stdout=stdout, stderr=stderr)


class FakeSubprocess:
    """Injected as the candidate module's ``subprocess`` attribute."""

    TimeoutExpired = _REAL_TIMEOUT_EXPIRED

    def __init__(self):
        self.calls = []
        self.handler = None

    def run(self, command, **kwargs):
        self.calls.append({"command": list(command), "kwargs": dict(kwargs)})
        if self.handler is None:
            raise AssertionError(
                "subprocess.run called without a configured in-memory handler"
            )
        return self.handler(list(command), dict(kwargs))


class FakeClock:
    """Deterministic ``time.monotonic`` replacement."""

    def __init__(self, values=None):
        self.values = list(values or [])
        self.calls = 0

    def monotonic(self):
        if not self.values:
            return 0.0
        index = min(self.calls, len(self.values) - 1)
        self.calls += 1
        return float(self.values[index])


class FakeOsModule:
    """Synthetic module-local ``os`` with an explicit environ mapping."""

    def __init__(self, environ=None):
        self.environ = dict(environ or {})


class FakePath:
    """Registry-backed ``pathlib.Path`` replacement; never touches the disk."""

    files: dict[str, str] = {}
    read_failures: dict[str, BaseException] = {}
    writes: list[tuple[str, str, str | None, str | None]] = []
    mkdirs: list[tuple[str, bool, bool]] = []

    def __init__(self, value):
        self._value = str(value)

    @classmethod
    def reset(cls):
        cls.files = {}
        cls.read_failures = {}
        cls.writes = []
        cls.mkdirs = []

    def __str__(self):
        return self._value

    def __repr__(self):
        return f"FakePath({self._value!r})"

    def __fspath__(self):
        return self._value

    def __eq__(self, other):
        return isinstance(other, FakePath) and other._value == self._value

    def __hash__(self):
        return hash(self._value)

    def __truediv__(self, other):
        return FakePath(f"{self._value.rstrip('/\\')}/{other}")

    def resolve(self):
        return FakePath(self._value)

    @property
    def parent(self):
        value = self._value.rstrip("/\\")
        for separator in ("/", "\\"):
            if separator in value:
                return FakePath(value.rsplit(separator, 1)[0])
        return FakePath(".")

    def with_suffix(self, suffix):
        value = self._value
        head, separator, tail = value.rpartition("/")
        if not separator:
            head, separator, tail = value.rpartition("\\")
        if "." in tail:
            tail = tail[: tail.rindex(".")]
        stem = f"{head}{separator}{tail}" if separator else tail
        return FakePath(stem + suffix)

    def mkdir(self, parents=False, exist_ok=False):
        FakePath.mkdirs.append((self._value, bool(parents), bool(exist_ok)))

    def read_text(self, encoding=None):
        if self._value in FakePath.read_failures:
            raise FakePath.read_failures[self._value]
        if self._value not in FakePath.files:
            raise FileNotFoundError(self._value)
        return FakePath.files[self._value]

    def write_text(self, data, encoding=None, newline=None):
        text = data if isinstance(data, str) else str(data)
        FakePath.writes.append((self._value, text, encoding, newline))
        FakePath.files[self._value] = text
        return len(text)


class CandidateTestCase(unittest.TestCase):
    """Shared in-memory harness for tests that drive the candidate module."""

    def setUp(self):
        FakePath.reset()
        self._saved_guards = _install_guards()
        self.addCleanup(_restore_guards, self._saved_guards)
        self._saved_candidate = {
            "subprocess": CANDIDATE.subprocess,
            "Path": CANDIDATE.Path,
            "time": CANDIDATE.time,
            "os": CANDIDATE.os,
        }
        self.addCleanup(self._restore_candidate_state)
        self.fake_subprocess = FakeSubprocess()
        self.fake_os = FakeOsModule()
        self.clock = FakeClock()
        CANDIDATE.subprocess = self.fake_subprocess
        CANDIDATE.Path = FakePath
        CANDIDATE.time = self.clock
        CANDIDATE.os = self.fake_os

    def _restore_candidate_state(self):
        CANDIDATE.subprocess = self._saved_candidate["subprocess"]
        CANDIDATE.Path = self._saved_candidate["Path"]
        CANDIDATE.time = self._saved_candidate["time"]
        CANDIDATE.os = self._saved_candidate["os"]

    # -- fixtures ---------------------------------------------------------

    def valid_case(self, **overrides):
        case = {
            "id": "case-1",
            "url": "https://example.invalid/page",
            "required": True,
        }
        case.update(overrides)
        return case

    def valid_config(self, *cases):
        return {"cases": list(cases) if cases else [self.valid_case()]}

    def ok_body(self, **overrides):
        body = {
            "status": "ok",
            "transport": "http",
            "final_url": "https://example.invalid/page",
            "title": "Example",
            "extracted_text": "x" * 40,
            "selector_results": [{"selector": "h1", "count": 1}],
            "site_data": {"site_profile": "generic"},
            "archive": {"archive_path": "C:/fake/archive/case-1"},
            "raw_sha256": "a" * 64,
            "extracted_sha256": "b" * 64,
            "errors": [],
        }
        body.update(overrides)
        return body

    def respond(self, *items, returncode=0, stderr=""):
        """Queue fake ``subprocess.run`` results for successive cases."""

        queue = list(items)

        def handler(command, kwargs):
            if not queue:
                raise AssertionError("unexpected extra subprocess.run call")
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, FakeCompletedProcess):
                return item
            if isinstance(item, dict):
                return FakeCompletedProcess(command, returncode, json.dumps(item), stderr)
            if isinstance(item, str):
                return FakeCompletedProcess(command, returncode, item, stderr)
            raise AssertionError(f"unsupported fake response: {item!r}")

        self.fake_subprocess.handler = handler

    def base_argv(self, *extra):
        return ["--config", CONFIG_PATH, "--output", OUTPUT_PATH, *extra]

    def run_main(self, config, argv=None, output=OUTPUT_PATH):
        if isinstance(config, str):
            FakePath.files[CONFIG_PATH] = config
        else:
            FakePath.files[CONFIG_PATH] = json.dumps(config)
        args = list(argv) if argv is not None else ["--config", CONFIG_PATH, "--output", output]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = CANDIDATE.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_main_rejects_config(self, config, argv=None):
        FakePath.reset()
        self.respond()  # any fetch call is a test failure
        code, stdout, stderr = self.run_main(config, argv=argv)
        self.assertEqual(code, 2)
        self.assertEqual(self.fake_subprocess.calls, [])
        self.assertEqual(FakePath.writes, [])
        self.assertIn("error:", stderr)

    def assert_cli_rejects(self, argv):
        FakePath.reset()
        FakePath.files[CONFIG_PATH] = json.dumps(self.valid_config())
        self.respond()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                CANDIDATE.main(argv)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.fake_subprocess.calls, [])
        self.assertEqual(FakePath.writes, [])

    # -- assertions -------------------------------------------------------

    def report_writes(self):
        return [write for write in FakePath.writes if write[0] == OUTPUT_PATH]

    def markdown_writes(self):
        return [write for write in FakePath.writes if write[0] == OUTPUT_MARKDOWN_PATH]


class GuardAndLoadContractTests(unittest.TestCase):
    """Guard self-checks, restoration controls and the loaded-module surface."""

    def assert_guard_identities_clear(self):
        for label, current, expected in _guard_identity_pairs():
            self.assertIs(current, expected, label)

    def test_guards_restored_after_candidate_import(self):
        self.assert_guard_identities_clear()
        self.assertIs(subprocess.CompletedProcess, _REAL_COMPLETED_PROCESS)
        self.assertTrue(issubclass(FakeCompletedProcess, subprocess.CompletedProcess))

    def test_guard_selfcheck_blocks_real_external_io_and_restores(self):
        # Intentional guard self-check: these paths must be blocked, never tried.
        saved = _install_guards()
        try:
            for label, trigger in (
                ("popen", lambda: subprocess.Popen(["cmd"])),
                ("run", lambda: subprocess.run(["cmd"])),
                ("socket", lambda: socket.socket()),
                ("create_connection", lambda: socket.create_connection(("example.invalid", 80))),
                ("getaddrinfo", lambda: socket.getaddrinfo("example.invalid", 80)),
                ("os.system", lambda: os.system("echo nope")),
            ):
                with self.subTest(guard=label):
                    with self.assertRaises(UnexpectedExternalIO):
                        trigger()
            self.assertIsNot(subprocess.run, _REAL_SUBPROCESS_RUN)
            self.assertIsNot(os.system, _REAL_OS_SYSTEM)
            self.assertIs(socket.socket, _REAL_SOCKET_CLASS)
        finally:
            _restore_guards(saved)
        self.assert_guard_identities_clear()

    def test_scoped_guards_restore_on_exception(self):
        with self.assertRaises(RuntimeError):
            with _scoped_guards():
                self.assertIsNot(subprocess.run, _REAL_SUBPROCESS_RUN)
                self.assertIsNot(socket.socket.__init__, _REAL_SOCKET_INIT)
                raise RuntimeError("intentional scope exit")
        self.assert_guard_identities_clear()

    def test_fake_completed_process_coexists_with_stdlib_process(self):
        fake = FakeCompletedProcess(["cmd"], 0, stdout="{}", stderr="")
        self.assertIsInstance(fake, subprocess.CompletedProcess)
        self.assertEqual(fake.returncode, 0)
        self.assertEqual(fake.stdout, "{}")
        self.assertEqual(fake.stderr, "")

    def test_candidate_is_loaded_from_the_candidate_tree(self):
        self.assertEqual(
            Path(CANDIDATE.__file__).resolve(),
            _resolve_candidate_path(),
        )
        self.assertEqual(CANDIDATE.ROOT, Path(CANDIDATE.__file__).resolve().parents[1])

    def test_selected_script_adds_no_new_package_modules(self):
        # A combined suite may already have imported web_content_fetcher during
        # discovery; only names added by the selected-script load matter here.
        self.assertEqual(_NEW_PACKAGE_MODULES, ())
        self.assertTrue(_PACKAGE_MODULES_BEFORE <= _PACKAGE_MODULES_AFTER)

    def test_selected_script_source_has_no_package_imports(self):
        imports = _candidate_import_names()
        package_imports = sorted(
            name
            for name in imports
            if name == _PACKAGE_PREFIX or name.startswith(_PACKAGE_PREFIX + ".")
        )
        self.assertEqual(package_imports, [])

    def test_preexisting_package_modules_do_not_fail_an_unchanged_snapshot(self):
        before = {_PACKAGE_PREFIX, _PACKAGE_PREFIX + ".cli", "unrelated.module"}
        after = {_PACKAGE_PREFIX, _PACKAGE_PREFIX + ".cli", "unrelated.module"}
        self.assertEqual(_new_package_modules(before, after), ())

    def test_new_package_module_is_detected_by_the_snapshot(self):
        before = {_PACKAGE_PREFIX, _PACKAGE_PREFIX + ".cli"}
        after = before | {_PACKAGE_PREFIX + ".synthetic_probe"}
        self.assertEqual(
            _new_package_modules(before, after),
            (_PACKAGE_PREFIX + ".synthetic_probe",),
        )

    def test_package_prefix_matching_uses_module_boundaries(self):
        names = {_PACKAGE_PREFIX + "_extra", _PACKAGE_PREFIX + "x", "unrelated"}
        self.assertEqual(_package_module_names(names), frozenset())

    def test_preserved_public_signatures(self):
        run_params = inspect.signature(CANDIDATE.run_case).parameters
        self.assertEqual(list(run_params), ["case", "timeout", "archive_root"])
        self.assertIs(run_params["case"].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        self.assertIs(run_params["timeout"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(run_params["archive_root"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(
            list(inspect.signature(CANDIDATE.evaluate_case).parameters),
            ["case", "body", "returncode"],
        )
        self.assertEqual(
            list(inspect.signature(CANDIDATE.render_markdown).parameters),
            ["report"],
        )
        self.assertEqual(list(inspect.signature(CANDIDATE.main).parameters), ["argv"])

    def test_config_is_required(self):
        FakePath.reset()
        with _scoped_guards():
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    CANDIDATE.main([])
        self.assertEqual(caught.exception.code, 2)
        self.assert_guard_identities_clear()


class ConfigValidationTests(CandidateTestCase):
    def test_rejects_malformed_top_level(self):
        for label, config in (
            ("null", None),
            ("list", []),
            ("string", "not-a-config"),
            ("missing-cases", {}),
            ("cases-not-list", {"cases": {}}),
            ("cases-empty", {"cases": []}),
        ):
            with self.subTest(label=label):
                self.assert_main_rejects_config(config)

    def test_rejects_non_object_case(self):
        self.assert_main_rejects_config({"cases": [["not-an-object"]]})
        self.assert_main_rejects_config({"cases": ["not-an-object"]})

    def test_rejects_invalid_fields(self):
        base = {"id": "case-1", "url": "https://example.invalid/page"}
        overrides = (
            ("missing-id", {"id": None}),
            ("empty-id", {"id": ""}),
            ("non-string-id", {"id": 5}),
            ("missing-url", {"url": None}),
            ("url-not-string", {"url": 5}),
            ("url-ftp", {"url": "ftp://example.invalid/page"}),
            ("url-relative", {"url": "/relative/path"}),
            ("url-no-host", {"url": "https://"}),
            ("url-padded", {"url": " https://example.invalid/page"}),
            ("url-malformed-ipv6", {"url": "https://[::1"}),
            ("url-unclosed-bracket", {"url": "http://[::"}),
            ("url-hostless-port", {"url": "https://:80"}),
            ("url-hostless-port-path", {"url": "https://:443/page"}),
            ("required-int", {"required": 1}),
            ("required-string", {"required": "true"}),
            ("use-web-access-int", {"use_web_access": 1}),
            ("prefer-browser-int", {"prefer_browser": 0}),
            ("allow-status-fail-none", {"allow_status_fail": None}),
            ("timeout-bool", {"timeout_seconds": True}),
            ("timeout-float", {"timeout_seconds": 1.5}),
            ("timeout-zero", {"timeout_seconds": 0}),
            ("timeout-negative", {"timeout_seconds": -1}),
            ("cache-negative", {"cache_max_age_seconds": -5}),
            ("cache-float", {"cache_max_age_seconds": 1.5}),
            ("cache-bool", {"cache_max_age_seconds": False}),
            ("minchars-string", {"min_extracted_chars": "3"}),
            ("minchars-negative", {"min_extracted_chars": -1}),
            ("minsel-bool", {"min_selector_results": False}),
            ("selectors-not-list", {"selectors": "h1"}),
            ("selectors-number", {"selectors": [3]}),
            ("selectors-empty-string", {"selectors": [""]}),
            ("selectors-blank-string", {"selectors": ["  "]}),
            ("selectors-dict-missing-key", {"selectors": [{"name": "h1"}]}),
            ("selectors-dict-non-string", {"selectors": [{"selector": 5}]}),
            ("selectors-dict-blank", {"selectors": [{"selector": "  "}]}),
        )
        for label, override in overrides:
            with self.subTest(label=label):
                case = dict(base)
                case.update(override)
                self.assert_main_rejects_config({"cases": [case]})

    def test_case_id_traversal_and_portable_name_matrix(self):
        for case_id in (
            " ",
            " leading",
            "trailing ",
            ".",
            "..",
            "...",
            "../escape",
            "a/b",
            "a\\b",
            "C:name",
            "a:b",
            "tab\tid",
            "line\nbreak",
            "nul\x00id",
            "con",
            "CON",
            "con.txt",
            "nul",
            "aux",
            "prn",
            "com1",
            "COM9",
            "lpt1",
            "LPT9",
            "LPT9.log",
            "case.",
            "<",
            ">",
            '"',
            "|",
            "?",
            "*",
            "a<b",
            "a>b",
            'a"b',
            "a|b",
            "a?b",
            "a*b",
        ):
            with self.subTest(case_id=case_id):
                self.assert_main_rejects_config(
                    {"cases": [self.valid_case(id=case_id)]}
                )

    def test_valid_unicode_and_portable_ids_are_accepted(self):
        case_ids = (
            "case-1",
            "案例-1",
            "Ünïcode",
            "a.b-c_d",
            ".hidden",
            "a..b",
            "2026-11-08",
            "CASE_ID_1",
        )
        cases = [self.valid_case(id=case_id) for case_id in case_ids]
        self.respond(*[self.ok_body() for _ in cases])
        code, stdout, stderr = self.run_main({"cases": cases})
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fake_subprocess.calls), len(cases))

    def test_rejects_casefold_collision(self):
        self.assert_main_rejects_config(
            {"cases": [self.valid_case(id="Case"), self.valid_case(id="case")]}
        )

    def test_rejects_nfc_nfd_collision(self):
        self.assert_main_rejects_config(
            {
                "cases": [
                    self.valid_case(id="caf\u00e9"),
                    self.valid_case(id="cafe\u0301"),
                ]
            }
        )

    def test_rejects_duplicate_ids(self):
        self.assert_main_rejects_config(
            {"cases": [self.valid_case(id="same"), self.valid_case(id="same")]}
        )

    def test_rejects_negative_limit(self):
        self.assert_cli_rejects(self.base_argv("--limit", "-1"))

    def test_rejects_nonpositive_timeout(self):
        for value in ("0", "-3"):
            with self.subTest(value=value):
                self.assert_cli_rejects(self.base_argv("--timeout", value))

    def test_whole_config_preflight_runs_before_any_fetch(self):
        cases = [self.valid_case(id="ok-case"), self.valid_case(id="../bad")]
        self.assert_main_rejects_config({"cases": cases})

    def test_limit_truncation_still_preflights_whole_config(self):
        cases = [self.valid_case(id="ok-case"), self.valid_case(id="../bad")]
        self.assert_main_rejects_config({"cases": cases}, argv=self.base_argv("--limit", "1"))

    def test_invalid_config_json_exits_two_without_fetch(self):
        self.assert_main_rejects_config("{this is not json")

    def test_config_read_permission_refusal_is_preserved(self):
        FakePath.reset()
        FakePath.read_failures[CONFIG_PATH] = PermissionError("config denied")
        self.respond()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(PermissionError):
                CANDIDATE.main(["--config", CONFIG_PATH, "--output", OUTPUT_PATH])
        self.assertEqual(self.fake_subprocess.calls, [])
        self.assertEqual(FakePath.writes, [])


class ArgvContractTests(CandidateTestCase):
    def test_default_argv_matches_public_cli_contract(self):
        self.respond(self.ok_body())
        code, stdout, stderr = self.run_main(self.valid_config())
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fake_subprocess.calls), 1)
        call = self.fake_subprocess.calls[0]
        expected = [
            sys.executable,
            "-m",
            "web_content_fetcher",
            "fetch",
            "https://example.invalid/page",
            "--format",
            "json",
            "--include-raw",
            "--fetch-profile",
            "generic",
            "--timeout",
            "20",
            "--cache-dir",
            str(CANDIDATE.ROOT / "output" / "real_page_cache"),
            "--cache-max-age",
            "900",
            "--archive-dir",
            str(CANDIDATE.ROOT / "output" / "real_page_archive" / "case-1"),
        ]
        self.assertEqual(call["command"], expected)
        kwargs = call["kwargs"]
        self.assertEqual(kwargs["cwd"], str(CANDIDATE.ROOT))
        self.assertIs(kwargs["capture_output"], True)
        self.assertIs(kwargs["text"], True)
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "strict")
        self.assertEqual(kwargs["timeout"], 20 + 45)
        self.assertIsInstance(kwargs["env"], dict)

    def test_case_fields_are_forwarded(self):
        case = self.valid_case(
            timeout_seconds=7,
            cache_max_age_seconds=0,
            fetch_profile="news",
            use_web_access=True,
            prefer_browser=True,
            selectors=["h1", {"selector": "#main"}],
            expect_site_profile="generic",
        )
        self.respond(self.ok_body())
        code, stdout, stderr = self.run_main(
            {"cases": [case]},
            argv=self.base_argv("--no-archive"),
        )
        self.assertEqual(code, 0)
        call = self.fake_subprocess.calls[0]
        command = call["command"]
        self.assertEqual(command[command.index("--timeout") + 1], "7")
        self.assertEqual(command[command.index("--cache-max-age") + 1], "0")
        self.assertEqual(command[command.index("--fetch-profile") + 1], "news")
        self.assertIn("--use-web-access", command)
        self.assertIn("--prefer-browser", command)
        self.assertEqual(command.count("--selector"), 2)
        first_selector = command.index("--selector")
        second_selector = command.index("--selector", first_selector + 1)
        self.assertEqual(command[first_selector + 1], "h1")
        self.assertEqual(command[second_selector + 1], "#main")
        self.assertNotIn("--archive-dir", command)
        self.assertEqual(call["kwargs"]["timeout"], 7 + 45)

    def test_global_timeout_is_used_when_case_has_none(self):
        self.respond(self.ok_body())
        self.run_main(self.valid_config(), argv=self.base_argv("--timeout", "9"))
        call = self.fake_subprocess.calls[0]
        self.assertEqual(call["command"][call["command"].index("--timeout") + 1], "9")
        self.assertEqual(call["kwargs"]["timeout"], 9 + 45)

    def test_environment_is_seeded_only_from_synthetic_os_mapping(self):
        self.fake_os.environ.clear()
        self.respond(self.ok_body())
        code, stdout, stderr = self.run_main(self.valid_config())
        self.assertEqual(code, 0)
        env = self.fake_subprocess.calls[0]["kwargs"]["env"]
        self.assertEqual(env, {"PYTHONIOENCODING": "utf-8"})
        self.assertIsNot(env, self.fake_os.environ)

    def test_existing_pythonioencoding_preserved_without_host_mutation(self):
        self.fake_os.environ = {
            "PYTHONIOENCODING": "latin-1",
            "WCF_SMOKE_TEST_SENTINEL": "kept",
        }
        self.respond(self.ok_body())
        code, stdout, stderr = self.run_main(self.valid_config())
        self.assertEqual(code, 0)
        env = self.fake_subprocess.calls[0]["kwargs"]["env"]
        self.assertEqual(env["PYTHONIOENCODING"], "latin-1")
        self.assertEqual(env["WCF_SMOKE_TEST_SENTINEL"], "kept")
        self.assertIsNot(env, self.fake_os.environ)
        # The candidate copies the mapping; the injected environ stays intact.
        self.assertEqual(
            self.fake_os.environ,
            {"PYTHONIOENCODING": "latin-1", "WCF_SMOKE_TEST_SENTINEL": "kept"},
        )

    def test_failed_case_is_not_retried(self):
        self.respond(self.ok_body(status="blocked"))
        code, stdout, stderr = self.run_main(self.valid_config())
        self.assertEqual(code, 1)
        self.assertEqual(len(self.fake_subprocess.calls), 1)

    def test_limit_runs_first_n_cases_in_order(self):
        cases = [
            self.valid_case(id=f"case-{index}", url=f"https://example.invalid/{index}")
            for index in range(1, 4)
        ]
        self.respond(self.ok_body(), self.ok_body())
        code, stdout, stderr = self.run_main(
            {"cases": cases}, argv=self.base_argv("--limit", "2")
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fake_subprocess.calls), 2)
        self.assertEqual(
            self.fake_subprocess.calls[0]["command"][4], "https://example.invalid/1"
        )
        self.assertEqual(
            self.fake_subprocess.calls[1]["command"][4], "https://example.invalid/2"
        )

    def test_empty_selector_entries_are_not_forwarded(self):
        case = self.valid_case(selectors=[])
        self.respond(self.ok_body())
        self.run_main({"cases": [case]})
        self.assertEqual(self.fake_subprocess.calls[0]["command"].count("--selector"), 0)


class RunCaseResponseTests(CandidateTestCase):
    def run_case(self, case=None, archive_root=None):
        return CANDIDATE.run_case(
            case if case is not None else self.valid_case(),
            timeout=20,
            archive_root=archive_root,
        )

    def test_success_row_contract(self):
        self.clock.values = [10.0, 10.5]
        self.respond(self.ok_body())
        row = self.run_case()
        self.assertTrue(row["ok"])
        self.assertEqual(row["id"], "case-1")
        self.assertEqual(row["url"], "https://example.invalid/page")
        self.assertIs(row["required"], True)
        self.assertEqual(row["duration_seconds"], 0.5)
        self.assertEqual(row["returncode"], 0)
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["transport"], "http")
        self.assertEqual(row["extracted_chars"], 40)
        self.assertEqual(row["selector_results"], 1)
        self.assertEqual(row["site_profile"], "generic")
        self.assertEqual(row["archive_path"], "C:/fake/archive/case-1")
        self.assertEqual(row["raw_sha256"], "a" * 64)
        self.assertEqual(row["extracted_sha256"], "b" * 64)
        self.assertEqual(row["check_failures"], [])
        self.assertEqual(row["notes"], "")
        self.assertEqual(row["stderr"], "")

    def test_archive_root_builds_case_directory(self):
        archive_root = pathlib.Path("C:/fake/archive-root")
        self.respond(self.ok_body())
        self.run_case(archive_root=archive_root)
        command = self.fake_subprocess.calls[0]["command"]
        self.assertEqual(
            command[command.index("--archive-dir") + 1],
            str(archive_root / "case-1"),
        )

    def test_child_timeout_is_a_bounded_case_failure(self):
        self.clock.values = [1.0, 1.25]
        self.respond(_REAL_TIMEOUT_EXPIRED("cmd", 5))
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertEqual(row["duration_seconds"], 0.25)
        self.assertIn("TimeoutExpired", row["failure"])
        self.assertIn("command", row)

    def test_os_error_propagates(self):
        self.respond(OSError("storage failure"))
        with self.assertRaises(OSError):
            self.run_case()

    def test_permission_error_propagates(self):
        self.respond(PermissionError("denied"))
        with self.assertRaises(PermissionError):
            self.run_case()

    def test_invalid_json_is_a_controlled_failure(self):
        self.respond("{not json")
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertIn("json:", row["failure"])
        self.assertIn("command", row)

    def test_empty_stdout_is_a_controlled_failure(self):
        self.respond("")
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertIn("json:", row["failure"])

    def test_non_object_json_is_a_controlled_failure(self):
        self.respond("[1, 2, 3]")
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertIn("json-shape:", row["failure"])

    def test_non_dict_site_data_is_a_controlled_failure(self):
        self.respond(self.ok_body(site_data=[]))
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertIn("site_data-shape:", row["failure"])

    def test_non_list_selector_results_is_a_controlled_failure(self):
        self.respond(self.ok_body(selector_results={"h1": 1}))
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertIn("selector_results-shape:", row["failure"])

    def test_non_string_stdout_is_a_controlled_failure(self):
        self.respond(FakeCompletedProcess(["cmd"], 0, stdout=b"\xff\xfe", stderr=""))
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertIn("stdout-type:bytes", row["failure"])

    def test_invalid_utf8_child_output_is_a_controlled_failure(self):
        self.respond(
            UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 2, "invalid start byte")
        )
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertIn("utf8:UnicodeDecodeError", row["failure"])
        self.assertEqual(len(self.fake_subprocess.calls), 1)

    def test_non_string_extracted_text_list_is_a_controlled_failure(self):
        self.respond(self.ok_body(extracted_text=["x" * 100]))
        row = self.run_case(case=self.valid_case(min_extracted_chars=5))
        self.assertFalse(row["ok"])
        self.assertIn("extracted_text-shape:list", row["failure"])

    def test_non_string_extracted_text_dict_is_a_controlled_failure(self):
        self.respond(self.ok_body(extracted_text={"text": "x" * 100}))
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertIn("extracted_text-shape:dict", row["failure"])

    def test_missing_extracted_text_counts_as_empty_for_threshold(self):
        self.respond(self.ok_body(extracted_text=None))
        row = self.run_case(case=self.valid_case(min_extracted_chars=1))
        self.assertFalse(row["ok"])
        self.assertIn("extracted_chars<1", row["check_failures"])

    def test_non_dict_archive_does_not_raise(self):
        self.respond(self.ok_body(archive="C:/fake/archive"))
        row = self.run_case()
        self.assertIsNone(row["archive_path"])

    def test_stderr_and_returncode_are_recorded_for_failed_returncodes(self):
        self.respond(
            FakeCompletedProcess(
                ["cmd"], 3, stdout=json.dumps(self.ok_body()), stderr="boom"
            )
        )
        row = self.run_case()
        self.assertFalse(row["ok"])
        self.assertEqual(row["returncode"], 3)
        self.assertEqual(row["stderr"], "boom")
        self.assertIn("returncode=3", row["check_failures"])

    def test_allow_status_fail_cannot_hide_an_unparseable_response(self):
        case = self.valid_case(required=False, allow_status_fail=True)
        self.respond("")
        row = self.run_case(case=case)
        self.assertFalse(row["ok"])


class EvaluateCaseTests(CandidateTestCase):
    def evaluate(self, case=None, body=None, returncode=0):
        payload = self.ok_body() if body is None else body
        return CANDIDATE.evaluate_case(
            case if case is not None else self.valid_case(),
            payload,
            returncode,
        )

    def test_ok_response_passes(self):
        result = self.evaluate()
        self.assertTrue(result["ok"])
        self.assertEqual(result["failures"], [])

    def test_returncode_zero_and_two_pass(self):
        for returncode in (0, 2):
            with self.subTest(returncode=returncode):
                self.assertTrue(self.evaluate(returncode=returncode)["ok"])

    def test_returncode_other_values_fail(self):
        for returncode in (1, 3):
            with self.subTest(returncode=returncode):
                result = self.evaluate(returncode=returncode)
                self.assertFalse(result["ok"])
                self.assertIn(f"returncode={returncode}", result["failures"])

    def test_status_contract(self):
        failing = self.evaluate(body=self.ok_body(status="blocked"))
        self.assertFalse(failing["ok"])
        self.assertIn("status=blocked", failing["failures"])
        allowed = self.evaluate(
            case=self.valid_case(allow_status_fail=True),
            body=self.ok_body(status="blocked"),
        )
        self.assertTrue(allowed["ok"])

    def test_min_extracted_chars_boundaries(self):
        case_zero = self.valid_case(min_extracted_chars=0)
        self.assertTrue(self.evaluate(case=case_zero, body=self.ok_body(extracted_text=""))["ok"])
        case_five = self.valid_case(min_extracted_chars=5)
        self.assertTrue(
            self.evaluate(case=case_five, body=self.ok_body(extracted_text="12345"))["ok"]
        )
        below = self.evaluate(case=case_five, body=self.ok_body(extracted_text="1234"))
        self.assertFalse(below["ok"])
        self.assertIn("extracted_chars<5", below["failures"])

    def test_min_selector_results_boundaries(self):
        case_zero = self.valid_case(min_selector_results=0)
        self.assertTrue(self.evaluate(case=case_zero, body=self.ok_body(selector_results=[]))["ok"])
        case_two = self.valid_case(min_selector_results=2)
        two = [{"selector": "h1"}, {"selector": "p"}]
        self.assertTrue(
            self.evaluate(case=case_two, body=self.ok_body(selector_results=two))["ok"]
        )
        below = self.evaluate(
            case=case_two, body=self.ok_body(selector_results=[{"selector": "h1"}])
        )
        self.assertFalse(below["ok"])
        self.assertIn("selector_results<2", below["failures"])

    def test_expected_profile_contract(self):
        case = self.valid_case(expect_site_profile="generic")
        self.assertTrue(self.evaluate(case=case, body=self.ok_body())["ok"])
        mismatch = self.evaluate(case=case, body=self.ok_body(site_data={"site_profile": "news"}))
        self.assertFalse(mismatch["ok"])
        missing = self.evaluate(case=case, body=self.ok_body(site_data=None))
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["failures"], ["site_profile=None expected='generic'"])

    def test_non_dict_body_is_not_a_false_green(self):
        result = CANDIDATE.evaluate_case(self.valid_case(), [], 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failures"], ["response-not-object"])


class MarkdownTests(CandidateTestCase):
    def report(self, **overrides):
        report = {
            "generated_at": "2026-11-08T00:00:00+00:00",
            "summary": {"total": 1, "ok": 0, "failed_required": 1, "failed_optional": 0},
            "strict": False,
            "cases": [
                {
                    "id": "case|one",
                    "ok": False,
                    "transport": "http|https",
                    "extracted_chars": 3,
                    "selector_results": 1,
                    "notes": "line one\nline two",
                    "check_failures": ["status=blocked|retry"],
                }
            ],
        }
        report.update(overrides)
        return report

    def test_markdown_escapes_pipes_and_newlines(self):
        markdown = CANDIDATE.render_markdown(self.report())
        self.assertIn("case\\|one", markdown)
        self.assertIn("http\\|https", markdown)
        self.assertIn("line one line two", markdown)
        self.assertIn("status=blocked\\|retry", markdown)
        self.assertNotIn("case|one", markdown.replace("case\\|one", ""))

    def test_markdown_table_shape_is_stable(self):
        markdown = CANDIDATE.render_markdown(self.report())
        lines = markdown.splitlines()
        self.assertEqual(len(lines), 8)
        self.assertTrue(lines[0].startswith("# Real Page Regression - "))
        self.assertEqual(
            lines[5], "| Case | OK | Transport | Chars | Selectors | Notes |"
        )
        self.assertEqual(lines[6], "|---|---:|---|---:|---:|---|")
        self.assertTrue(lines[7].startswith("| case\\|one | no |"))

    def test_markdown_reports_strict_mode(self):
        markdown = CANDIDATE.render_markdown(self.report(strict=True))
        self.assertIn("- Strict mode: True", markdown)


class MainFlowTests(CandidateTestCase):
    def test_all_pass_exit_zero_and_writes_two_artifacts(self):
        self.respond(self.ok_body())
        code, stdout, stderr = self.run_main(self.valid_config())
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), json.dumps({"total": 1, "ok": 1, "failed_required": 0, "failed_optional": 0}))
        self.assertEqual(len(FakePath.writes), 2)
        self.assertEqual(FakePath.writes[0][0], OUTPUT_PATH)
        self.assertEqual(FakePath.writes[1][0], OUTPUT_MARKDOWN_PATH)
        self.assertEqual(FakePath.writes[0][2], "utf-8")
        self.assertEqual(FakePath.writes[0][3], "\n")
        self.assertEqual(FakePath.writes[1][2], "utf-8")
        self.assertEqual(FakePath.writes[1][3], "\n")
        self.assertEqual(FakePath.mkdirs, [("C:/fake/reports", True, True)])

    def test_report_json_is_a_decodable_structure(self):
        self.respond(self.ok_body())
        self.run_main(self.valid_config())
        report = json.loads(self.report_writes()[0][1])
        self.assertIsInstance(report, dict)
        self.assertEqual(list(report), ["generated_at", "config", "strict", "summary", "cases"])
        self.assertEqual(report["config"], CONFIG_PATH)
        self.assertIs(report["strict"], False)
        self.assertEqual(report["summary"], {"total": 1, "ok": 1, "failed_required": 0, "failed_optional": 0})
        self.assertEqual(len(report["cases"]), 1)
        row = report["cases"][0]
        self.assertEqual(row["id"], "case-1")
        self.assertIs(row["ok"], True)
        self.assertEqual(row["returncode"], 0)
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["extracted_chars"], 40)
        self.assertEqual(row["selector_results"], 1)
        self.assertEqual(row["site_profile"], "generic")
        self.assertEqual(row["archive_path"], "C:/fake/archive/case-1")
        self.assertEqual(row["notes"], "")

    def test_report_markdown_is_written_for_the_case(self):
        self.respond(self.ok_body())
        self.run_main(self.valid_config())
        markdown = self.markdown_writes()[0][1]
        self.assertIn("# Real Page Regression - ", markdown)
        self.assertIn("| case-1 | yes | http | 40 | 1 |", markdown)

    def test_required_failure_exits_one(self):
        self.respond(self.ok_body(status="blocked"))
        code, stdout, stderr = self.run_main(self.valid_config())
        self.assertEqual(code, 1)
        report = json.loads(self.report_writes()[0][1])
        self.assertEqual(report["summary"]["failed_required"], 1)
        self.assertEqual(report["summary"]["failed_optional"], 0)

    def test_optional_failure_exits_zero(self):
        case = self.valid_case(required=False)
        self.respond(self.ok_body(status="blocked"))
        code, stdout, stderr = self.run_main({"cases": [case]})
        self.assertEqual(code, 0)
        report = json.loads(self.report_writes()[0][1])
        self.assertEqual(report["summary"]["failed_optional"], 1)

    def test_strict_optional_failure_exits_one(self):
        case = self.valid_case(required=False)
        self.respond(self.ok_body(status="blocked"))
        code, stdout, stderr = self.run_main(
            {"cases": [case]}, argv=self.base_argv("--strict")
        )
        self.assertEqual(code, 1)

    def test_timeout_continues_to_next_case(self):
        cases = [
            self.valid_case(id="first", required=False),
            self.valid_case(id="second", required=False),
        ]
        self.respond(_REAL_TIMEOUT_EXPIRED("cmd", 5), self.ok_body())
        code, stdout, stderr = self.run_main({"cases": cases})
        self.assertEqual(code, 0)
        report = json.loads(self.report_writes()[0][1])
        self.assertEqual(report["summary"], {"total": 2, "ok": 1, "failed_required": 0, "failed_optional": 1})
        self.assertIn("TimeoutExpired", report["cases"][0]["failure"])

    def test_permission_error_stops_remaining_cases_and_writes_nothing(self):
        cases = [
            self.valid_case(id="first"),
            self.valid_case(id="second"),
        ]
        FakePath.files[CONFIG_PATH] = json.dumps({"cases": cases})
        self.respond(PermissionError("denied"))
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(PermissionError):
                CANDIDATE.main(["--config", CONFIG_PATH, "--output", OUTPUT_PATH])
        self.assertEqual(len(self.fake_subprocess.calls), 1)
        self.assertEqual(FakePath.writes, [])

    def test_other_os_error_stops_remaining_cases_and_writes_nothing(self):
        cases = [self.valid_case(id="first"), self.valid_case(id="second")]
        FakePath.files[CONFIG_PATH] = json.dumps({"cases": cases})
        self.respond(OSError("storage failure"))
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(OSError):
                CANDIDATE.main(["--config", CONFIG_PATH, "--output", OUTPUT_PATH])
        self.assertEqual(len(self.fake_subprocess.calls), 1)
        self.assertEqual(FakePath.writes, [])

    def test_invalid_response_writes_controlled_report(self):
        self.respond("{not json")
        code, stdout, stderr = self.run_main(self.valid_config())
        self.assertEqual(code, 1)
        report = json.loads(self.report_writes()[0][1])
        self.assertFalse(report["cases"][0]["ok"])
        self.assertIn("json:", report["cases"][0]["failure"])
        self.assertIn("| case-1 | no |", self.markdown_writes()[0][1])

    def test_default_output_path_is_used_when_not_overridden(self):
        self.respond(self.ok_body())
        code, stdout, stderr = self.run_main(
            self.valid_config(), argv=["--config", CONFIG_PATH]
        )
        self.assertEqual(code, 0)
        default_path = str(CANDIDATE.DEFAULT_OUTPUT)
        self.assertEqual(FakePath.writes[0][0], default_path)
        self.assertEqual(FakePath.writes[1][0], default_path[:-5] + ".md")

    def test_no_archive_omits_archive_directory(self):
        self.respond(self.ok_body())
        self.run_main(self.valid_config(), argv=self.base_argv("--no-archive"))
        self.assertNotIn("--archive-dir", self.fake_subprocess.calls[0]["command"])

    def test_summary_line_has_exact_counts(self):
        cases = [
            self.valid_case(id="good", required=True),
            self.valid_case(id="bad", required=True),
        ]
        self.respond(self.ok_body(), self.ok_body(status="blocked"))
        code, stdout, stderr = self.run_main({"cases": cases})
        self.assertEqual(code, 1)
        self.assertEqual(
            json.loads(stdout.strip()),
            {"total": 2, "ok": 1, "failed_required": 1, "failed_optional": 0},
        )


if __name__ == "__main__":
    unittest.main()
