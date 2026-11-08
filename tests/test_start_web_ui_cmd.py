"""Explicit Windows-only synthetic launcher contract; never starts the real UI."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows cmd.exe is required; no alternate shell is used.')
    root = args.output_root.absolute()
    if root.exists():
        parser.error('Output root must not exist; old evidence is never cleared.')
    if any(c in str(root) for c in '%!^&|<>()"\r\n'):
        parser.error('Use a test-owned output path without batch metacharacters.')
    root.mkdir(parents=True)
    fixture = root / 'SYNTHETIC repo with space'
    (fixture / 'scripts').mkdir(parents=True)
    package = fixture / 'web_content_fetcher'
    package.mkdir()
    caller = root / 'SYNTHETIC caller with space'
    caller.mkdir()
    source = Path(__file__).resolve().parents[1] / 'scripts/start-web-ui.cmd'
    shutil.copyfile(source, fixture / 'scripts/start-web-ui.cmd')
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / 'ui.py').write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['WCF_SYNTHETIC_MARKER']).write_text(json.dumps({"
        "'synthetic_only':True,'cwd':os.getcwd(),'pythonpath':os.environ.get('PYTHONPATH'),"
        "'python':sys.executable,'argv':sys.argv[1:]}),encoding='utf-8')\n"
        "raise SystemExit(int(os.environ['WCF_SYNTHETIC_EXIT']))\n", encoding='utf-8')
    driver = root / 'synthetic-driver.cmd'
    driver.write_bytes((
        '@echo off\ncd /d "%WCF_SYNTHETIC_CALLER%" || exit /b 1\n'
        'set "_WCF_UI_EXIT=SYNTHETIC previous exit variable"\n'
        'call "%WCF_SYNTHETIC_LAUNCHER%" --host "SYNTHETIC host" --port 7319\n'
        'set "_SYNTHETIC_DRIVER_EXIT=%errorlevel%"\n'
        '> "%WCF_SYNTHETIC_CWD_REPORT%" cd\n'
        'if defined PYTHONPATH (set PYTHONPATH > "%WCF_SYNTHETIC_ENV_REPORT%") else (echo ABSENT> "%WCF_SYNTHETIC_ENV_REPORT%")\n'
        'set _WCF_UI_EXIT > "%WCF_SYNTHETIC_VAR_REPORT%"\n'
        'exit /b %_SYNTHETIC_DRIVER_EXIT%\n').replace('\n', '\r\n').encode('ascii'))
    cmd = Path(os.environ['SystemRoot']) / 'System32/cmd.exe'
    assert cmd.is_file()
    results = []
    for name, child_exit, present, missing in [
        ('zero-present', 0, True, False), ('seven-present', 7, True, False),
        ('zero-absent', 0, False, False), ('missing-python', 0, True, True),
    ]:
        marker = root / (name + '-child.json')
        cwd_report = root / (name + '-cwd.txt')
        env_report = root / (name + '-pythonpath.txt')
        var_report = root / (name + '-variable.txt')
        env = dict(os.environ)
        env.pop('PYTHONHOME', None)
        env['PYTHONNOUSERSITE'] = '1'
        env['PATH'] = str(cmd.parent) if missing else str(Path(sys.executable).parent) + os.pathsep + str(cmd.parent)
        if present:
            env['PYTHONPATH'] = 'SYNTHETIC previous Python path'
        else:
            env.pop('PYTHONPATH', None)
        env.update(WCF_SYNTHETIC_MARKER=str(marker), WCF_SYNTHETIC_EXIT=str(child_exit),
                   WCF_SYNTHETIC_CALLER=str(caller), WCF_SYNTHETIC_LAUNCHER=str(fixture / 'scripts/start-web-ui.cmd'),
                   WCF_SYNTHETIC_CWD_REPORT=str(cwd_report), WCF_SYNTHETIC_ENV_REPORT=str(env_report),
                   WCF_SYNTHETIC_VAR_REPORT=str(var_report))
        run = subprocess.run([str(cmd), '/d', '/c', 'call', str(driver)], cwd=root, env=env,
                             capture_output=True, timeout=30)
        (root / (name + '-stdout.log')).write_bytes(run.stdout)
        (root / (name + '-stderr.log')).write_bytes(run.stderr)
        expected = 9009 if missing else child_exit
        assert run.returncode == expected, (name, expected, run.returncode)
        assert Path(cwd_report.read_text(encoding='utf-8').strip()) == caller
        assert env_report.read_text(encoding='utf-8').strip() == ('PYTHONPATH=SYNTHETIC previous Python path' if present else 'ABSENT')
        assert var_report.read_text(encoding='utf-8').strip() == '_WCF_UI_EXIT=SYNTHETIC previous exit variable'
        assert marker.exists() is not missing
        if not missing:
            child = json.loads(marker.read_text(encoding='utf-8'))
            assert child['synthetic_only'] and Path(child['cwd']) == fixture and Path(child['pythonpath']) == fixture
            assert Path(child['python']) == Path(sys.executable)
            assert child['argv'] == ['--host', 'SYNTHETIC host', '--port', '7319']
        results.append({'name': name, 'passed': True, 'expected_exit': expected, 'observed_exit': run.returncode})
    receipt = {'synthetic_only': True, 'passed': len(results), 'failed': 0, 'skip': 0,
               'cases': results, 'real_WCF_UI_started': False, 'network_browser_model': False,
               'security_settings_changed': False}
    (root / 'RESULT.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(f'PASS {len(results)} synthetic cmd launcher cases; no real UI/server/network.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
