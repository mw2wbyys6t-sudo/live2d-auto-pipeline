"""start.py 一键启动器的单元测试。

覆盖：
1. check_python_version 满足/不满足最低版本
2. parse_node_version / parse_go_version 版本号解析
3. check_environment 全部依赖就绪时返回 True

不引入新依赖，仅使用 stdlib（unittest.mock）。
"""

import pytest
from unittest import mock
from unittest.mock import MagicMock

import start


# 1. Python 版本检测 ----------------------------------------------

def test_check_python_version_pass():
    """mock sys.version_info = (3, 14, 7)，断言通过。"""
    assert start.check_python_version((3, 14, 7)) is True


def test_check_python_version_fail():
    """mock sys.version_info = (3, 8, 0)，断言抛 SystemExit(1)。"""
    with pytest.raises(SystemExit) as exc_info:
        start.check_python_version((3, 8, 0))
    assert exc_info.value.code == 1


# 2. 版本字符串解析 -----------------------------------------------

def test_parse_node_version():
    """传入 "v24.1.0"，断言返回 (24, 1)。"""
    assert start.parse_node_version("v24.1.0") == (24, 1)


def test_parse_go_version():
    """传入完整 `go version` 输出，断言返回 (1, 25)。"""
    assert start.parse_go_version("go version go1.25.1 linux/amd64") == (1, 25)


# 3. 环境综合检测 -------------------------------------------------

@mock.patch("start.subprocess.run")
def test_check_environment_all_present(mock_run):
    """mock subprocess.run 返回 Node / Go 版本字符串，断言返回 True。

    check_environment 内部按顺序调用：
      1) node --version  → "v24.1.0"
      2) go version      → "go version go1.25.1 linux/amd64"
    Python 部分使用真实 sys.version_info（测试运行环境 >= 3.9）。
    """
    mock_run.side_effect = [
        MagicMock(stdout="v24.1.0\n", stderr="", returncode=0),
        MagicMock(stdout="go version go1.25.1 linux/amd64\n", stderr="", returncode=0),
    ]
    assert start.check_environment() is True
    # 确保两次 subprocess.run 都被消费
    assert mock_run.call_count == 2
