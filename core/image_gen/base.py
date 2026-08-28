#!/usr/bin/env python3
"""
Live2D Master Agent - Image Provider Base Class (Provider Registry Pattern)

Abstract base for all image generation providers. The ProviderRouter
automatically discovers and routes to registered providers.
"""

import abc
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable


class GenerationError(Exception):
    """Raised when image generation fails."""
    def __init__(self, message: str, provider: str = "", retryable: bool = True):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


@dataclass
class GenerationResult:
    """Result of an image generation request."""
    success: bool
    image_path: Optional[str] = None
    image_data: Optional[bytes] = None
    provider: str = ""
    model: str = ""
    prompt: str = ""
    width: int = 0
    height: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.success and self.image_path is not None


class ImageProvider(abc.ABC):
    """Abstract base class for image generation providers."""

    # Subclasses must set these
    name: str = "base"
    display_name: str = "Base Provider"
    requires_api_key: bool = False
    max_retries: int = 2
    timeout: int = 60

    def __init__(self, config=None):
        from core.config import config as global_config
        self.config = config or global_config
        self._on_progress: Optional[Callable[[str, float], None]] = None

    def set_progress_callback(self, callback: Callable[[str, float], None]):
        """Set callback for progress updates: callback(phase, percent_0_100)."""
        self._on_progress = callback

    def _report_progress(self, phase: str, percent: float):
        if self._on_progress:
            self._on_progress(phase, min(max(percent, 0), 100))

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is available (dependencies installed, keys configured)."""
        ...

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1024,
        height: int = 1024,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs
    ) -> GenerationResult:
        """Generate an image and save to output_path."""
        ...

    def get_setup_guide(self) -> str:
        """Return setup instructions for this provider."""
        return f"{self.display_name}: No setup required."

    def _save_bytes(self, data: bytes, output_path: str) -> str:
        """Save image bytes to file, creating parent dirs."""
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)

    def _timed_generate(self, generate_fn, *args, **kwargs) -> GenerationResult:
        """Wrap generate with timing and error handling."""
        start = time.time()
        try:
            result = generate_fn(*args, **kwargs)
            result.elapsed_seconds = time.time() - start
            return result
        except GenerationError:
            raise
        except Exception as e:
            elapsed = time.time() - start
            return GenerationResult(
                success=False,
                provider=self.name,
                error=str(e),
                elapsed_seconds=elapsed,
            )
