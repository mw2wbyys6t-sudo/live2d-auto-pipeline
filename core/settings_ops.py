"""设置页 API Key 的保险库读写（唯一落盘通道）。

Go 后端不接触 Key 明文存储：保存/删除都通过本模块调用
``core/secure_storage.py`` 的加密保险库。Key 只在 Python 进程内存与
加密文件里出现，不写 settings.json、不进日志、不返回给前端。
"""

from __future__ import annotations

from typing import Dict

#: 设置页 provider 名 -> 配置里的环境变量名（与 core/config.py 的映射一致）。
KEY_ENV: Dict[str, str] = {
    "ark": "ARK_API_KEY",
    # Seedream provider 实际读的就是 ARK_API_KEY（见 seedream.py），
    # 这里把别名指向同一把 Key，避免出现"存了 seedream 却仍缺 Key"。
    "seedream": "ARK_API_KEY",
    "sensenova": "SENSENOVA_API_KEY",
    "openai": "OPENAI_API_KEY",
}

#: 设置页 provider 名 -> 保险库里的条目名（alias 必须落到同一项）。
VAULT_PROVIDER: Dict[str, str] = {
    "ark": "ark",
    "seedream": "ark",
    "sensenova": "sensenova",
    "openai": "openai",
}


def _config():
    from core.config import config

    return config


def _vault():
    cfg = _config()
    vault = getattr(cfg, "_encrypted_config", None)
    if vault is None:
        raise RuntimeError("加密保险库不可用（缺少 cryptography 或主密钥无法创建）")
    return cfg, vault


def save_key(provider: str, value: str) -> bool:
    """保存一个 API Key；写入失败时抛异常（调用方只回传 provider 名）。"""
    if provider not in KEY_ENV:
        raise ValueError(f"未知的 Key 名称: {provider}")
    cfg, vault = _vault()
    if not vault.store_api_key(VAULT_PROVIDER[provider], value):
        raise RuntimeError("写入加密保险库失败")
    # 同进程内立即生效（保险库缓存），下次生成是新进程，会重新读盘。
    cfg._secrets[KEY_ENV[provider]] = value
    return True


def delete_key(provider: str) -> bool:
    """删除一个 API Key；不存在时视为成功（幂等）。"""
    if provider not in KEY_ENV:
        raise ValueError(f"未知的 Key 名称: {provider}")
    cfg, vault = _vault()
    if not vault.delete_api_key(VAULT_PROVIDER[provider]):
        raise RuntimeError("删除保险库条目失败")
    # delete_api_key 已清缓存；这里再清一次 cfg 的私有字典，避免同进程读到旧值。
    cfg._secrets.pop(KEY_ENV[provider], None)
    return True


def key_status() -> Dict[str, bool]:
    """返回每个 provider 是否已配置 Key（只有布尔，不含任何 Key 内容）。"""
    cfg = _config()
    return {name: bool(cfg._get_secret(env)) for name, env in KEY_ENV.items()}
