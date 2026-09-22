"""Native desktop launch handshake, using real SDK only when opted in."""
import json
import os
import subprocess
import pytest


def target():
    manifest = os.environ.get('LIVE2D_TEST_MANIFEST')
    python = os.environ.get('LIVE2D_TEST_PYTHON')
    if not manifest or not python:
        pytest.skip('Real GPU environment not configured')
    return python, manifest


def test_native_pet_first_frame_and_clean_exit():
    python, manifest = target()
    p = subprocess.run([python, '-m', 'drivers.desktop_pet.native_window', manifest,
                        '--frames', '8'], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    reports = [json.loads(s.split('=', 1)[1]) for s in p.stdout.splitlines()
               if s.startswith('LIVE2D_PET=')]
    assert len(reports) == 1
    assert reports[0]['frames_drawn'] == 1
    assert reports[0]['pid'] > 0
    assert len(reports[0]['package_sha256']) == 64
    assert reports[0]['visual_desktop_verified'] is False
    assert '"frames_drawn": 8' in p.stdout


def test_modified_package_refused_before_window():
    python, manifest = target()
    p = subprocess.run([python, '-m', 'drivers.desktop_pet.native_window', manifest,
                        '--frames', '2', '--expected-sha256', '0' * 64],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode != 0
    assert 'LIVE2D_PET=' not in p.stdout
    assert 'Model changed after verification' in p.stderr
