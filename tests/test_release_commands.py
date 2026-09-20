"""Exercise the Poetry-based release ordering with stand-in tools only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from heckle import __version__

ROOT = Path(__file__).resolve().parents[1]

TOOL = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
stage = name
if name == 'poetry':
    if args[:1] == ['build']:
        stage = 'poetry:build'
    elif args[:1] == ['publish']:
        stage = 'poetry:publish'
    elif args[:2] == ['run', 'pyproject-appimage']:
        stage = 'poetry:appimage'
    elif args[:2] == ['run', 'pytest']:
        stage = 'poetry:pytest'
    else:
        stage = 'poetry:run'
elif name == 'docker' and args:
    stage = 'docker:' + args[0]
with open(os.environ['TOOL_LOG'], 'a') as f:
    f.write(json.dumps([stage, args]) + '\n')
if os.environ.get('FAIL_STAGE') == stage:
    sys.exit(19)
root = pathlib.Path.cwd()
if stage == 'poetry:build':
    dist = root / 'dist'; dist.mkdir(exist_ok=True)
    (dist / f'heckle-{os.environ["VERSION"]}-py3-none-any.whl').write_text('wheel')
    (dist / f'heckle-{os.environ["VERSION"]}.tar.gz').write_text('sdist')
elif stage == 'poetry:appimage':
    output = pathlib.Path(args[args.index('--output') + 1])
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text('appimage')
elif name == 'docker' and args[0] == 'run':
    mount = next(a for a in args if a.endswith(':/out'))
    output = pathlib.Path(mount[:-5]); output.mkdir(parents=True, exist_ok=True)
    image = args[-1]
    if 'deb' in image:
        (output / 'heckle.deb').write_text('deb')
    else:
        (output / 'heckle.noarch.rpm').write_text('rpm')
elif name == 'qubes-gpg-client':
    print('detached signature')
elif name == 'rpmsign':
    pathlib.Path(args[-1]).write_text(pathlib.Path(args[-1]).read_text() + '\nsigned')
'''


@pytest.fixture
def release(tmp_path):
    project = tmp_path / 'project with spaces'
    project.mkdir()
    for filename in ('release.sh', 'tests.sh', 'pyproject.toml'):
        shutil.copy2(ROOT / filename, project / filename)
    tools = tmp_path / 'tools'; tools.mkdir()
    for name in ('filedust', 'poetry', 'docker', 'qubes-gpg-client', 'rpmsign', 'sudo'):
        path = tools / name
        if name == 'sudo':
            path.write_text('#!/bin/sh\nexit 0\n')
        else:
            path.write_text(f'#!{sys.executable} -S\n' + TOOL)
        path.chmod(0o755)
    env = {
        **os.environ,
        'PATH': str(tools) + os.pathsep + os.environ['PATH'],
        'TOOL_LOG': str(tmp_path / 'calls'),
        'VERSION': __version__,
        'USER': 'tester',
    }
    def run(**extra):
        result = subprocess.run(['bash', str(project / 'release.sh')], cwd=project,
                                env={**env, **extra}, text=True, capture_output=True)
        log = Path(env['TOOL_LOG'])
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return project, run


@pytest.mark.parametrize('stage', ['poetry:pytest', 'poetry:build', 'poetry:appimage', 'docker:build', 'docker:run', 'rpmsign', 'qubes-gpg-client'])
def test_failed_step_never_publishes(release, stage):
    _, run = release
    result, calls = run(FAIL_STAGE=stage)
    assert result.returncode != 0
    assert not any(call_stage == 'poetry:publish' for call_stage, _ in calls)

