"""调用方契约：崩溃与超时必须被归类为「验证失败」，绝不抛出。"""
import subprocess

from drivers.live2d_runtime import moc3_verify
from drivers.live2d_runtime.moc3_verify import verify_moc3_consistency


def test_missing_file_is_reported_not_raised(tmp_path):
    result = verify_moc3_consistency(str(tmp_path / "absent.moc3"))
    assert result["ok"] is False
    assert "不存在" in result["blocker"]


def test_clean_exit_zero_is_success(tmp_path, monkeypatch):
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, '{"ok": true}', "")

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target))
    assert result["ok"] is True
    assert result["blocker"] is None


def test_nonzero_exit_is_failure(tmp_path, monkeypatch):
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, '{"ok": false}', "")

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target))
    assert result["ok"] is False
    assert "不一致" in result["blocker"]


def test_crash_exit_is_failure(tmp_path, monkeypatch):
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 3221225477, "", "")

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target))
    assert result["ok"] is False
    assert result["crashed"] is True
    assert "crashed" in result["blocker"]


def test_timeout_is_failure(tmp_path, monkeypatch):
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target), timeout=1)
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert "timed out" in result["blocker"]


def test_subprocess_output_is_decoded_as_utf8(tmp_path, monkeypatch):
    """中文 Windows 的 locale 是 cp936：不显式指定编码就会随机炸。"""
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, '{"ok": true}', "")

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    assert verify_moc3_consistency(str(target))["ok"] is True
    assert seen.get("encoding") == "utf-8", seen
    assert seen.get("errors") == "replace", seen
    assert not seen.get("text"), "text=True 会按 locale 解码"


def test_undecodable_child_output_is_failure_not_crash(tmp_path, monkeypatch):
    """子进程输出解码失败时 stdout 可能是 None：必须归类为验证失败。"""
    target = tmp_path / "a.moc3"
    target.write_bytes(b"MOC3" + bytes(2044))

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, None, None)

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = verify_moc3_consistency(str(target))
    assert result["ok"] is False
    assert result["blocker"]


def test_runtime_probe_survives_none_output(tmp_path, monkeypatch):
    manifest = tmp_path / "m.model3.json"
    manifest.write_text("{}", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, -1073741819, None, None)

    monkeypatch.setattr(moc3_verify.subprocess, "run", fake_run)
    result = moc3_verify.render_probe(str(manifest))
    assert result["ok"] is False
    assert result["crashed"] is True
