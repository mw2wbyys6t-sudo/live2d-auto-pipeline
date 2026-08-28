#!/usr/bin/env python3
"""
Live2D Master Agent - Unified Logging & Telemetry System (DEF-007)

Provides structured logging with:
- Console output with rich formatting (if available) or plain text
- File-based log rotation
- Telemetry event tracking (opt-in)
- Sensitive data redaction
- Multiple log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
"""

import os
import sys

# Silence the pygame welcome banner globally (it prints to stdout and would
# corrupt the workflow's --json output). Set before pygame is imported.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import json
import time
import logging
import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any, List

# Sensitive key patterns that should always be redacted
_SENSITIVE_PATTERNS = [
    "api_key", "apikey", "secret", "password", "token",
    "authorization", "auth", "credential", "private_key",
    "ARK_API_KEY", "SENSENOVA_API_KEY",
]


def _redact_sensitive(data: Any) -> Any:
    """Recursively redact sensitive fields from data structures."""
    if isinstance(data, dict):
        return {
            k: ("***REDACTED***" if any(p in k.lower() for p in _SENSITIVE_PATTERNS) else _redact_sensitive(v))
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [_redact_sensitive(item) for item in data]
    elif isinstance(data, str) and len(data) > 20 and data.startswith(("sk-", "Bearer ", "eyJ")):
        return data[:8] + "***REDACTED***"
    return data


class Live2DLogger:
    """Unified logger for the Live2D Master Agent system."""

    _instances: Dict[str, "Live2DLogger"] = {}

    def __init__(self, name: str = "live2d", log_dir: Optional[str] = None, level: str = "INFO"):
        self.name = name
        self._logger = logging.getLogger(f"live2d.{name}")
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self._telemetry_events: List[Dict[str, Any]] = []
        self._telemetry_enabled = os.environ.get("LIVE2D_TELEMETRY", "0") == "1"
        self._start_time = time.time()

        # Avoid duplicate handlers
        if not self._logger.handlers:
            self._setup_console_handler()
            self._setup_file_handler(log_dir)

    def _setup_console_handler(self):
        """Set up console handler with clean formatting.

        When ``LIVE2D_JSON_MODE`` is set (i.e. the CLI is emitting a result
        JSON document on stdout), all console logging goes to *stderr* so
        stdout contains only the JSON payload. This keeps the Go bridge /
        e2e parsers from having to scrape braces out of interleaved logs.
        """
        json_mode = os.environ.get("LIVE2D_JSON_MODE", "0") == "1"
        console_stream = sys.stderr if json_mode else sys.stdout
        handler = logging.StreamHandler(console_stream)
        handler.setLevel(logging.DEBUG)
        # Use simple format - rich optional
        try:
            from rich.console import Console
            from rich.logging import RichHandler
            handler = RichHandler(
                console=Console(stderr=True),
                show_time=True,
                show_path=False,
                markup=False,
                rich_tracebacks=True,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
        except ImportError:
            handler.setFormatter(logging.Formatter(
                "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
                datefmt="%H:%M:%S"
            ))
        self._logger.addHandler(handler)

    def _setup_file_handler(self, log_dir: Optional[str] = None):
        """Set up rotating file handler."""
        if log_dir is None:
            root = os.environ.get("LIVE2D_PROJECT_ROOT", str(Path.home()))
            log_dir = str(Path(root) / "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = str(Path(log_dir) / "live2d.log")
        handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-7s %(name)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        self._logger.addHandler(handler)

    def debug(self, msg: str, **kwargs):
        self._logger.debug(msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._logger.info(msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._logger.warning(msg, **kwargs)

    def error(self, msg: str, exc_info: bool = False, **kwargs):
        self._logger.error(msg, exc_info=exc_info, **kwargs)

    def critical(self, msg: str, exc_info: bool = True, **kwargs):
        self._logger.critical(msg, exc_info=exc_info, **kwargs)

    def success(self, msg: str, **kwargs):
        """Log a success message (INFO level with visual indicator)."""
        self._logger.info(f"[OK] {msg}", **kwargs)

    def step(self, step_num: int, total: int, msg: str):
        """Log a pipeline step."""
        self._logger.info(f"[{step_num}/{total}] {msg}")

    def telemetry(self, event_type: str, data: Optional[Dict[str, Any]] = None):
        """Record a telemetry event (opt-in only)."""
        if not self._telemetry_enabled:
            return
        event = {
            "event": event_type,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "elapsed_ms": int((time.time() - self._start_time) * 1000),
            "data": _redact_sensitive(data or {}),
        }
        self._telemetry_events.append(event)
        self._logger.debug(f"Telemetry: {event_type}")

    def get_telemetry(self) -> List[Dict[str, Any]]:
        """Return collected telemetry events."""
        return list(self._telemetry_events)

    def flush_telemetry(self, filepath: Optional[str] = None):
        """Flush telemetry events to a JSON file."""
        if filepath is None:
            root = os.environ.get("LIVE2D_PROJECT_ROOT", str(Path.home()))
            filepath = str(Path(root) / "logs" / "telemetry.jsonl")
        os.makedirs(str(Path(filepath).parent), exist_ok=True)
        with open(filepath, "a", encoding="utf-8") as f:
            for event in self._telemetry_events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._telemetry_events.clear()

    def section(self, title: str):
        """Log a section header."""
        self._logger.info("")
        self._logger.info("=" * 60)
        self._logger.info(f"  {title}")
        self._logger.info("=" * 60)


def get_logger(name: str = "core", level: str = "INFO") -> Live2DLogger:
    """Get or create a named logger instance."""
    if name not in Live2DLogger._instances:
        Live2DLogger._instances[name] = Live2DLogger(name=name, level=level)
    return Live2DLogger._instances[name]


# Default logger instance
log = get_logger("core")


if __name__ == "__main__":
    log = get_logger("test", level="DEBUG")
    log.section("Logger Test")
    log.debug("Debug message")
    log.info("Info message")
    log.success("Success message")
    log.warning("Warning message")
    log.error("Error message")
    log.step(1, 5, "Testing step logging")
    log.telemetry("test_event", {"key": "value", "api_key": "sk-1234567890abcdef"})
    events = log.get_telemetry()
    print(f"Telemetry events: {len(events)}")
    if events:
        print(f"Redacted data: {events[0]['data']}")
