"""core/psd/part_naming.py 的单元测试（借鉴 psd2live 图层名识别思路的自研实现）。"""

from core.psd.part_naming import recognize_part


def test_english_eye_layers():
    assert recognize_part("eye_l_white")["part"] == "eye_white"
    assert recognize_part("eye_l_white")["side"] == "left"
    assert recognize_part("eye_r_iris")["part"] == "eye_iris"
    assert recognize_part("eyeR_pupil")["part"] == "eye_pupil"


def test_longest_keyword_wins():
    # "eye" 不能抢走更具体的 "eye_white"
    r = recognize_part("eye_l_white")
    assert r["part"] == "eye_white"
    assert r["matched_by"] in ("eye_white", "白眼", "眼白", "白目", "sclera", "eyewhite")


def test_multilingual_names():
    assert recognize_part("前发_01")["part"] == "hair_front"
    assert recognize_part("後髪")["part"] == "hair_back"
    assert recognize_part("左目 白目")["part"] == "eye_white"
    assert recognize_part("左目 白目")["side"] == "left"
    assert recognize_part("リボン")["part"] == "accessory"


def test_mouth_vowels():
    for vowel in ("a", "i", "u", "e", "o"):
        assert recognize_part(f"mouth_{vowel}")["mouth_vowel"] == vowel
    assert recognize_part("嘴_a")["mouth_vowel"] == "a"
    assert recognize_part("mouth_smile")["mouth_vowel"] is None


def test_unrecognized_name():
    r = recognize_part("Layer 7")
    assert r["part"] is None
    assert r["side"] is None
    assert recognize_part("")["part"] is None
