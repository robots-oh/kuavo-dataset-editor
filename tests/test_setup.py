"""No network/install/robot access: platform selection and activation failure."""
import importlib.util
from pathlib import Path
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('setup_env', Path(__file__).resolve().parents[1] / 'tools/setup_env.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def select(args=(), machine='aarch64', version='2.31', environ=None):
    return setup.options(args, machine=machine, system='Linux', libc=('glibc', version), environ=environ or {})[1]


def test_platform_defaults_and_explicit_features():
    assert select()['extras'] == ['export']
    assert select(machine='x86_64', version='2.35', environ={'DISPLAY': ':0'})['extras'] == ['export', 'desktop']
    assert select(['--headless'], machine='x86_64', environ={'DISPLAY': ':0'})['extras'] == ['export']
    assert select(['--minimal'])['extras'] == []
    assert select(['--minimal', '--with-export']) == select(['--with-export', '--minimal'])
    with pytest.raises(ValueError, match='glibc'):
        select(['--with-desktop'])
    with pytest.raises(ValueError, match='Supported'):
        select(machine='armv7l')
    with pytest.raises(ValueError, match='glibc'):
        select(version='2.27')


def test_targets_preserve_existing_environments(tmp_path):
    (tmp_path / 'uv.lock').write_text('fixture')
    old = tmp_path / '.venv'
    old.mkdir()
    arm = setup.target_environment(tmp_path, select())
    x86 = setup.target_environment(tmp_path, select(machine='x86_64'))
    assert arm != x86 and arm != old
    with pytest.raises(ValueError):
        setup.target_environment(tmp_path, select(), str(old))
    with pytest.raises(ValueError):
        setup.target_environment(tmp_path, select(), str(tmp_path.parent / 'foreign-env'))


def test_failed_doctor_does_not_activate_new_runtime(tmp_path, monkeypatch):
    (tmp_path / 'uv.lock').write_text('fixture')
    (tmp_path / '.bootstrap/bin').mkdir(parents=True)
    (tmp_path / '.bootstrap/bin/uv').touch()
    marker = tmp_path / '.editor-python'
    marker.write_text('.venv/bin/python\n')
    selection = select()
    monkeypatch.setattr(setup, 'options', lambda args: (type('Args', (), {'plan': False, 'fresh': False})(), selection))
    commands = []
    def execute(command, **kwargs):
        commands.append(command)
        assert 'PYTHONPATH' not in kwargs['env']
        assert 'LD_LIBRARY_PATH' not in kwargs['env']
        if command[-1] == '--version':
            return subprocess.CompletedProcess(command, 0, stdout='uv 0.13.1\n')
        if 'doctor' in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(setup.subprocess, 'run', execute)
    with pytest.raises(subprocess.CalledProcessError):
        setup.run(tmp_path, [])
    assert marker.read_text() == '.venv/bin/python\n'
    assert not any('sudo' in command or 'apt' in command for command in commands)
