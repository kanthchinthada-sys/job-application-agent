"""Talks to a local Ollama server. Free, runs on your Mac, no API key.

The rest of the app only depends on the small ``LLMClient`` interface below,
so tests can swap in a fake client and a different provider could be added
later without touching the features that use it.
"""

import json
import os
import re
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


# ---------------------------------------------------------------------------------------------------------------
# Claude and OpenAI: used when the signed-in account has saved its own API key (Profile > AI provider).
# Both get the same request the local model gets (instructions, the task, and the JSON shape of the answer) and
# must answer in exactly that shape.

DEFAULT_CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")          # low | medium | high | xhigh | max
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "medium")
CLOUD_TIMEOUT = 240.0
# Room for the model's own reasoning on top of the answer (the local model's caps are for the answer alone)
CLOUD_MAX_TOKENS = 32000

# JSON Schema keywords the providers' strict JSON modes don't accept (the answer is still validated by the app)
_UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength",
                "maxLength", "pattern", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
                "default", "examples", "title"}


def strict_schema(schema: dict, all_required: bool = False) -> dict:
    """The app's answer shape as the providers' strict JSON modes accept it: every object closed
    (additionalProperties false), constraint keywords left out (the app checks the answer itself), and for
    OpenAI every property listed as required."""
    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        # keywords are dropped; names under "properties" and "$defs" are the answer's own fields, always kept
        out = {k: ({name: walk(sub) for name, sub in v.items()} if k in ("properties", "$defs") and isinstance(v, dict)
                   else walk(v))
               for k, v in node.items() if k not in _UNSUPPORTED}
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
            if all_required:
                out["required"] = list(out.get("properties", {}))
        return out
    return walk(schema)


class ClaudeClient:
    """Claude through the Anthropic API, with the account's own key."""
    provider = "claude"
    writes_whole_resume = True     # strong enough to write the whole resume in one call (app/resume_writer.py)

    def __init__(self, api_key: str, model: str | None = None, timeout: float = CLOUD_TIMEOUT):
        import anthropic
        self._anthropic = anthropic
        self.model = model or DEFAULT_CLAUDE_MODEL
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)

    def generate_json(self, system: str, user: str, schema: dict, timeout: float | None = None,
                      max_tokens: int | None = None) -> dict:
        a = self._anthropic
        try:
            response = self.client.messages.create(
                model=self.model, max_tokens=CLOUD_MAX_TOKENS, system=system,
                messages=[{"role": "user", "content": user}],
                # every field required: an optional part of the answer (Core Skills, reserve sentences) is never skipped
                output_config={"effort": CLAUDE_EFFORT,
                               "format": {"type": "json_schema", "schema": strict_schema(schema, all_required=True)}},
            )
        except a.AuthenticationError as exc:
            raise LLMUnavailableError("Claude did not accept your API key. Check it in Profile > AI provider.") from exc
        except a.PermissionDeniedError as exc:
            raise LLMUnavailableError(f"Your Claude API key can't use {self.model}.") from exc
        except a.NotFoundError as exc:
            raise LLMUnavailableError(f"Claude model '{self.model}' was not found.") from exc
        except a.RateLimitError as exc:
            raise LLMUnavailableError("Claude is rate-limiting your key (or its credit ran out). Try again shortly.") from exc
        except a.APITimeoutError as exc:
            raise LLMTimeoutError("Claude took too long to answer. Try again.") from exc
        except a.BadRequestError as exc:
            raise LLMOutputError(f"Claude could not take this request: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise LLMUnavailableError(f"Claude returned HTTP {exc.status_code}. Try again shortly.") from exc
        except a.APIConnectionError as exc:
            raise LLMUnavailableError("Cannot reach Claude. Check your internet connection.") from exc
        from app import metrics
        usage = response.usage
        metrics.note_usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        if response.stop_reason == "refusal":
            raise LLMOutputError("Claude declined this request.")
        if response.stop_reason == "max_tokens":
            raise LLMOutputError("Claude's answer was cut off (too long).")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise LLMOutputError("Claude's answer was not valid JSON.") from exc

    def status(self) -> dict:
        try:
            self.client.models.retrieve(self.model)        # free: checks the key and the model, no tokens
        except self._anthropic.AuthenticationError:
            return {"provider": "claude", "reachable": True, "model": self.model, "model_available": False,
                    "error": "API key not accepted"}
        except self._anthropic.NotFoundError:
            return {"provider": "claude", "reachable": True, "model": self.model, "model_available": False,
                    "error": "model not found"}
        except self._anthropic.APIError:
            return {"provider": "claude", "reachable": False, "model": self.model, "model_available": False}
        return {"provider": "claude", "reachable": True, "model": self.model, "model_available": True}


def pick_openai_model(ids: list[str]) -> str | None:
    """The newest general GPT model the key can use ("gpt-5.1" over "gpt-5", never a mini/nano/audio/realtime
    variant): chosen from the key's own model list, so no model name is guessed."""
    import re as _re
    best = None
    for i in ids:
        m = _re.fullmatch(r"gpt-(\d+(?:\.\d+)?)", i)
        if m and (best is None or float(m.group(1)) > best[0]):
            best = (float(m.group(1)), i)
    return best[1] if best else None


class OpenAIClient:
    """OpenAI's GPT models through the OpenAI API, with the account's own key."""
    provider = "openai"
    writes_whole_resume = True     # strong enough to write the whole resume in one call (app/resume_writer.py)

    def __init__(self, api_key: str, model: str | None = None, timeout: float = CLOUD_TIMEOUT):
        import openai
        self._openai = openai
        self.client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model or pick_openai_model(self.available_models()) or "gpt-5"

    def available_models(self) -> list[str]:
        try:
            return [m.id for m in self.client.models.list()]
        except self._openai.OpenAIError:
            return []

    def generate_json(self, system: str, user: str, schema: dict, timeout: float | None = None,
                      max_tokens: int | None = None) -> dict:
        o = self._openai
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "answer", "strict": True, "schema": strict_schema(schema, all_required=True)}},
                max_completion_tokens=CLOUD_MAX_TOKENS,
                **({"reasoning_effort": OPENAI_REASONING_EFFORT}
                   if re.match(r"(gpt-[5-9]|o\d)", self.model) else {}),
            )
        except o.AuthenticationError as exc:
            raise LLMUnavailableError("OpenAI did not accept your API key. Check it in Profile > AI provider.") from exc
        except o.PermissionDeniedError as exc:
            raise LLMUnavailableError(f"Your OpenAI API key can't use {self.model}.") from exc
        except o.NotFoundError as exc:
            raise LLMUnavailableError(f"OpenAI model '{self.model}' was not found.") from exc
        except o.RateLimitError as exc:
            raise LLMUnavailableError("OpenAI is rate-limiting your key (or its credit ran out). Try again shortly.") from exc
        except o.APITimeoutError as exc:
            raise LLMTimeoutError("OpenAI took too long to answer. Try again.") from exc
        except o.BadRequestError as exc:
            raise LLMOutputError(f"OpenAI could not take this request: {exc.message}") from exc
        except o.APIStatusError as exc:
            raise LLMUnavailableError(f"OpenAI returned HTTP {exc.status_code}. Try again shortly.") from exc
        except o.APIConnectionError as exc:
            raise LLMUnavailableError("Cannot reach OpenAI. Check your internet connection.") from exc
        from app import metrics
        if response.usage:
            metrics.note_usage(input_tokens=response.usage.prompt_tokens, output_tokens=response.usage.completion_tokens)
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMOutputError("OpenAI declined this request.")
        if choice.finish_reason == "length":
            raise LLMOutputError("OpenAI's answer was cut off (too long).")
        try:
            return json.loads(choice.message.content or "")
        except ValueError as exc:
            raise LLMOutputError("OpenAI's answer was not valid JSON.") from exc

    def status(self) -> dict:
        try:
            self.client.models.retrieve(self.model)
        except self._openai.AuthenticationError:
            return {"provider": "openai", "reachable": True, "model": self.model, "model_available": False,
                    "error": "API key not accepted"}
        except self._openai.NotFoundError:
            return {"provider": "openai", "reachable": True, "model": self.model, "model_available": False,
                    "error": "model not found"}
        except self._openai.OpenAIError:
            return {"provider": "openai", "reachable": False, "model": self.model, "model_available": False}
        return {"provider": "openai", "reachable": True, "model": self.model, "model_available": True}


def writes_whole_resume(llm) -> bool:
    """Is this Claude or OpenAI (strong enough to write the whole resume in one call), not the local model?"""
    try:
        return bool(getattr(llm, "writes_whole_resume", False))
    except Exception:
        return False


class AccountLLM:
    """The AI for one workspace: Claude or OpenAI when that account saved its own key, else the local model.
    Reads the account's choice on every call, so a key saved (or removed) in Profile applies at once, including
    to work already running in the background."""

    def __init__(self, db_path, fallback: LLMClient):
        self.db_path, self.fallback = db_path, fallback
        self._cache: dict[tuple, LLMClient] = {}

    def current(self) -> LLMClient:
        from app import ai_settings
        s = ai_settings.load(self.db_path)
        if s is None or s["provider"] == "ollama" or not s.get("api_key"):
            return self.fallback
        key = (s["provider"], s["api_key"], s.get("model") or "")
        if key not in self._cache:
            self._cache.clear()
            cls = ClaudeClient if s["provider"] == "claude" else OpenAIClient
            self._cache[key] = cls(s["api_key"], s.get("model") or None)
        return self._cache[key]

    @property
    def model(self) -> str:
        return self.current().model

    @property
    def writes_whole_resume(self) -> bool:
        return bool(getattr(self.current(), "writes_whole_resume", False))

    def generate_json(self, system: str, user: str, schema: dict, **kwargs) -> dict:
        client = self.current()
        if kwargs:
            import inspect
            try:
                params = inspect.signature(client.generate_json).parameters
                if not any(p.kind is p.VAR_KEYWORD for p in params.values()):
                    kwargs = {k: v for k, v in kwargs.items() if k in params}   # only what this client accepts
            except (TypeError, ValueError):
                kwargs = {}
        return client.generate_json(system, user, schema, **kwargs)

    def status(self) -> dict:
        return self.current().status()
