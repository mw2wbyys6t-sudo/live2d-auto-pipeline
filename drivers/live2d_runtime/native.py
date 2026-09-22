"""Native Cubism rendering. Caller owns an active OpenGL context and SDK lifecycle.

Unlike renderer.Live2DRenderer this never composites atlases as PNG layers.
Successful drawing alone is NOT deployment or visual-motion acceptance.
"""
from __future__ import annotations

import importlib
import json
import math
from pathlib import Path, PureWindowsPath


def validate_package(manifest: str) -> tuple[Path, dict]:
    path = Path(manifest).resolve(strict=True)
    if not path.name.endswith('.model3.json'):
        raise ValueError('Expected a .model3.json manifest')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('Version') != 3:
        raise ValueError('Unsupported model3 manifest schema')
    refs = data.get('FileReferences')
    if not isinstance(refs, dict):
        raise ValueError('Missing FileReferences')

    def check(value):
        if not isinstance(value, str) or not value:
            raise ValueError('Empty or non-string asset reference')
        if PureWindowsPath(value).drive or value.startswith(('/', '\\')):
            raise ValueError('Absolute asset reference forbidden')
        target = (path.parent / value.replace('\\', '/')).resolve(strict=True)
        if not target.is_relative_to(path.parent) or not target.is_file():
            raise ValueError('Asset reference escapes model directory')
        return target

    moc = check(refs.get('Moc'))
    with moc.open('rb') as stream:
        header = stream.read(64)
    if len(header) < 64 or header[:4] != b'MOC3':
        raise ValueError('Invalid or truncated moc3 header; Cubism export required')
    textures = refs.get('Textures')
    if not isinstance(textures, list) or not textures:
        raise ValueError('At least one texture is required')
    for texture in textures:
        check(texture)
    # Validate every SDK file reference, including motion sound and optional files.
    def walk(value, key=''):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif value and key not in ('Name', 'FadeInTime', 'FadeOutTime'):
            # 空字符串表示「这个可选文件没有」（导出器在没有可用物理链时
            # 就不写 Physics 引用）。必需的 Moc/Textures 在上面已单独校验。
            check(value)
    walk(refs)
    return path, data


def package_sha256(manifest):
    """Fingerprint package bytes, rejecting symlinks outside the model root."""
    import hashlib
    path, _ = validate_package(manifest)
    digest = hashlib.sha256()
    for asset in sorted(path.parent.rglob('*')):
        if not asset.resolve().is_relative_to(path.parent):
            raise ValueError('Package entry escapes model directory')
        if asset.is_file():
            digest.update(asset.relative_to(path.parent).as_posix().encode())
            digest.update(asset.read_bytes())
    return digest.hexdigest()


class CubismRenderer:
    """Single-thread/context native renderer; SDK initialization belongs to host."""

    def __init__(self, width=600, height=800):
        if width <= 0 or height <= 0:
            raise ValueError('Canvas dimensions must be positive')
        self.width, self.height = width, height
        self._model = None
        self.parameter_ids = []
        self.frames_drawn = 0
        self.parameter_write_verified = False
        self.last_error = None

    def close(self):
        model, self._model = self._model, None
        self.parameter_ids = []
        self.frames_drawn = 0
        self.parameter_write_verified = False
        if model is not None:
            model.DestroyRenderer()

    def load_model(self, manifest: str):
        self.close()
        self.last_error = None
        try:
            path, data = validate_package(manifest)
            sdk = importlib.import_module('live2d.v3')
            model = sdk.LAppModel()
            self._model = model
            moc = path.parent / data['FileReferences']['Moc'].replace('\\', '/')
            if not model.HasMocConsistencyFromFile(str(moc)):
                raise ValueError('Cubism Core rejected moc3 consistency')
            model.LoadModelJson(str(path))
            model.Resize(self.width, self.height)
            self.parameter_ids = list(model.GetParamIds())
            if not self.parameter_ids:
                raise ValueError('No native parameters loaded; driven model acceptance blocked')
        except Exception as exc:
            self.last_error = str(exc)
            self.close()
            raise

    def set_parameter(self, name: str, value: float):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        if name not in self.parameter_ids:
            raise KeyError(name)
        value = float(value)
        if not math.isfinite(value):
            raise ValueError('Parameter value must be finite')
        index = self.parameter_ids.index(name)
        definition = self._model.GetParameter(index)
        value = max(definition.min, min(definition.max, value))
        before = self._model.GetParameterValue(index)
        self._model.SetParameterValue(name, value)
        after = self._model.GetParameterValue(index)
        if not math.isclose(after, value, abs_tol=1e-5):
            raise RuntimeError('Native parameter readback did not match requested value')
        if not math.isclose(before, after, abs_tol=1e-5):
            self.parameter_write_verified = True
        return after

    def draw(self):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        try:
            self._model.Update()
            self._model.Draw()
            self.frames_drawn += 1
        except Exception as exc:
            self.last_error = str(exc)
            self.close()
            raise

    def report(self):
        return {
            'backend': 'cubism_native',
            'native_loaded': self._model is not None,
            'frames_drawn': self.frames_drawn,
            'parameter_write_verified': self.parameter_write_verified,
            'runtime_verified': False,
            'deployment_status': 'requires_visual_verification' if self.frames_drawn else 'blocked',
            'error': self.last_error,
        }
