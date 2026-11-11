"""协议层纯函数测试：信封、版本规则、参数解析（对照 5.3 字节码）。"""

import json

import pytest

from drivers.cubism_editor.protocol import (
    LATEST_VERSION,
    CubismProtocolError,
    Parameter,
    build_request,
    new_request_id,
    parse_message,
    resolve_version,
    version_number,
)


def test_build_request_envelope_shape():
    req = build_request("GetParameters", {"ModelUID": "m1"})
    assert set(req) == {"Version", "Timestamp", "RequestId", "Type", "Method", "Data"}
    assert req["Type"] == "Request"
    assert req["Method"] == "GetParameters"
    assert req["Data"] == {"ModelUID": "m1"}
    assert req["Version"] == LATEST_VERSION
    assert isinstance(req["Timestamp"], int)
    assert len(req["RequestId"]) == 32 and "-" not in req["RequestId"]


def test_build_request_defaults_data_to_empty_object():
    assert build_request("GetAPIVersion")["Data"] == {}


def test_request_ids_are_unique():
    assert new_request_id() != new_request_id()


def test_parse_message_roundtrip():
    req = build_request("SetParameterValues", {"ModelUID": "m1", "Parameters": []})
    msg = parse_message(json.dumps(req))
    assert msg.type == "Request"
    assert msg.method == "SetParameterValues"
    assert msg.request_id == req["RequestId"]
    assert msg.data == req["Data"]


def test_parse_event_without_request_id():
    raw = json.dumps({
        "Version": "1.0.1", "Timestamp": 1, "Type": "Event",
        "Method": "NotifyMocFileExported",
        "Data": {"Path": "C:/out/model.moc3", "ModelFilePath": "C:/out/model.model3.json"},
    })
    msg = parse_message(raw)
    assert msg.type == "Event"
    assert msg.request_id is None
    assert msg.data["Path"].endswith(".moc3")


@pytest.mark.parametrize("bad", [
    "[]",
    json.dumps({"Type": "Request", "Method": "X"}),  # 缺 Data
    json.dumps({"Type": "Bogus", "Method": "X", "Data": {}}),
    json.dumps({"Type": "Request", "Method": "", "Data": {}}),
    json.dumps({"Type": "Request", "Method": "X", "Data": []}),
    json.dumps({"Type": "Request", "Method": "X", "Data": {}, "RequestId": 5}),
])
def test_parse_message_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_message(bad)


@pytest.mark.parametrize("text,expected", [
    ("1.0.1", 10001),
    ("0.9.0", 900),
    ("0.9.3", 903),
    ("1.0.0", 10000),
    ("2.5.7", 20507),
    ("1.100.1", 10001),  # 字节码 quirks：minor%100
    ("1.0", None),
    ("a.b.c", None),
    ("-1.0.0", None),
])
def test_version_number_rule(text, expected):
    assert version_number(text) == expected


@pytest.mark.parametrize("requested,plugin,expected", [
    (None, None, "1.0.1"),
    (None, "0.9.3", "0.9.3"),
    ("0.9.2", None, "0.9.2"),
    ("1.0.5", None, "1.0.1"),   # 映射到 ≤ 解析值的最高已知版本
    ("9.9.9", None, "1.0.1"),
    ("0.8.0", None, None),      # 低于最低已知 → UNKNOWN
    ("garbage", None, None),
    ("0.9.1", "1.0.0", "0.9.1"),  # 信封 Version 优先于插件全局
])
def test_resolve_version(requested, plugin, expected):
    assert resolve_version(requested, plugin) == expected


def test_parameter_from_json_full():
    p = Parameter.from_json({
        "Id": "ParamAngleX", "Name": "角度 X", "GroupUID": "G1",
        "Default": 0.0, "Max": 30.0, "Min": -30.0,
        "Repeat": False, "Type": 0,
        "Keyform": [{"Value": -30.0}, {"Value": 0.0}, {"Value": 30.0}],
    })
    assert p.id == "ParamAngleX"
    assert p.minimum == -30.0 and p.maximum == 30.0
    assert p.keyform == (-30.0, 0.0, 30.0)
    assert p.group_uid == "G1"


def test_parameter_from_json_v090_without_keyform():
    p = Parameter.from_json({
        "Id": "ParamX", "Name": "", "GroupUID": "",
        "Default": 1.0, "Max": 2.0, "Min": 0.0, "Repeat": True, "Type": 1,
    })
    assert p.keyform == ()
    assert p.repeat is True
    assert p.type == 1


def test_protocol_error_carries_type_and_method():
    exc = CubismProtocolError("InvalidModel", "GetParameters")
    assert exc.error_type == "InvalidModel"
    assert exc.method == "GetParameters"
    assert "InvalidModel" in str(exc)