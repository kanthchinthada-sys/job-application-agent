"""Talks to a local Ollama server. Free, runs on your Mac, no API key.

The rest of the app only depends on the small ``LLMClient`` interface below,
so tests can swap in a fake client and a different provider could be added
later without touching the features that use it.
"""

import json
import os
from typing import Protocol

import httpx

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"


class LLMUnavailableError(Exception):
    """Ollama is not running, or the model is not downloaded -> HTTP 503."""


class LLMTimeoutError(LLMUnavailableError):
    """The model didn't answer within the call's timeout (it may be busy or slow)."""


class LLMOutputError(Exception):
    """The model answered, but not in the expected shape -> HTTP 502."""


class LLMClient(Protocol):
    model: str

    def generate_json(self, system: str, user: str, schema: dict) -> dict: ...   # may take timeout=, max_tokens=

    def status(self) -> dict: ...


MAX_OUTPUT_TOKENS = 2400   # the longest real answer (a whole tailored resume) is about 1,300 tokens
# Long enough for the longest answer on a slow laptop: 2,400 tokens at ~10 tokens/second, plus reading the prompt
REQUEST_TIMEOUT = 330.0


class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_OLLAMA_MODEL,
                 timeout: float = REQUEST_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OllamaClient":
        return cls(
            base_url=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL),
            model=os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        )

    def generate_json(self, system: str, user: str, schema: dict, timeout: float | None = None,
                      max_tokens: int | None = None) -> dict:
        """``timeout`` / ``max_tokens``: a shorter limit for one step (reading a job posting), else the defaults."""
        timeout = timeout or self.timeout
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,      # Ollama constrains the answer to this JSON schema
            "stream": False,
            "think": False,        # skip qwen3's long "Thinking..." text
            "options": {"temperature": 0,   # as repeatable as possible
                        "num_ctx": 8192,    # room for posting + profile; the default can silently cut prompts
                        "num_predict": max_tokens or MAX_OUTPUT_TOKENS},   # a runaway answer ends as invalid, not a timeout
            "keep_alive": "30m",            # keep the model loaded between calls: no reload wait
        }
        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"The model took longer than {timeout:.0f} seconds to answer. It may be busy with another "
                "request, or this computer may be slow for it: try again, or use a smaller model (OLLAMA_MODEL)."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. Is the Ollama app running?"
            ) from exc
        if response.status_code == 404:
            raise LLMUnavailableError(f"Model '{self.model}' not found. Run: ollama pull {self.model}")
        if response.status_code != 200:
            raise LLMUnavailableError(f"Ollama returned HTTP {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
            from app import metrics          # tokens and model-load time, as Ollama reports them
            metrics.note_usage(input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"),
                               load_seconds=round((data.get("load_duration") or 0) / 1e9, 3))
            return json.loads(data["message"]["content"])
        except (KeyError, ValueError) as exc:
            raise LLMOutputError("The model's answer was not valid JSON.") from exc

    def status(self) -> dict:
        try:
            tags = httpx.get(f"{self.base_url}/api/tags", timeout=5).json()
        except (httpx.HTTPError, ValueError):
            return {"provider": "ollama", "reachable": False, "model": self.model, "model_available": False}
        names = {m.get("name") for m in tags.get("models", [])}
        return {"provider": "ollama", "reachable": True, "model": self.model, "model_available": self.model in names}
