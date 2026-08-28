#!/usr/bin/env python3
"""
Microphone audio capture and real-time feature analysis.

Supports two backends with graceful fallback:
1. sounddevice (preferred)
2. pyaudio (fallback)

Provides RMS volume, pitch estimation, spectral centroid, zero-crossing
rate, and speaking detection for lip-sync and emotion-aware animation.
"""

import math
import threading
import time
from typing import Optional, Dict

from core.logger import get_logger

log = get_logger("audio")

# Optional backend imports
try:
    import numpy as np
    _NP_AVAILABLE = True
except ImportError:
    _NP_AVAILABLE = False
    np = None  # type: ignore

try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False
    sd = None  # type: ignore

try:
    import pyaudio
    _PA_AVAILABLE = True
except ImportError:
    _PA_AVAILABLE = False
    pyaudio = None  # type: ignore


class AudioCapture:
    """Real-time microphone audio capture and feature extraction.

    Parameters
    ----------
    sample_rate : int
        Audio sample rate in Hz (default 44100).
    chunk_size : int
        Frames per buffer chunk (default 1024).
    channels : int
        Number of audio channels (default 1, mono).
    """

    def __init__(
        self,
        sample_rate: int = 44100,
        chunk_size: int = 1024,
        channels: int = 1,
    ):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.channels = channels

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self._pa = None  # pyaudio instance if using pyaudio backend
        self._backend: Optional[str] = None

        # Latest audio data and features
        self._latest_chunk = None  # numpy array
        self._lock = threading.Lock()

        # Smoothed features
        self._volume_smooth: float = 0.0
        self._mouth_open_smooth: float = 0.0
        self._pitch_smooth: float = 0.0
        self._smooth_factor: float = 0.3

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start microphone capture in a background thread."""
        if not _NP_AVAILABLE:
            log.error("numpy is required for audio capture. Install: pip install numpy")
            return

        if self._running:
            return

        if _SD_AVAILABLE:
            self._start_sounddevice()
        elif _PA_AVAILABLE:
            self._start_pyaudio()
        else:
            log.error(
                "No audio backend available. Install one of: "
                "pip install sounddevice  OR  pip install pyaudio"
            )
            return

        if self._backend:
            self._running = True
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            log.success(f"AudioCapture started (backend={self._backend})")

    def stop(self) -> None:
        """Stop microphone capture and release resources."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._stream is not None:
            try:
                if self._backend == "sounddevice":
                    self._stream.close()
                elif self._backend == "pyaudio":
                    self._stream.stop_stream()
                    self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        log.info("AudioCapture stopped")

    def is_running(self) -> bool:
        """Return True if audio capture is active."""
        return self._running

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "AudioCapture":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Backend initialization
    # ------------------------------------------------------------------

    def _start_sounddevice(self) -> None:
        """Initialize sounddevice InputStream."""
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                blocksize=self.chunk_size,
                dtype="float32",
            )
            self._stream.start()
            self._backend = "sounddevice"
        except Exception as e:
            log.warning(f"sounddevice failed to start: {e}")
            self._stream = None
            self._backend = None

    def _start_pyaudio(self) -> None:
        """Initialize PyAudio stream."""
        try:
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
            )
            self._backend = "pyaudio"
        except Exception as e:
            log.warning(f"pyaudio failed to start: {e}")
            if self._pa:
                try:
                    self._pa.terminate()
                except Exception:
                    pass
            self._pa = None
            self._stream = None
            self._backend = None

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        """Background thread: read audio chunks continuously."""
        while self._running:
            try:
                chunk = self._read_chunk()
                if chunk is not None:
                    with self._lock:
                        self._latest_chunk = chunk
                    self._update_features(chunk)
            except Exception as e:
                log.debug(f"Audio capture error: {e}")
                time.sleep(0.01)

    def _read_chunk(self) -> Optional["np.ndarray"]:
        """Read one audio chunk from the active backend."""
        if self._backend == "sounddevice" and self._stream is not None:
            try:
                data, overflowed = self._stream.read(self.chunk_size)
                return data.flatten().astype(np.float32)
            except Exception:
                return None
        elif self._backend == "pyaudio" and self._stream is not None:
            try:
                raw = self._stream.read(self.chunk_size, exception_on_overflow=False)
                return np.frombuffer(raw, dtype=np.float32)
            except Exception:
                return None
        return None

    # ------------------------------------------------------------------
    # Feature computation
    # ------------------------------------------------------------------

    def _update_features(self, chunk: "np.ndarray") -> None:
        """Compute and smooth audio features from a chunk."""
        if not _NP_AVAILABLE or chunk is None or len(chunk) == 0:
            return

        # RMS volume
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        self._volume_smooth = self._exp_smooth(self._volume_smooth, rms, self._smooth_factor)

        # Mouth open (non-linear mapping with attack/decay)
        target_mouth = min(1.0, rms * 8.0)
        # Fast attack, slow decay for natural lip movement
        alpha = 0.5 if target_mouth > self._mouth_open_smooth else 0.15
        self._mouth_open_smooth = (
            self._mouth_open_smooth * (1.0 - alpha) + target_mouth * alpha
        )

        # Pitch estimation via autocorrelation
        pitch = self._estimate_pitch(chunk)
        if pitch > 0:
            self._pitch_smooth = self._exp_smooth(
                self._pitch_smooth, pitch, self._smooth_factor * 0.5
            )

    @staticmethod
    def _exp_smooth(prev: float, current: float, alpha: float) -> float:
        """Exponential smoothing."""
        return prev * (1.0 - alpha) + current * alpha

    def _estimate_pitch(self, chunk: "np.ndarray") -> float:
        """Estimate fundamental frequency via autocorrelation.

        Returns frequency in Hz, or 0.0 if no clear pitch detected.
        """
        if not _NP_AVAILABLE:
            return 0.0

        # Apply window and zero-mean
        windowed = chunk * np.hanning(len(chunk))
        windowed = windowed - np.mean(windowed)

        if np.max(np.abs(windowed)) < 1e-4:
            return 0.0

        # Autocorrelation
        corr = np.correlate(windowed, windowed, mode="full")
        corr = corr[len(corr) // 2:]

        # Find first peak after the initial zero crossing
        min_lag = int(self.sample_rate / 400)  # 400 Hz max
        max_lag = int(self.sample_rate / 80)   # 80 Hz min

        if max_lag >= len(corr):
            max_lag = len(corr) - 1
        if min_lag >= max_lag:
            return 0.0

        search_region = corr[min_lag:max_lag]
        if len(search_region) == 0:
            return 0.0

        peak_idx = int(np.argmax(search_region)) + min_lag
        if corr[peak_idx] < corr[0] * 0.1:
            return 0.0  # Weak correlation

        # Parabolic interpolation for sub-sample accuracy
        if 1 <= peak_idx < len(corr) - 1:
            alpha_p = corr[peak_idx - 1]
            beta_p = corr[peak_idx]
            gamma_p = corr[peak_idx + 1]
            denom = alpha_p - 2 * beta_p + gamma_p
            if abs(denom) > 1e-10:
                peak_idx += 0.5 * (alpha_p - gamma_p) / denom

        return float(self.sample_rate / peak_idx) if peak_idx > 0 else 0.0

    def _spectral_centroid(self, chunk: "np.ndarray") -> float:
        """Compute spectral centroid (brightness) in Hz."""
        if not _NP_AVAILABLE or len(chunk) == 0:
            return 0.0
        magnitudes = np.abs(np.fft.rfft(chunk * np.hanning(len(chunk))))
        freqs = np.fft.rfftfreq(len(chunk), 1.0 / self.sample_rate)
        total = np.sum(magnitudes)
        if total < 1e-10:
            return 0.0
        return float(np.sum(freqs * magnitudes) / total)

    def _zero_crossing_rate(self, chunk: "np.ndarray") -> float:
        """Compute zero-crossing rate (0..1)."""
        if not _NP_AVAILABLE or len(chunk) < 2:
            return 0.0
        signs = np.sign(chunk)
        crossings = np.sum(np.abs(np.diff(signs)) > 0)
        return float(crossings) / len(chunk)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_volume(self) -> float:
        """Return current RMS volume, normalized 0..1."""
        return min(1.0, self._volume_smooth * 10.0)

    def get_mouth_open_amount(self) -> float:
        """Return mouth open amount 0..1, with smoothing for lip-sync."""
        return max(0.0, min(1.0, self._mouth_open_smooth))

    def get_pitch(self) -> float:
        """Return estimated fundamental frequency in Hz."""
        return self._pitch_smooth

    def get_latest_chunk(self):
        """Return the most recent raw audio chunk (numpy float32) or None.

        Used by beat-detection consumers that need the raw signal rather
        than the pre-computed RMS/pitch features.
        """
        with self._lock:
            return self._latest_chunk

    def is_speaking(self, threshold: float = 0.05) -> bool:
        """Return True if current volume exceeds the speaking threshold."""
        return self.get_volume() > threshold

    def get_audio_features(self) -> Dict[str, float]:
        """Return a dict of current audio features.

        Keys: volume (0..1), mouth_open (0..1), pitch (Hz),
        spectral_centroid (Hz), zcr (0..1).
        """
        with self._lock:
            chunk = self._latest_chunk

        sc = 0.0
        zcr = 0.0
        if chunk is not None and _NP_AVAILABLE:
            sc = self._spectral_centroid(chunk)
            zcr = self._zero_crossing_rate(chunk)

        return {
            "volume": self.get_volume(),
            "mouth_open": self.get_mouth_open_amount(),
            "pitch": self.get_pitch(),
            "spectral_centroid": sc,
            "zcr": zcr,
        }
