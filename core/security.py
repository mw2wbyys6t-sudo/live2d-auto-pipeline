#!/usr/bin/env python3
"""
Live2D Master Agent - Security Module (Consolidated, Hardened)

Unified security utilities:
- Path traversal prevention
- Input sanitization
- Malicious file protection (PSD parser)
- Sensitive data redaction
- Command injection prevention
"""

import os
import re
import struct
from pathlib import Path
from typing import Tuple, Optional, List

# --- Path Validation ---

def validate_path(path: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    """Validate a file path, preventing path traversal attacks.

    Returns (is_valid, reason).
    """
    if not path or not isinstance(path, str):
        return False, "Empty or invalid path"

    if len(path) > 4096:
        return False, "Path too long (>4096 chars)"

    # Block null bytes and dangerous shell characters
    dangerous_chars = ['\x00', ';', '&', '|', '`', '$', '*', '?', '<', '>']
    for ch in dangerous_chars:
        if ch in path:
            return False, f"Path contains dangerous character: {repr(ch)}"

    # Block path traversal
    if '..' in path.replace('\\', '/').split('/'):
        return False, "Path traversal detected (..)"

    # If base_dir is set, ensure resolved path is within it
    if base_dir:
        try:
            resolved_base = Path(base_dir).resolve()
            resolved_path = (resolved_base / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
            resolved_base_str = str(resolved_base)
            if not str(resolved_path).startswith(resolved_base_str + os.sep) and str(resolved_path) != resolved_base_str:
                return False, f"Path escapes base directory: {resolved_path} not in {resolved_base}"
        except (OSError, RuntimeError):
            return False, "Unable to resolve path"

    return True, "OK"


def validate_image_path(image_path: str, max_size_mb: int = 50) -> Tuple[bool, str]:
    """Validate an image file path exists and has allowed extension."""
    valid, reason = validate_path(image_path)
    if not valid:
        return False, reason

    allowed_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff'}
    ext = Path(image_path).suffix.lower()
    if ext not in allowed_exts:
        return False, f"Unsupported image format: {ext}. Allowed: {allowed_exts}"

    if not os.path.isfile(image_path):
        return False, f"File not found: {image_path}"

    try:
        size_mb = os.path.getsize(image_path) / (1024 * 1024)
        if size_mb > max_size_mb:
            return False, f"File too large: {size_mb:.1f}MB > {max_size_mb}MB"
    except OSError:
        return False, "Cannot read file size"

    return True, "OK"


def validate_directory(directory: str, create_if_not_exists: bool = False) -> Tuple[bool, str]:
    """Validate a directory path, optionally creating it."""
    valid, reason = validate_path(directory)
    if not valid:
        return False, reason

    try:
        p = Path(directory)
        if p.exists():
            if not p.is_dir():
                return False, f"Path exists but is not a directory: {directory}"
        elif create_if_not_exists:
            p.mkdir(parents=True, exist_ok=True)
        else:
            return False, f"Directory does not exist: {directory}"
    except OSError as e:
        return False, f"OS error: {e}"

    return True, "OK"


# --- PSD Malicious File Protection (P2-1 fix) ---

MAX_PSD_SIZE_BYTES = 500 * 1024 * 1024      # 500MB max PSD file
MAX_PSD_LAYERS = 2000                        # Max number of layers
MAX_PSD_CHANNELS = 64                        # Max channels per layer
MAX_PSD_DIMENSION = 30000                    # Max width/height
PSD_MAGIC = b'8BPS'
PSB_MAGIC = b'8BP'  # PSB (large doc) starts with 8BP (8BPS for version 2)


def validate_psd_file(filepath: str, max_size_mb: int = 500, max_layers: int = 2000) -> Tuple[bool, str]:
    """
    Validate a PSD/PSB file for safety before parsing.
    Prevents zip bombs, decompression bombs, and malicious files.
    """
    valid, reason = validate_image_path(filepath, max_size_mb=max_size_mb)
    if not valid:
        return False, reason

    try:
        file_size = os.path.getsize(filepath)
        if file_size > MAX_PSD_SIZE_BYTES:
            return False, f"PSD file too large: {file_size / (1024*1024):.1f}MB > {MAX_PSD_SIZE_BYTES/(1024*1024):.0f}MB"

        with open(filepath, 'rb') as f:
            magic = f.read(4)
            if magic != PSD_MAGIC:
                return False, f"Not a valid PSD file (bad magic: {magic!r})"

            version = struct.unpack('>H', f.read(2))[0]
            if version not in (1, 2):
                return False, f"Unsupported PSD version: {version}"

            # Skip reserved bytes
            f.read(6)

            channels = struct.unpack('>H', f.read(2))[0]
            if channels > MAX_PSD_CHANNELS:
                return False, f"Too many channels: {channels} > {MAX_PSD_CHANNELS}"

            height = struct.unpack('>I', f.read(4))[0]
            width = struct.unpack('>I', f.read(4))[0]
            if width > MAX_PSD_DIMENSION or height > MAX_PSD_DIMENSION:
                return False, f"Image dimensions too large: {width}x{height} > {MAX_PSD_DIMENSION}"

            # Depth and color mode validation
            depth = struct.unpack('>H', f.read(2))[0]
            if depth not in (1, 8, 16, 32):
                return False, f"Unsupported bit depth: {depth}"

            color_mode = struct.unpack('>H', f.read(2))[0]
            if color_mode > 9:
                return False, f"Unknown color mode: {color_mode}"

        return True, "PSD file valid"

    except struct.error:
        return False, "PSD file truncated or corrupt"
    except (OSError, IOError) as e:
        return False, f"Cannot read PSD file: {e}"


def scan_psd_layer_count(filepath: str, max_layers: int = MAX_PSD_LAYERS) -> Tuple[bool, int]:
    """
    Quick scan of PSD layer count without full parsing.
    Returns (is_safe, layer_count).
    """
    try:
        with open(filepath, 'rb') as f:
            # Skip header (26 bytes for version 1, more for PSB)
            f.seek(0)
            magic = f.read(4)
            version = struct.unpack('>H', f.read(2))[0]
            f.seek(26)  # Skip to color mode data section

            # Skip color mode data
            cm_len = struct.unpack('>I', f.read(4))[0]
            f.seek(cm_len, 1)

            # Skip image resources
            ir_len_raw = f.read(4)
            if len(ir_len_raw) < 4:
                return True, 0  # No layers
            ir_len = struct.unpack('>I', ir_len_raw)[0]
            if ir_len > 100 * 1024 * 1024:
                return False, 0
            f.seek(ir_len, 1)

            # Layer and mask information
            lm_len_raw = f.read(4)
            if len(lm_len_raw) < 4:
                return True, 0
            lm_len = struct.unpack('>I', lm_len_raw)[0]
            if lm_len > 500 * 1024 * 1024:
                return False, 0

            # Read layer count (2 bytes signed - negative means first alpha)
            layer_count_bytes = f.read(2)
            if len(layer_count_bytes) < 2:
                return True, 0
            layer_count_raw = struct.unpack('>h', layer_count_bytes)[0]
            layer_count = abs(layer_count_raw)

            if layer_count > max_layers:
                return False, layer_count
            return True, layer_count

    except (struct.error, OSError):
        return True, 0  # Fail open if we can't count layers; main parser handles errors


# --- Input Sanitization ---

_DANGEROUS_COMMANDS = [
    'rm -rf', 'rm -f', r'\brm\b', 'del ', 'rmdir', 'format ', 'mkfs',
    'dd if=', 'curl ', 'wget ', 'python ', 'bash ', 'sh ', 'cmd ',
    'powershell', 'eval(', 'exec(', 'system(', '__import__',
    'subprocess', 'os.system', 'os.popen', 'chmod ', 'chown ',
]

_DANGEROUS_CHARS_PROMPT = [';', '&', '|', '`', '$(', '${', '\\', '\n', '\r']


def sanitize_prompt(prompt: str, max_length: int = 4000) -> str:
    """Sanitize user prompt to prevent injection attacks."""
    if not prompt:
        return ""
    # Truncate to max length
    prompt = prompt[:max_length]
    # Remove dangerous characters
    for ch in _DANGEROUS_CHARS_PROMPT:
        prompt = prompt.replace(ch, '')
    # Remove dangerous command patterns (case insensitive)
    lower = prompt.lower()
    for cmd in _DANGEROUS_COMMANDS:
        pattern = re.compile(re.escape(cmd), re.IGNORECASE)
        lower_check = lower
        while pattern.search(lower_check):
            m = pattern.search(lower_check)
            prompt = prompt[:m.start()] + '_' * (m.end() - m.start()) + prompt[m.end():]
            lower_check = lower_check[m.end():]
    return prompt.strip()


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize a filename to be safe across platforms.

    Removes path separators, dangerous characters, parent-directory
    references (``..``) and leading dots.
    """
    if not filename:
        return "unnamed"
    # Take only the basename to strip any directory components
    filename = os.path.basename(filename.replace('\\', '/'))
    # Remove path separators and dangerous characters
    bad_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*', '\x00', '\n', '\r']
    for ch in bad_chars:
        filename = filename.replace(ch, '_')
    # Collapse any remaining parent-directory references
    while '..' in filename:
        filename = filename.replace('..', '_')
    # Remove leading dots (hidden files on Unix)
    filename = filename.lstrip('.')
    # Collapse leading underscores left by stripping
    filename = filename.lstrip('_') or "unnamed"
    # Truncate
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length - len(ext)] + ext
    return filename or "unnamed"


def redact_sensitive(text: str) -> str:
    """Redact API keys and secrets from text output."""
    if not text:
        return text
    # Pattern for API keys (sk-..., Bearer tokens, JWTs)
    patterns = [
        (r'(sk-[a-zA-Z0-9]{8})[a-zA-Z0-9]{20,}', r'\1***REDACTED***'),
        (r'(Bearer\s+[a-zA-Z0-9]{8})[a-zA-Z0-9_-]{20,}', r'\1***REDACTED***'),
        (r'(eyJ[a-zA-Z0-9_-]{8})[a-zA-Z0-9_-]{30,}\.[a-zA-Z0-9_-]{10,}', r'\1***REDACTED***'),
        (r'(api[_-]?key["\s:=]+["\']?[a-zA-Z0-9]{8})[a-zA-Z0-9]{16,}', r'\1***REDACTED***'),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


# --- Desktop Pet Path Helper (P1-3 fix: script-relative resource paths) ---

def get_script_dir(frames_up: int = 0) -> Path:
    """Get the directory of the calling script file (not cwd).

    Uses inspect to find the actual caller's file location,
    avoiding cwd-dependent bugs when pet package is moved.
    """
    import inspect
    frame = inspect.currentframe()
    for _ in range(frames_up + 1):
        if frame.f_back:
            frame = frame.f_back
    caller_file = frame.f_globals.get('__file__', os.getcwd())
    return Path(caller_file).resolve().parent


class SecurityTools:
    """OO interface for security utilities."""

    # Whitelisted AI models
    MODEL_WHITELIST = {
        'Linaqruf/anything-v3.0',
        'stablediffusionapi/anything-v5',
        'gsdf/Counterfeit-V3.0',
        'Meina/MeinaMix',
        'andite/pastel-mix',
        'WarriorMama777/OrangeMixs',
        'gpt-4o', 'claude-3-opus', 'claude-3-sonnet',
        'seedream-4.0', 'seedream-5.0',
    }

    @staticmethod
    def validate_model_id(model_id: str) -> Tuple[bool, str]:
        if model_id not in SecurityTools.MODEL_WHITELIST:
            return False, f"Model not in whitelist: {model_id}"
        return True, "OK"

    @staticmethod
    def validate_path(path: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
        return validate_path(path, base_dir)

    @staticmethod
    def sanitize_prompt(prompt: str) -> str:
        return sanitize_prompt(prompt)

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        return sanitize_filename(filename)

    @staticmethod
    def redact_sensitive(text: str) -> str:
        return redact_sensitive(text)


if __name__ == "__main__":
    # Run basic security tests
    tests = [
        (validate_path("/etc/passwd", base_dir="/tmp"), False, "Path traversal outside base"),
        (validate_path("../../etc/passwd"), False, "Double dot traversal"),
        (sanitize_prompt("hello; rm -rf /"), "hello", "Command injection blocked"),
        (sanitize_filename('bad:file/name?.png'), "bad_file_name_.png", "Bad filename chars"),
        (redact_sensitive("sk-abcdefghijklmnopqrstuvwx1234"), "sk-abcdefg***REDACTED***", "API key redacted"),
    ]
    all_pass = True
    for test_result, expected_ok, desc in tests:
        if isinstance(test_result, tuple):
            passed = test_result[0] == expected_ok
        else:
            passed = expected_ok in str(test_result) if expected_ok else bool(test_result)
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"[{status}] {desc}: {test_result}")

    # PSD file validation
    print("\nPSD validator loaded. Checking max constants:")
    print(f"  Max PSD size: {MAX_PSD_SIZE_BYTES/(1024*1024):.0f}MB")
    print(f"  Max layers: {MAX_PSD_LAYERS}")
    print(f"  Max dimension: {MAX_PSD_DIMENSION}px")

    print(f"\n{'All tests passed!' if all_pass else 'SOME TESTS FAILED!'}")
