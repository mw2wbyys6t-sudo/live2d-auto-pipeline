"""Regression tests for artifact truthfulness and PSD compositing."""
import json
from pathlib import Path

from PIL import Image
from psd_tools import PSDImage
from core.psd.creator import PSDCreator
from live2d_builder.exporter.model3_exporter import Model3Exporter
from live2d_builder.validator.model_validator import ModelValidator


def test_psd_layer_order_and_count(tmp_path):
    layers = tmp_path / 'layers'
    layers.mkdir()
    Image.new('RGBA', (16, 16), 'red').save(layers / 'layer_00.png')
    Image.new('RGBA', (16, 16), 'blue').save(layers / 'layer_01.png')
    result = PSDCreator().create_psd(str(layers), str(tmp_path / 'nested' / 'hero.psd'))
    assert result['success'] and not result['fallback']
    psd = PSDImage.open(result['psd_path'])
    assert len(psd) == 2
    assert [layer.name for layer in psd] == ['layer_00', 'layer_01']
    assert psd.composite().convert('RGB').getpixel((8, 8)) == (0, 0, 255)


def test_missing_moc_is_not_a_valid_deployment(tmp_path):
    Model3Exporter(max_atlas_size=64).export(
        {'layers': {'face': Image.new('RGBA', (16, 16), 'red')}}, str(tmp_path))
    result = ModelValidator().validate_all(str(tmp_path))
    assert not result['valid']
    assert result['deployment_status'] == 'blocked'
    assert not result['runtime_verified']
    assert any('moc3 unavailable' in error for error in result['errors'])


def test_invalid_moc_header_is_rejected(tmp_path):
    result = Model3Exporter(max_atlas_size=64).export(
        {'layers': {'face': Image.new('RGBA', (16, 16), 'red')}}, str(tmp_path))
    (tmp_path / result['moc3_ref']).write_bytes(b'not a model' * 10)
    assert not ModelValidator().validate_all(str(tmp_path))['valid']


def test_preview_layers_reach_model3(tmp_path):
    """整幅画布图层清单必须写进 model3.json。

    浏览器播放器优先按 PreviewLayers 叠图层；清单缺失时退回叠「图集切片」，
    每张图集都是角色的一个局部切片却被铺满整个画布，画面变成错位的重影。
    """
    result = Model3Exporter(max_atlas_size=64).export(
        {'layers': {'face': Image.new('RGBA', (16, 16), 'red'),
                    'hair_front': Image.new('RGBA', (16, 16), 'blue')}},
        str(tmp_path))
    model3 = json.loads(Path(result['model3_json']).read_text(encoding='utf-8'))

    preview = model3.get('PreviewLayers')
    assert preview, 'model3.json 缺少 PreviewLayers，网页预览会退化成重影'
    assert [entry['Name'] for entry in preview] == ['face', 'hair_front']
    assert [entry['Z'] for entry in preview] == [0, 1]
    for entry in preview:
        assert (tmp_path / entry['Texture']).is_file()
        assert entry['Width'] == 16 and entry['Height'] == 16
        assert entry['Group'] in {'face', 'hair', 'body', 'mouth', 'other'}
