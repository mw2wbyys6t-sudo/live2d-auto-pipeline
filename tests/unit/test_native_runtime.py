"""Contract tests with a fake SDK, NOT real-model runtime acceptance."""
import json
from types import SimpleNamespace

import pytest
from drivers.live2d_runtime import native


@pytest.fixture
def package(tmp_path):
    (tmp_path / 'hero.moc3').write_bytes(b'MOC3' + bytes(60))
    (tmp_path / 'texture.png').write_bytes(b'fixture')
    path = tmp_path / 'hero.model3.json'
    path.write_text(json.dumps({'Version': 3, 'FileReferences': {
        'Moc': 'hero.moc3', 'Textures': ['texture.png']}}))
    return path


class Model:
    def __init__(self):
        self.value = 0
        self.destroyed = False
        self.events = []

    def HasMocConsistencyFromFile(self, path): return True
    def LoadModelJson(self, path): self.events.append('load')
    def Resize(self, w, h): pass
    def GetParamIds(self): return ['ParamAngleX']
    def GetParameter(self, index): return SimpleNamespace(min=-30, max=30)
    def GetParameterValue(self, index): return self.value
    def SetParameterValue(self, name, value): self.value = value
    def Update(self): self.events.append('update')
    def Draw(self): self.events.append('draw')
    def DestroyRenderer(self): self.destroyed = True


@pytest.fixture
def sdk(monkeypatch):
    model = Model()
    monkeypatch.setattr(native.importlib, 'import_module', lambda _: SimpleNamespace(LAppModel=lambda: model))
    return model


def test_native_contract_does_not_claim_acceptance(package, sdk):
    renderer = native.CubismRenderer()
    renderer.load_model(str(package))
    assert renderer.set_parameter('ParamAngleX', 100) == 30
    renderer.draw()
    assert sdk.events == ['load', 'update', 'draw']
    assert renderer.report()['parameter_write_verified']
    assert not renderer.report()['runtime_verified']
    renderer.close()
    assert sdk.destroyed and not renderer.report()['native_loaded']


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_nonfinite_parameters_rejected(package, sdk, value):
    renderer = native.CubismRenderer()
    renderer.load_model(str(package))
    with pytest.raises(ValueError): renderer.set_parameter('ParamAngleX', value)


def test_unknown_parameter_rejected(package, sdk):
    renderer = native.CubismRenderer()
    renderer.load_model(str(package))
    with pytest.raises(KeyError): renderer.set_parameter('invented', 1)


def test_core_rejection_cleans_up(package, sdk):
    sdk.HasMocConsistencyFromFile = lambda _: False
    renderer = native.CubismRenderer()
    with pytest.raises(ValueError, match='consistency'): renderer.load_model(str(package))
    assert sdk.destroyed and renderer.report()['deployment_status'] == 'blocked'


def test_failed_reload_clears_previous_success(package, sdk):
    renderer = native.CubismRenderer()
    renderer.load_model(str(package))
    renderer.draw()
    with pytest.raises(FileNotFoundError): renderer.load_model(str(package.parent / 'missing.model3.json'))
    assert renderer.frames_drawn == 0 and not renderer.report()['native_loaded']


@pytest.mark.parametrize('reference', ['../outside.png', '..\\outside.png', 'C:\\outside.png', '/outside.png'])
def test_unsafe_texture_reference(package, reference):
    data = json.loads(package.read_text())
    data['FileReferences']['Textures'] = [reference]
    package.write_text(json.dumps(data))
    with pytest.raises((ValueError, OSError)): native.validate_package(str(package))


def test_absent_optional_reference_is_allowed_but_paths_still_rejected(package):
    """可选引用留空 = 「没有这个文件」，但放宽只到这一步。

    导出器在一条可用物理链都没有时就不写 Physics 引用（空串），校验器必须放行；
    而任何越界/绝对路径仍然要拒 —— 这是安全边界，不能为了便利一起放松。
    """
    data = json.loads(package.read_text())
    data['FileReferences']['Physics'] = ''
    package.write_text(json.dumps(data))
    native.validate_package(str(package))

    for reference in ['/evil.physics3.json', 'C:\\evil.physics3.json',
                      '../outside.physics3.json']:
        data = json.loads(package.read_text())
        data['FileReferences']['Physics'] = reference
        package.write_text(json.dumps(data))
        with pytest.raises((ValueError, OSError)):
            native.validate_package(str(package))


def test_optional_motion_asset_is_checked(package):
    data = json.loads(package.read_text())
    data['FileReferences']['Motions'] = {'Idle': [{'File': 'missing.motion3.json'}]}
    package.write_text(json.dumps(data))
    with pytest.raises(FileNotFoundError): native.validate_package(str(package))


def test_bad_header_rejected_before_sdk_import(package):
    (package.parent / 'hero.moc3').write_bytes(b'fake')
    with pytest.raises(ValueError, match='header'): native.validate_package(str(package))


def test_draw_failure_revokes_state(package, sdk):
    renderer = native.CubismRenderer()
    renderer.load_model(str(package))
    def fail(): raise RuntimeError('context lost')
    sdk.Draw = fail
    with pytest.raises(RuntimeError): renderer.draw()
    assert not renderer.report()['native_loaded']
    assert renderer.report()['deployment_status'] == 'blocked'
