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


def test_psd2live_extended_tags():
    assert recognize_part("back hair")["part"] == "hair_back"
    assert recognize_part("headwear")["part"] == "headwear"
    assert recognize_part("face detail")["part"] == "face_detail"
    assert recognize_part("eye close")["part"] == "eye_close"
    assert recognize_part("mouth open")["part"] == "mouth_open"
    assert recognize_part("mouth close")["part"] == "mouth_close"
    assert recognize_part("tooth-t")["part"] == "tooth_top"
    assert recognize_part("tooth-b")["part"] == "tooth_bottom"
    assert recognize_part("tongue")["part"] == "tongue"
    assert recognize_part("neckwear")["part"] == "neckwear"


def test_korean_layer_names():
    assert recognize_part("뒷머리")["part"] == "hair_back"
    assert recognize_part("앞머리")["part"] == "hair_front"
    assert recognize_part("흰자")["part"] == "eye_white"
    assert recognize_part("눈동자")["part"] == "eye_iris"
    assert recognize_part("눈썹")["part"] == "eyebrow"
    assert recognize_part("왼쪽 속눈썹")["side"] == "left"
    assert recognize_part("오른쪽 속눈썹")["side"] == "right"


def test_photoshop_copy_suffix_is_stripped():
    assert recognize_part("前发 copy 2")["part"] == "hair_front"
    assert recognize_part("mouth (1)")["part"] == "mouth"
    assert recognize_part("hair_back 副本")["part"] == "hair_back"
