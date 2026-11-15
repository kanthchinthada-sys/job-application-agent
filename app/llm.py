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


class LLMOutputError(Exception):
    """The model answered, but not in the expected shape -> HTTP 502."""


class LLMClient(Protocol):
    model: str

    def generate_json(self, system: str, user: str, schema: dict) -> dict: ...

    def status(self) -> dict: ...


class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_OLLAMA_MODEL, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OllamaClient":
        return cls(
            base_url=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL),
            model=os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        )

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,      # Ollama constrains the answer to this JSON schema
            "stream": False,
            "think": False,        # skip qwen3's long "Thinking..." text
            "options": {"temperature": 0},  # as repeatable as possible
        }
        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. Is the Ollama app running?"
            ) from exc
        if response.status_code == 404:
            raise LLMUnavailableError(f"Model '{self.model}' not found. Run: ollama pull {self.model}")
        if response.status_code != 200:
            raise LLMUnavailableError(f"Ollama returned HTTP {response.status_code}: {response.text[:300]}")
        try:
            return json.loads(response.json()["message"]["content"])
        except (KeyError, ValueError) as exc:
            raise LLMOutputError("The model's answer was not valid JSON.") from exc

    def status(self) -> dict:
        try:
            tags = httpx.get(f"{self.base_url}/api/tags", timeout=5).json()
        except (httpx.HTTPError, ValueError):
            return {"provider": "ollama", "reachable": False, "model": self.model, "model_available": False}
        names = {m.get("name") for m in tags.get("models", [])}
        return {"provider": "ollama", "reachable": True, "model": self.model, "model_available": self.model in names}
