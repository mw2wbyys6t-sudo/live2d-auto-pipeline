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


def package_sha256(manifest, exclude=()):
    """Fingerprint package bytes, rejecting symlinks outside the model root.

    ``exclude`` 是不计入指纹的路径（文件或目录）。受控像素验证会把证据图写进
    产物目录，那是验证过程自己的输出、不属于模型，若不排除就会让「验证期间包没变」
    这条判断对任何把证据放在产物目录里的调用方恒假。
    """
    import hashlib
    path, _ = validate_package(manifest)
    root = path.parent.resolve()
    skip = [Path(item).resolve() for item in exclude if item]
    digest = hashlib.sha256()
    for asset in sorted(root.rglob('*')):
        if not asset.resolve().is_relative_to(root):
            raise ValueError('Package entry escapes model directory')
        if any(asset.resolve() == item or asset.resolve().is_relative_to(item)
               for item in skip):
            continue
        if asset.is_file():
            digest.update(asset.relative_to(root).as_posix().encode())
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
            # live2d-py 0.5.4（内置 Cubism Core 5.0.0）**没有** DestroyRenderer；
            # 早期版本有。直接调用会让 close() 抛 AttributeError，从而把「模型
            # 加载成功」误报成失败（实测：moc3_load 子进程正常打印了参数表，却
            # 因为退出时的 AttributeError 被判成「Core 判定 moc3 不一致」）。
            # 因此按能力探测，两个版本都能干净收尾。
            destroy = getattr(model, 'DestroyRenderer', None)
            if callable(destroy):
                destroy()

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


class CubismModelSession:
    """直接驱动 Cubism SDK 的 ``Model``，而不是 ``LAppModel`` 示例外壳。

    验收与交互播放的需求不同，所以是两套对象：

    * ``CubismRenderer``（LAppModel）面向桌面宠物 —— 它自带真实时钟、拖拽、
      自动眨眼与呼吸，正是运行时该有的行为。
    * 本类面向 moc3 验收 —— 验收必须能**逐帧指定 deltaTime**（冻结时间，
      否则物理与呼吸会把「参数没起作用」伪装成「画面变了」），并且要直接调用
      ``UpdateMotion`` / ``UpdatePhysics`` / ``LoadParameters`` /
      ``SaveParameters`` 做逐帧对账。

    LAppModel **不暴露**内部 Model，上面这些全都做不到；live2d-py 0.5.4 暴露的
    ``live2d.v3.Model`` 才是原生可控入口（实测 2026-10：键形模型在
    ``Model`` 上位移 200 画布像素，与静态参照逐像素一致）。

    注意 ``Model.Update`` 会先 ``LoadParameters``（当前值 ← 已保存值），因此参数
    必须用 ``SetAndSaveParameterValueById`` 写，否则下一次 Update 会把写入抹掉。
    """

    def __init__(self, width=600, height=800, mask_buffer_count=1):
        if width <= 0 or height <= 0:
            raise ValueError('Canvas dimensions must be positive')
        self.width, self.height = width, height
        self._mask_buffer_count = mask_buffer_count
        self._model = None
        self.parameter_ids = []
        self.frames_drawn = 0
        self.last_error = None

    def load_model(self, manifest: str):
        self.close()
        self.last_error = None
        try:
            path, _ = validate_package(manifest)
            sdk = importlib.import_module('live2d.v3')
            model = sdk.Model()
            model.LoadModelJson(str(path))
            model.Resize(self.width, self.height)
            model.CreateRenderer(self._mask_buffer_count)
            self._model = model
            self.parameter_ids = [str(i) for i in model.GetParameterIds()]
            if not self.parameter_ids:
                raise ValueError(
                    'No native parameters loaded; driven model acceptance blocked')
        except Exception as exc:
            self.last_error = str(exc)
            self.close()
            raise

    def close(self):
        model, self._model = self._model, None
        self.parameter_ids = []
        self.frames_drawn = 0
        if model is not None:
            # 必须在 GL 上下文仍然有效时释放；调用方负责先 close() 再 glRelease()。
            destroy = getattr(model, 'DestroyRenderer', None)
            if callable(destroy):
                destroy()

    def parameter_index(self, name: str) -> int:
        try:
            return self.parameter_ids.index(name)
        except ValueError:
            raise KeyError(name) from None

    def parameter_bounds(self, name: str) -> tuple:
        if self._model is None:
            raise RuntimeError('No native model loaded')
        index = self.parameter_index(name)
        return (float(self._model.GetParameterMinimumValue(index)),
                float(self._model.GetParameterMaximumValue(index)))

    def get_parameter_value(self, index: int) -> float:
        if self._model is None:
            raise RuntimeError('No native model loaded')
        return float(self._model.GetParameterValue(index))

    def set_parameter(self, name: str, value: float) -> float:
        if self._model is None:
            raise RuntimeError('No native model loaded')
        value = float(value)
        if not math.isfinite(value):
            raise ValueError('Parameter value must be finite')
        low, high = self.parameter_bounds(name)
        value = max(low, min(high, value))
        index = self.parameter_index(name)
        self._model.SetAndSaveParameterValueById(name, value, 1.0)
        after = float(self._model.GetParameterValue(index))
        if not math.isclose(after, value, abs_tol=1e-5):
            raise RuntimeError('Native parameter readback did not match requested value')
        return after

    def update(self, delta_seconds: float = 0.0):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.Update(float(delta_seconds))

    def update_motion(self, delta_seconds: float):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.UpdateMotion(float(delta_seconds))

    def update_physics(self, delta_seconds: float):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.UpdatePhysics(float(delta_seconds))

    def load_parameters(self):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.LoadParameters()

    def save_parameters(self):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.SaveParameters()

    def set_auto_blink(self, enabled: bool):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.SetAutoBlink(bool(enabled))

    def set_auto_breath(self, enabled: bool):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.SetAutoBreath(bool(enabled))

    def stop_all_motions(self):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.StopAllMotions()

    def start_motion(self, group: str, no: int, priority: int = 3):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        self._model.StartMotion(group, no, priority)

    def is_motion_finished(self) -> bool:
        if self._model is None:
            raise RuntimeError('No native model loaded')
        return bool(self._model.IsMotionFinished())

    def get_motions(self) -> dict:
        if self._model is None:
            raise RuntimeError('No native model loaded')
        return dict(self._model.GetMotions() or {})

    def draw(self):
        if self._model is None:
            raise RuntimeError('No native model loaded')
        try:
            self._model.Draw()
            self.frames_drawn += 1
        except Exception as exc:
            self.last_error = str(exc)
            self.close()
            raise
