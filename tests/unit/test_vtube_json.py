"""VTS 配置写出：待机动作必须指向真实文件，否则导入后模型是死的。"""
import json
import re

from live2d_builder.exporter.vtube_json import build_vtube_json, write_vtube_json


def test_vtube_json_points_at_the_idle_motion(tmp_path):
    path = write_vtube_json(str(tmp_path), "hero",
                            idle_animation="motions/hero_idle_00.motion3.json")
    doc = json.loads(open(path, encoding="utf-8").read())
    assert doc["FileReferences"]["Model"] == "hero.model3.json"
    assert doc["FileReferences"]["IdleAnimation"] == "motions/hero_idle_00.motion3.json"
    assert doc["Name"] == "hero"
    assert doc["Version"] == 1


def test_vtube_json_uses_english_long_date_like_vts():
    """VTS 按英文长日期写；中文 locale 下 strftime 会写成中文，必须手工拼。"""
    doc = build_vtube_json("hero", "hero.model3.json")
    stamp = doc["ModelSaveMetadata"]["LastSavedDateUTC"]
    assert re.fullmatch(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
        r"\d{2} (January|February|March|April|May|June|July|August|September|"
        r"October|November|December) \d{4}, \d{2}:\d{2}:\d{2}", stamp), stamp


def test_vtube_json_disables_workshop_sharing_by_default():
    """产物带用户自己的立绘，默认不允许一键上传到创意工坊。"""
    doc = build_vtube_json("hero", "hero.model3.json")
    assert doc["GeneralSettings"]["WorkshopSharingForbidden"] is True
    assert doc["PhysicsSettings"]["Use"] is True
