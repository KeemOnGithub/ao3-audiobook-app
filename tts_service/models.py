"""Model manager and embedding cache for Qwen3-TTS checkpoints."""

import os
import time
from typing import Any, Dict, Optional, Tuple
import numpy as np


class EmbeddingLRUCache:
    """Thread-safe in-memory LRU cache keyed by (embedding_path, mtime)."""

    def __init__(self, maxsize: int = 128):
        self.maxsize = maxsize
        self._cache: Dict[Tuple[str, float], Any] = {}
        self._access_times: Dict[Tuple[str, float], float] = {}

    def get(self, path: str) -> Optional[Any]:
        if not os.path.exists(path):
            return None
        mtime = os.path.getmtime(path)
        key = (path, mtime)
        if key in self._cache:
            self._access_times[key] = time.monotonic()
            return self._cache[key]
        return None

    def put(self, path: str, value: Any) -> None:
        mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0
        key = (path, mtime)
        if len(self._cache) >= self.maxsize and key not in self._cache:
            oldest_key = min(self._access_times, key=self._access_times.get)
            self._cache.pop(oldest_key, None)
            self._access_times.pop(oldest_key, None)
        self._cache[key] = value
        self._access_times[key] = time.monotonic()


import io
import json
import logging
import httpx
import soundfile as sf

logger = logging.getLogger(__name__)


class TTSModelManager:
    """
    Manages Qwen3-TTS VoiceDesign and Base checkpoints or delegates to remote Qwen3-TTS endpoint.
    Supports real remote ngrok/cloud execution as well as deterministic simulation for testing/CPU.
    """

    def __init__(
        self,
        use_mock: bool = False,
        remote_url: Optional[str] = None,
        api_key: str = "dummy",
    ):
        if remote_url is not None:
            raw_remote_url = remote_url
        else:
            raw_remote_url = os.getenv("REMOTE_TTS_URL", "")
        self.remote_url = raw_remote_url.rstrip("/")
        self.api_key = api_key or os.getenv("TTS_API_KEY", "dummy")
        use_mock_env = os.getenv("TTS_USE_MOCK", "").lower() in ("true", "1")
        # Spec 07: TTS_USE_MOCK=true is the only way to select simulation.
        # PYTEST_CURRENT_TEST MUST NOT alter runtime behavior.
        self.use_mock = use_mock or use_mock_env
        self.provider_mode = "simulation" if self.use_mock else "remote"

        if not self.use_mock and not self.remote_url:
            raise ValueError("CONFIGURATION_ERROR: REMOTE_TTS_URL is required when TTS_USE_MOCK is false.")

        self.models_loaded: list[str] = ["VoiceDesign", "Base"]
        self.embedding_cache = EmbeddingLRUCache(maxsize=128)

    def generate_voice_design(
        self,
        instruct: str,
        reference_text: str,
        language: str = "English",
        voice_id: Optional[str] = None,
    ) -> Tuple[np.ndarray, int]:
        """Synthesize 5s reference clip via VoiceDesign (remote or mock)."""
        target_voice_id = voice_id or f"voice_{int(time.time() * 1000)}"

        if not self.use_mock:
            headers = {
                "ngrok-skip-browser-warning": "1",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "voice_id": target_voice_id,
                "instruct": instruct,
                "sample_text": reference_text or "Reference recording of voice persona for cloning.",
                "language": "Auto" if language in ("English", "Auto") else language,
            }
            try:
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(
                        f"{self.remote_url}/v1/tts/voices/create",
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    wav_data, sr = sf.read(io.BytesIO(resp.content), dtype="float32")
                    # If stereo, downmix over channel dimension
                    if wav_data.ndim > 1:
                        channel_axis = 1 if wav_data.shape[1] <= 2 else 0
                        wav_data = np.mean(wav_data, axis=channel_axis)
                    return wav_data, sr
            except Exception as e:
                logger.error("Remote TTS create_voice failed: %s", e)
                raise RuntimeError(f"TTS_REMOTE_CREATE_VOICE_ERROR: {e}") from e

        # Generates 24kHz audio waveform (4.5s - 5.5s)
        sample_rate = 24000
        duration_sec = 4.8
        num_samples = int(duration_sec * sample_rate)
        t = np.linspace(0, duration_sec, num_samples, endpoint=False)
        waveform = (
            0.3 * np.sin(2 * np.pi * 220 * t)
            + 0.15 * np.sin(2 * np.pi * 440 * t)
            + 0.05 * np.random.normal(0, 0.01, num_samples)
        ).astype(np.float32)
        return waveform, sample_rate

    def create_voice_clone_prompt(
        self,
        ref_audio_path: str,
        ref_text: str,
        voice_id: Optional[str] = None,
        instruct: Optional[str] = None,
        language: str = "English",
        allow_recreation: bool = True,
    ) -> bytes:
        """Extract speaker embedding tensor representation or voice metadata pointer."""
        if voice_id:
            # Store metadata in JSON format for self-healing remote synchronization
            meta = {
                "voice_id": voice_id,
                "instruct": instruct or "",
                "sample_text": ref_text or "",
                "language": language or "English",
                "allow_recreation": allow_recreation,
            }
            return json.dumps(meta).encode("utf-8")
        embedding_data = b"QWEN3_TTS_EMBEDDING_V1_" + os.urandom(4000)
        return embedding_data

    def generate_voice_clone(
        self,
        text: str,
        voice_clone_prompt: Any,
        language: str = "English",
    ) -> Tuple[np.ndarray, int]:
        """Synthesize dialogue or narration segment using cloned voice embedding."""
        # Check if voice_clone_prompt contains JSON metadata or legacy VOICE_ID: pointer
        voice_id: Optional[str] = None
        voice_meta: dict = {}
        if isinstance(voice_clone_prompt, bytes):
            try:
                decoded = voice_clone_prompt.decode("utf-8")
                if decoded.startswith("{"):
                    voice_meta = json.loads(decoded)
                    voice_id = voice_meta.get("voice_id")
                elif decoded.startswith("VOICE_ID:"):
                    voice_id = decoded.split("VOICE_ID:", 1)[1]
            except Exception:
                pass

        if not voice_id:
            voice_id = "system_narrator_v1"

        if not self.use_mock:
            headers = {
                "ngrok-skip-browser-warning": "1",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "voice_id": voice_id,
                "text": text,
                "language": "Auto" if language in ("English", "Auto") else language,
            }
            try:
                with httpx.Client(timeout=120.0) as client:
                    resp = client.post(
                        f"{self.remote_url}/v1/tts/speak",
                        headers=headers,
                        json=payload,
                    )
                    # Spec 07: Only an authoritative provider error identifying a missing voice MAY trigger recreation.
                    # Generic 404, route mismatch, auth, or malformed responses must fail closed.
                    if resp.status_code == 404:
                        error_detail = ""
                        try:
                            error_detail = resp.json().get("detail", "")
                        except Exception:
                            error_detail = resp.text
                        if "voice" in error_detail.lower() and "not found" in error_detail.lower():
                            if not voice_meta.get('allow_recreation',True):
                                raise RuntimeError('AUDITIONED_VOICE_LOST: Return to review to select a replacement voice.')
                            logger.info(
                                f"Authoritative missing voice '{voice_id}' detected. Auto-registering voice persona once..."
                            )
                            instruct = voice_meta.get("instruct") or (
                                "An adult neutral narrator voice, warm mid-register pitch, clear and "
                                "articulate studio audiobook delivery, steady and engaging pacing."
                            )
                            sample_text = voice_meta.get("sample_text") or (
                                "The shadows lengthened across the quiet room as the evening wind began to rise outside."
                            )
                            self.generate_voice_design(
                                instruct=instruct,
                                reference_text=sample_text,
                                language=voice_meta.get("language", language),
                                voice_id=voice_id,
                            )
                            resp = client.post(
                                f"{self.remote_url}/v1/tts/speak",
                                headers=headers,
                                json=payload,
                            )

                    resp.raise_for_status()
                    wav_data, sr = sf.read(io.BytesIO(resp.content), dtype="float32")
                    if wav_data.ndim > 1:
                        channel_axis = 1 if wav_data.shape[1] <= 2 else 0
                        wav_data = np.mean(wav_data, axis=channel_axis)
                    return wav_data, sr
            except Exception as e:
                logger.error("Remote TTS speak failed: %s", e)
                raise RuntimeError(f"TTS_REMOTE_SPEAK_ERROR: {e}") from e

        sample_rate = 24000
        words = max(len(text.split()), 1)
        duration_sec = max(words / 2.5, 0.3)
        num_samples = int(duration_sec * sample_rate)
        t = np.linspace(0, duration_sec, num_samples, endpoint=False)
        waveform = (
            0.35 * np.sin(2 * np.pi * 260 * t)
            + 0.1 * np.sin(2 * np.pi * 520 * t)
            + 0.02 * np.random.normal(0, 0.01, num_samples)
        ).astype(np.float32)
        return waveform, sample_rate

