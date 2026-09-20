from pathlib import Path
import subprocess
import tomllib
import pytest

from heckle import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_package_is_dependency_free():
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert metadata['project']['version'] == __version__
    assert metadata['project']['dependencies'] == []
    assert metadata['project']['scripts'] == {'heckle': 'heckle.cli:main'}
    assert metadata['project']['requires-python'] == '>=3.11,<4.0'
    assert {
        'path': 'tools/repair_forgejo_alpha3.py',
        'format': ['sdist', 'wheel'],
    } in metadata['tool']['poetry']['include']
    assert {'path': 'tests', 'format': 'sdist'} in metadata['tool']['poetry']['include']


def test_release_metadata_agrees():
    import re
    if not (ROOT / 'debian/changelog').is_file():
        pytest.skip('Debian/RPM source metadata is not part of the installed wheel')
    assert re.search(r"heckle \(" + re.escape(__version__) + r"-1(?:~[^)]*)?\)", (ROOT / 'debian/changelog').read_text().splitlines()[0])
    assert __version__ in (ROOT / 'rpm/heckle.spec').read_text()
    assert __version__ in (ROOT / 'CHANGELOG.md').read_text()


def test_unreleased_history_uses_one_alpha_sequence():
    import re
    if not (ROOT / 'CHANGELOG.md').is_file():
        pytest.skip('source changelog is not part of the installed wheel')
    headings = re.findall(r'^## (\S+)', (ROOT / 'CHANGELOG.md').read_text(), re.MULTILINE)
    assert headings == ['0.1.0'] + [f'0.1.0-alpha{i}' for i in range(13, 0, -1)]


def test_shell_scripts_parse():
    if not all((ROOT / name).is_file() for name in ('tests.sh', 'release.sh')):
        pytest.skip('release scripts are not part of the installed wheel')
    for name in ('tests.sh', 'release.sh'):
        subprocess.run(['bash', '-n', str(ROOT / name)], check=True)


def test_rpm_source_archive_avoids_tar_transform():
    dockerfile_path = ROOT / 'Dockerfile.rpmbuild'
    if not dockerfile_path.is_file():
        pytest.skip('RPM build files are not part of the installed wheel')
    dockerfile = dockerfile_path.read_text()
    assert '--transform' not in dockerfile
    assert 'SOURCE_STAGE=$(mktemp -d "$WORKROOT/source.XXXXXX")' in dockerfile
    assert 'tar -C "$SOURCE_STAGE" -czf' in dockerfile
    assert '"heckle-$ver"' in dockerfile
