"""Opt-in real GPU integration test; no downloaded fixture is redistributed."""
import json
import os
import subprocess

import pytest


def test_real_cubism_pixels_and_parameters():
    manifest = os.environ.get('LIVE2D_TEST_MANIFEST')
    python = os.environ.get('LIVE2D_TEST_PYTHON')
    if not manifest or not python:
        pytest.skip('Set LIVE2D_TEST_MANIFEST and LIVE2D_TEST_PYTHON for a real GPU test')
    result = subprocess.run([python, '-m', 'drivers.live2d_runtime.verify', manifest],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [s for s in result.stdout.splitlines() if s.startswith('LIVE2D_VERIFICATION=')]
    assert len(lines) == 1
    report = json.loads(lines[0].split('=', 1)[1])
    assert report['runtime_verified'] is True
    assert report['checks'] and all(c['passed'] for c in report['checks'])
