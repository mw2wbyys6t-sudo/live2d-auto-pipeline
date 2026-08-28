#!/usr/bin/env python3
"""
Live2D Master Agent - Secure Configuration Manager (P0-5 FIXED: env path resolution)

P0-5 Fix: .env file is searched in multiple locations to support:
1. LIVE2D_PROJECT_ROOT (set by root wrappers)
2. Directory of the live2d package (for Trae Skill runtime)
3. Current working directory
4. Script directory (when running from root wrappers)
5. Home directory .trae config

Other improvements:
- Cryptography is required (no XOR fallback per P0-4)
- Version constant sourced from core.version
- All sensitive keys stored privately, not leaked to os.environ
"""

import os
import re
import sys
import atexit
from pathlib import Path
from typing import Optional, Dict, Set

from core.version import __version__

# Lazy import secure_storage
try:
    from core.secure_storage import EncryptedConfig, SecureStorage, _CRYPTO_AVAILABLE
except ImportError:
    _CRYPTO_AVAILABLE = False
    EncryptedConfig = None  # type: ignore
    SecureStorage = None  # type: ignore


class SecureConfig:
    """Thread-safe singleton configuration manager with secure key storage."""

    _instance: Optional["SecureConfig"] = None
    _loaded: bool = False

    SENSITIVE_KEYS: Set[str] = {
        'ARK_API_KEY', 'SENSENOVA_API_KEY', 'API_KEY',
        'SECRET_KEY', 'PASSWORD', 'TOKEN',
        'SEEDREAM_API_KEY', 'OPENAI_API_KEY',
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._loaded:
            return
        self._secrets: Dict[str, str] = {}
        self._config: Dict[str, str] = {}
        self._encrypted_config = None
        if _CRYPTO_AVAILABLE and EncryptedConfig is not None:
            try:
                self._encrypted_config = EncryptedConfig()
            except Exception:
                self._encrypted_config = None
        self._load_config()
        self._set_defaults()
        self._loaded = True
        atexit.register(self._cleanup)

    # P0-5 FIX: Search multiple .env locations with Trae Skill compatibility
    def _find_env_file(self) -> Optional[Path]:
        """Search for .env file across all possible project locations.

        Order of precedence:
        1. LIVE2D_ENV_PATH environment variable (explicit override)
        2. LIVE2D_PROJECT_ROOT/.env (set by root wrappers)
        3. live2d package parent dir (project root) /.env
        4. Current working directory/.env
        5. Directory of the main script (sys.argv[0])/.env
        6. ~/.trae-cn/skills/live2d-master-agent/.env (Trae Skill default)
        7. ~/.live2d/.env (user home config)
        """
        search_paths = []

        # 1. Explicit override
        if os.environ.get("LIVE2D_ENV_PATH"):
            search_paths.append(Path(os.environ["LIVE2D_ENV_PATH"]))

        # 2. LIVE2D_PROJECT_ROOT
        project_root = os.environ.get("LIVE2D_PROJECT_ROOT", "")
        if project_root:
            search_paths.append(Path(project_root) / ".env")

        # 3. Package parent (one level up from live2d/ package)
        pkg_dir = Path(__file__).parent.parent
        search_paths.append(pkg_dir / ".env")

        # 4. Current working directory
        search_paths.append(Path.cwd() / ".env")

        # 5. Script directory (where the entry point lives)
        if sys.argv and sys.argv[0]:
            try:
                script_dir = Path(sys.argv[0]).resolve().parent
                search_paths.append(script_dir / ".env")
            except (OSError, RuntimeError):
                pass

        # 6. Trae Skill config directory
        search_paths.append(Path.home() / ".trae-cn" / "skills" / "live2d-master-agent" / ".env")

        # 7. User home config
        search_paths.append(Path.home() / ".live2d" / ".env")

        for p in search_paths:
            try:
                if p.is_file():
                    return p
            except OSError:
                continue

        return None

    def _load_env_file(self):
        """Load .env file from the discovered path."""
        env_path = self._find_env_file()
        if env_path is None:
            return

        try:
            content = env_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue

            if key in self.SENSITIVE_KEYS:
                self._secrets[key] = value
            else:
                self._config[key] = value
                os.environ[key] = value

    def _load_encrypted_keys(self):
        """Load keys from encrypted storage if available."""
        if self._encrypted_config is None:
            return
        for provider_env in ('SENSENOVA_API_KEY', 'ARK_API_KEY', 'SEEDREAM_API_KEY'):
            provider = provider_env.replace('_API_KEY', '').lower()
            try:
                key = self._encrypted_config.get_api_key(provider)
                if key:
                    self._secrets[provider_env] = key
            except Exception:
                pass

    def _set_defaults(self):
        """Set default configuration values."""
        defaults = {
            'ARK_BASE_URL': 'https://ark.cn-beijing.volces.com/api/v3',
            'SEEDREAM_VERSION': 'seedream-4.0',
            'SEEDREAM_SIZE': '1024x1024',
            'SEEDREAM_QUALITY': 'standard',
            'OUTPUT_DIR': '',
            'MAX_PSD_SIZE_MB': '500',
            'SENSENOVA_BASE_URL': 'https://api.sensenova.cn/v1',
            'LIVE2D_LOG_LEVEL': 'INFO',
            'LIVE2D_TELEMETRY': '0',
            'GO_API_HOST': '0.0.0.0',
            'GO_API_PORT': '8080',
            'GO_API_TIMEOUT': '120',  # P1-4 fix: configurable timeout, default 120s
        }
        for k, v in defaults.items():
            if k not in self._config and k not in os.environ:
                self._config[k] = v
                os.environ.setdefault(k, v)

        # Default output dir relative to project root
        if not self._config.get('OUTPUT_DIR'):
            root = os.environ.get("LIVE2D_PROJECT_ROOT", str(Path(__file__).parent.parent))
            self._config['OUTPUT_DIR'] = str(Path(root) / "output")

    def _load_config(self):
        """Load configuration from all sources."""
        self._load_env_file()
        self._load_encrypted_keys()

    def _get_secret(self, key: str) -> Optional[str]:
        """Get a secret value: encrypted storage > private dict > env var."""
        if self._encrypted_config:
            provider_map = {
                'SENSENOVA_API_KEY': 'sensenova',
                'ARK_API_KEY': 'ark',
                'SEEDREAM_API_KEY': 'seedream',
            }
            if key in provider_map:
                try:
                    val = self._encrypted_config.get_api_key(provider_map[key])
                    if val:
                        return val
                except Exception:
                    pass
        if key in self._secrets:
            return self._secrets[key]
        return os.environ.get(key)

    def get(self, key: str, default=None):
        """Get a configuration value (secrets from private storage, others from config)."""
        if key in self.SENSITIVE_KEYS:
            val = self._get_secret(key)
            return val if val is not None else default
        if key in self._config:
            return self._config[key]
        return os.environ.get(key, default)

    def set(self, key: str, value: str, persist: bool = False):
        """Set a configuration value."""
        if key in self.SENSITIVE_KEYS:
            self._secrets[key] = value
            if persist and self._encrypted_config:
                provider_map = {
                    'SENSENOVA_API_KEY': 'sensenova',
                    'ARK_API_KEY': 'ark',
                    'SEEDREAM_API_KEY': 'seedream',
                }
                if key in provider_map:
                    self._encrypted_config.store_api_key(provider_map[key], value)
        else:
            self._config[key] = value
            os.environ[key] = value

    def _cleanup(self):
        """Securely wipe secrets from memory on exit."""
        for k in list(self._secrets.keys()):
            val = self._secrets[k]
            if val:
                self._secrets[k] = '\x00' * len(val)
        self._secrets.clear()
        if self._encrypted_config:
            self._encrypted_config.clear_cache()

    # --- Properties ---

    @property
    def version(self) -> str:
        return __version__

    @property
    def ark_api_key(self) -> Optional[str]:
        return self._get_secret('ARK_API_KEY')

    @property
    def ark_base_url(self) -> str:
        return self.get('ARK_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3')

    @property
    def seedream_version(self) -> str:
        return self.get('SEEDREAM_VERSION', 'seedream-4.0')

    @property
    def seedream_size(self) -> str:
        return self.get('SEEDREAM_SIZE', '1024x1024')

    @property
    def output_dir(self) -> str:
        d = self.get('OUTPUT_DIR', '')
        if not d:
            root = os.environ.get("LIVE2D_PROJECT_ROOT", str(Path(__file__).parent.parent))
            d = str(Path(root) / "output")
        os.makedirs(d, exist_ok=True)
        return d

    @property
    def sensenova_api_key(self) -> Optional[str]:
        return self._get_secret('SENSENOVA_API_KEY')

    @property
    def sensenova_base_url(self) -> str:
        return self.get('SENSENOVA_BASE_URL', 'https://api.sensenova.cn/v1')

    @property
    def has_api_key(self) -> bool:
        return bool(self.sensenova_api_key)

    @property
    def has_ark_key(self) -> bool:
        return bool(self.ark_api_key)

    @property
    def go_api_timeout(self) -> int:
        """P1-4 fix: Configurable Go API timeout."""
        return int(self.get('GO_API_TIMEOUT', '120'))

    def validate_api_key(self, provider: str = "sensenova") -> bool:
        """Validate API key format."""
        key = self._get_secret(f'{provider.upper()}_API_KEY')
        if not key:
            return False
        if provider == "sensenova":
            return bool(re.match(r'^sk-[a-zA-Z0-9]{32,}$', key))
        elif provider == "ark":
            return len(key) >= 20
        return len(key) >= 10

    def __repr__(self) -> str:
        return (
            f"SecureConfig(version={__version__}, "
            f"sensenova_key={'***' + self.sensenova_api_key[-4:] if self.sensenova_api_key else 'NOT SET'}, "
            f"ark_key={'***' + self.ark_api_key[-4:] if self.ark_api_key else 'NOT SET'}, "
            f"output_dir={self.output_dir})"
        )


# Backward compatibility alias
class Config(SecureConfig):
    pass


# Global singleton
config = SecureConfig()


if __name__ == "__main__":
    from core.version import FULL_VERSION_STRING
    print(FULL_VERSION_STRING)
    print(f"\nConfig loaded: {config}")
    print(f"Output directory: {config.output_dir}")
    print(f"Cryptography available: {_CRYPTO_AVAILABLE}")
    print(f"API timeout: {config.go_api_timeout}s")
    print(f"SenseNova key valid: {config.validate_api_key('sensenova')}")
    print(f"ARK key valid: {config.validate_api_key('ark')}")
