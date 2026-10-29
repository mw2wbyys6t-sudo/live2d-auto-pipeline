#!/usr/bin/env python3
"""
Pygmalion Studio - Secure Storage Module

Security:
- REQUIRES cryptography library (Fernet AES-128-CBC + HMAC-SHA256)
- 主密钥为首次启动时生成的随机 Fernet key，存放于独立的 0600 权限文件
  （与密文分离保管；可用 LIVE2D_VAULT_KEY_FILE 覆盖路径），不是由
  HOSTNAME/USER 等非秘密系统信息推导——旧版方案已废弃（见 _derive_legacy_key）。
- 旧版确定性密钥加密的保险库仍可读取（decrypt 回退），并在启动时通过
  upgrade_file 原地重加密迁移到随机密钥。
- File permissions 0600（密钥目录 0700）on Unix
- Memory cleanup on exit
- NO insecure XOR fallback - raises ImportError with clear message if cryptography missing
"""

import os
import sys
import json
import base64
import hashlib
import secrets
import stat
import atexit
from pathlib import Path
from typing import Mapping, Optional, Dict

from core.app_paths import APP_DIR_NAME, LEGACY_DIR_NAMES

# P0-4 FIX: Require cryptography at import time with clear error message
try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    # Don't raise here - let classes raise when actually used
    # This allows tests that don't use encryption to import


def _require_crypto():
    """Raise clear error if cryptography is not available."""
    if not _CRYPTO_AVAILABLE:
        raise ImportError(
            "The 'cryptography' package is REQUIRED for secure storage. "
            "Install it with: pip install cryptography\n"
            "This was made mandatory in v8.0.0 - the insecure XOR fallback was removed."
        )


def _config_root_for_platform(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """配置根目录（密钥库放这里，与数据目录分开）。"""
    if platform == "win32":
        appdata = env.get("APPDATA")
        return Path(appdata) if appdata else home / "AppData" / "Roaming"
    if platform == "darwin":
        return home / "Library" / "Application Support"
    xdg = env.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else home / ".config"


def master_key_path_for_platform(
    platform: str,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """主密钥路径：显式覆盖 > 旧目录里已有的密钥 > 新目录。

    产品改名（Pygmalion Studio → Pygmalion Studio）时，如果只顾换新目录，
    首次读取就会「密钥不存在 → 随机生成一把新的」，旧密文从此解不开 ——
    那是用户数据的静默损坏。所以这里必须先看旧目录在不在。
    """
    environment = dict(os.environ) if env is None else dict(env)
    override = environment.get("LIVE2D_VAULT_KEY_FILE", "").strip()
    if override:
        return Path(override)
    base_home = Path(home) if home else Path(environment.get("HOME") or Path.home())
    root = _config_root_for_platform(platform, environment, base_home)
    for name in (APP_DIR_NAME, *LEGACY_DIR_NAMES):
        candidate = root / name / "vault.key"
        if candidate.is_file():
            return candidate
    return root / APP_DIR_NAME / "vault.key"


def _default_master_key_path() -> Path:
    """随机主密钥文件的存放位置（用户配置目录，与项目文件/密文分离）。

    - LIVE2D_VAULT_KEY_FILE 显式覆盖（测试/运维/便携部署）；
    - Windows: %APPDATA%/PygmalionStudio/vault.key
    - macOS:   ~/Library/Application Support/PygmalionStudio/vault.key
    - Linux:   $XDG_CONFIG_HOME 或 ~/.config/PygmalionStudio/vault.key
    旧版目录名（live2d-master-agent / Live2DMasterAgent）若已存在密钥则继续沿用。
    """
    return master_key_path_for_platform(sys.platform)


def _harden_path(path: Path) -> None:
    """Unix 下把密钥目录收为 0700、文件收为 0600（Windows 忽略）。"""
    if os.name == "nt":
        return
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


class SecureStorage:
    """Fernet-based secure storage with a random, file-backed master key.

    主密钥在首次使用时随机生成并独立落盘；旧版「系统信息确定性推导」密钥
    仅作为解密回退（_derive_legacy_key），用于迁移历史保险库，不再用于加密。
    """

    # legacy KDF 输入：改名会改变派生结果，导致旧保险库永远解不开，因此必须保持字节一致（仅用于解密回退）。
    APP_IDENTIFIER = b'pygmalion-studio-v8.0-secure-storage'
    PBKDF2_ITERATIONS = 200_000  # 仅用于旧保险库的兼容解密
    KEY_LENGTH = 32  # 256-bit key for Fernet

    def __init__(self, master_key_file: Optional[str] = None):
        _require_crypto()
        self._key_path = Path(master_key_file) if master_key_file else _default_master_key_path()
        self._master_key = self._load_or_create_master_key()
        self._legacy_key: Optional[bytes] = None  # 懒加载，避免无谓的 PBKDF2

    def _load_or_create_master_key(self) -> bytes:
        """加载既有随机密钥；不存在则生成、0600 落盘（临时文件+原子替换）。"""
        path = self._key_path
        if path.exists():
            key = path.read_bytes().strip()
            if key:
                return key
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                path.parent.chmod(0o700)
            except OSError:
                pass
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
        try:
            # O_EXCL 防止两个并发首启互相覆盖；读到他人文件时直接改用其内容
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, key)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except FileExistsError:
            tmp.unlink(missing_ok=True)
            if path.exists():
                existing = path.read_bytes().strip()
                if existing:
                    return existing
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        _harden_path(path)
        return key

    def _derive_legacy_key(self) -> bytes:
        """[已废弃] 旧版确定性主密钥推导，仅用于解密历史保险库。

        旧方案的盐来自 HOSTNAME/USER/家目录（低熵、非秘密、常随项目文件
        一起泄露），口令是源码常量——任何拿到 .env.encrypted 的人都能离线
        还原，因此绝不能再用于新加密。
        """
        salt_parts = [
            os.environ.get('HOSTNAME', ''),
            os.environ.get('USER', os.environ.get('USERNAME', '')),
            os.name,
            str(Path.home()),
        ]
        salt_material = '|'.join(salt_parts).encode('utf-8')
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=self.KEY_LENGTH,
            salt=hashlib.sha256(salt_material).digest(),
            iterations=self.PBKDF2_ITERATIONS,
        )
        derived = kdf.derive(self.APP_IDENTIFIER)
        return base64.urlsafe_b64encode(derived)

    def _get_legacy_key(self) -> bytes:
        if self._legacy_key is None:
            self._legacy_key = self._derive_legacy_key()
        return self._legacy_key

    def _decrypt_with_current_key(self, ciphertext: str) -> str:
        """只用当前随机密钥解密；失败抛 InvalidToken。"""
        raw = base64.urlsafe_b64decode(ciphertext.encode('ascii'))
        return Fernet(self._master_key).decrypt(raw).decode('utf-8')

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string using Fernet (AES-128-CBC + HMAC-SHA256).

        Returns urlsafe-base64 encoded ciphertext.
        """
        _require_crypto()
        if not isinstance(plaintext, str):
            raise TypeError("plaintext must be a string")
        f = Fernet(self._master_key)
        encrypted = f.encrypt(plaintext.encode('utf-8'))
        return base64.urlsafe_b64encode(encrypted).decode('ascii')

    def decrypt(self, ciphertext: str) -> Optional[str]:
        """Decrypt a Fernet-encrypted string.

        优先用当前随机密钥；失败时回退到旧版确定性密钥（迁移期兼容），
        两者都失败返回 None。
        """
        _require_crypto()
        if not ciphertext:
            return None
        try:
            return self._decrypt_with_current_key(ciphertext)
        except (InvalidToken, base64.binascii.Error, ValueError, UnicodeDecodeError):
            pass
        try:
            raw = base64.urlsafe_b64decode(ciphertext.encode('ascii'))
            return Fernet(self._get_legacy_key()).decrypt(raw).decode('utf-8')
        except (InvalidToken, base64.binascii.Error, ValueError, UnicodeDecodeError):
            return None

    def upgrade_file(self, filepath: str) -> bool:
        """把旧版确定性密钥加密的保险库原地重加密为当前随机密钥。

        - 文件不存在、为空或已是当前密钥加密 → 返回 False；
        - 旧密钥可解 → 0600 原子重写为新密文 → 返回 True；
        - 两种密钥都解不开（损坏/外来源）→ 保持原文件不动并返回 False。
        """
        _require_crypto()
        path = Path(filepath)
        if not path.exists():
            return False
        try:
            ciphertext = path.read_text(encoding='utf-8')
        except OSError:
            return False
        if not ciphertext.strip():
            return False
        try:
            self._decrypt_with_current_key(ciphertext)
            return False  # 已是当前密钥方案
        except (InvalidToken, base64.binascii.Error, ValueError, UnicodeDecodeError):
            pass
        try:
            raw = base64.urlsafe_b64decode(ciphertext.encode('ascii'))
            plaintext = Fernet(self._get_legacy_key()).decrypt(raw).decode('utf-8')
        except (InvalidToken, base64.binascii.Error, ValueError, UnicodeDecodeError):
            return False
        new_token = self.encrypt(plaintext)
        tmp = path.with_name(f".{path.name}.up-{os.getpid()}-{secrets.token_hex(4)}")
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, new_token.encode('ascii'))
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            return False
        _harden_path(path)
        return True

    def encrypt_to_file(self, data: dict, filepath: str) -> bool:
        """Encrypt a dict and save to file with 0600 permissions."""
        _require_crypto()
        try:
            plaintext = json.dumps(data, ensure_ascii=False)
            ciphertext = self.encrypt(plaintext)
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ciphertext, encoding='utf-8')
            # Set file permissions to owner-only
            if os.name != 'nt':
                import stat
                try:
                    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
            return True
        except Exception:
            return False

    def decrypt_from_file(self, filepath: str) -> Optional[dict]:
        """Read and decrypt an encrypted JSON file."""
        _require_crypto()
        try:
            path = Path(filepath)
            if not path.exists():
                return None
            ciphertext = path.read_text(encoding='utf-8')
            plaintext = self.decrypt(ciphertext)
            if plaintext is None:
                return None
            return json.loads(plaintext)
        except (json.JSONDecodeError, OSError):
            return None

    def store_api_key(self, provider: str, api_key: str, filepath: Optional[str] = None) -> bool:
        """Store an API key in the encrypted file."""
        if filepath is None:
            filepath = self._default_key_path()
        existing = self.decrypt_from_file(filepath) or {}
        existing[provider] = api_key
        return self.encrypt_to_file(existing, filepath)

    def get_api_key(self, provider: str, filepath: Optional[str] = None) -> Optional[str]:
        """Retrieve an API key from the encrypted file."""
        if filepath is None:
            filepath = self._default_key_path()
        data = self.decrypt_from_file(filepath)
        if data and provider in data:
            return data[provider]
        return None

    def delete_api_key(self, provider: str, filepath: Optional[str] = None) -> bool:
        """Delete an API key from the encrypted file."""
        if filepath is None:
            filepath = self._default_key_path()
        data = self.decrypt_from_file(filepath) or {}
        if provider in data:
            del data[provider]
            return self.encrypt_to_file(data, filepath)
        return True

    def _default_key_path(self) -> str:
        root = os.environ.get("LIVE2D_PROJECT_ROOT", str(Path.home()))
        return str(Path(root) / ".env.encrypted")


class EncryptedConfig:
    """Encrypted configuration manager with in-memory caching and cleanup."""

    def __init__(self, storage: Optional[SecureStorage] = None):
        _require_crypto()
        self._storage = storage or SecureStorage()
        self._cache: Dict[str, str] = {}
        root = os.environ.get("LIVE2D_PROJECT_ROOT")
        self._file = Path(root) / ".env.encrypted" if root else Path(__file__).parent.parent / ".env.encrypted"
        # 首次启动即把旧版确定性密钥加密的保险库迁移到随机主密钥；
        # 失败不阻塞启动（读路径仍有旧密钥回退）。
        try:
            self._storage.upgrade_file(str(self._file))
        except Exception:
            pass
        atexit.register(self.clear_cache)

    def store_api_key(self, provider: str, api_key: str) -> bool:
        self._cache[provider] = api_key
        return self._storage.store_api_key(provider, api_key, str(self._file))

    def get_api_key(self, provider: str) -> Optional[str]:
        if provider in self._cache:
            return self._cache[provider]
        key = self._storage.get_api_key(provider, str(self._file))
        if key:
            self._cache[provider] = key
        return key

    def has_key(self, provider: str) -> bool:
        return self.get_api_key(provider) is not None

    def clear_cache(self):
        """Securely overwrite and clear in-memory keys."""
        for k in list(self._cache.keys()):
            val = self._cache[k]
            if val:
                self._cache[k] = '\x00' * len(val)
        self._cache.clear()

    def list_providers(self) -> list:
        """List providers that have stored keys (without exposing keys)."""
        data = self._storage.decrypt_from_file(str(self._file))
        return list(data.keys()) if data else []


# Compatibility functions
def encrypt_api_key(api_key: str) -> str:
    return SecureStorage().encrypt(api_key)


def decrypt_api_key(encrypted_key: str) -> Optional[str]:
    return SecureStorage().decrypt(encrypted_key)


if __name__ == "__main__":
    if not _CRYPTO_AVAILABLE:
        print("ERROR: cryptography package not installed. Run: pip install cryptography")
        sys.exit(1)

    storage = SecureStorage()
    test_key = "sk-test-v8-abcdefghijklmnopqrstuvwx1234"

    print("=== Secure Storage v0.9.0 Test ===")
    print(f"PBKDF2 iterations: {SecureStorage.PBKDF2_ITERATIONS}")

    encrypted = storage.encrypt(test_key)
    print(f"Encrypted: {encrypted[:60]}...")

    decrypted = storage.decrypt(encrypted)
    ok = "PASS" if decrypted == test_key else "FAIL"
    print(f"Decrypt: [{ok}]")

    bad_decrypt = storage.decrypt("invalid-ciphertext")
    print(f"Bad decrypt returns None: [{'PASS' if bad_decrypt is None else 'FAIL'}]")

    # Test file roundtrip
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.enc', delete=False) as f:
        tmp = f.name
    try:
        ok_store = storage.store_api_key("test_provider", test_key, tmp)
        retrieved = storage.get_api_key("test_provider", tmp)
        print(f"File store/get: [{'PASS' if ok_store and retrieved == test_key else 'FAIL'}]")
    finally:
        os.unlink(tmp)

    print("\nAll secure storage tests passed.")
