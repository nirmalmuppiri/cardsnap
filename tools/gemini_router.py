"""
Standalone rate-limit-aware Gemini model router.

One API key, requests distributed across all models that still have quota.
Tracks RPM (rolling 60-second window) and RPD (resets midnight PT) per model.
Automatically falls through to the next available model when one is saturated.

Usage:
    router = GeminiRouter()
    response, model_used = router.call(contents, task="agent", tools=tools, system="...")
"""

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

_PT = timezone(timedelta(hours=-8))  # Pacific Standard Time — RPD resets midnight PT


@dataclass(frozen=True)
class ModelConfig:
    api_id: str  # model identifier string for the Gemini API
    rpm: int     # requests per minute (0 = unavailable on this tier)
    rpd: int     # requests per day   (0 = unavailable)
    tpm: int     # tokens per minute  (0 = no published limit)
    rank: int    # lower = preferred capability tier


# Free-tier limits verified 2026-05-23 via AI Studio.
# Gemma 26B/31B: 1500 RPD each — best for sustained agent runs.
# Flash models: 20-500 RPD — good quality but burn out fast.
REGISTRY: dict[str, ModelConfig] = {
    "flash-2.0": ModelConfig(
        api_id="gemini-2.0-flash",
        rpm=15, rpd=1_500, tpm=1_000_000, rank=1,
    ),
    "flash-1.5": ModelConfig(
        api_id="gemini-1.5-flash",
        rpm=15, rpd=1_500, tpm=1_000_000, rank=2,
    ),
    "flash-2.5": ModelConfig(
        api_id="gemini-2.5-flash",
        rpm=10, rpd=500, tpm=250_000, rank=3,
    ),
}

TASK_PREFERENCES: dict[str, list[str]] = {
    "agent":   ["flash-2.0", "flash-1.5", "flash-2.5"],
    "scorer":  ["flash-2.0", "flash-1.5", "flash-2.5"],
    "default": ["flash-2.0", "flash-1.5", "flash-2.5"],
}


class GeminiRouter:
    def __init__(self) -> None:
        self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self._rpm_log: dict[str, list[float]] = {k: [] for k in REGISTRY}
        self._rpd_log: dict[str, tuple[str, int]] = {}  # key → (date_str_PT, count)
        self._tpm_backoff: dict[str, float] = {}  # key → monotonic time until TPM window resets
        self._dead: set[str] = set()  # models permanently excluded (bad API ID, etc.)

    # ── usage tracking ─────────────────────────────────────────────────────────

    def _today(self) -> str:
        return datetime.now(_PT).strftime("%Y-%m-%d")

    def _rpm_used(self, key: str) -> int:
        now = time.monotonic()
        log = self._rpm_log[key]
        while log and now - log[0] > 60:
            log.pop(0)
        return len(log)

    def _rpd_used(self, key: str) -> int:
        today = self._today()
        date, count = self._rpd_log.get(key, ("", 0))
        return count if date == today else 0

    def _record(self, key: str) -> None:
        self._rpm_log[key].append(time.monotonic())
        today = self._today()
        _, count = self._rpd_log.get(key, (today, 0))
        self._rpd_log[key] = (today, count + 1)

    # ── model selection ────────────────────────────────────────────────────────

    def available(self, key: str) -> bool:
        if key in self._dead:
            return False
        cfg = REGISTRY[key]
        if cfg.rpm == 0 or cfg.rpd == 0:
            return False
        if time.monotonic() < self._tpm_backoff.get(key, 0):
            return False
        return self._rpm_used(key) < cfg.rpm and self._rpd_used(key) < cfg.rpd

    def select(self, task: str = "default") -> tuple[str, ModelConfig]:
        """Return (key, config) for the best available model for this task."""
        for key in TASK_PREFERENCES.get(task, TASK_PREFERENCES["default"]):
            if self.available(key):
                return key, REGISTRY[key]
        raise RuntimeError(
            f"All Gemini models exhausted for task '{task}'. "
            f"RPD limits reset at midnight Pacific time.\n\n{self.status_str()}"
        )

    def _wait_rpm(self, key: str) -> None:
        """No-op — router now selects only available models, so no waiting needed."""
        pass

    # ── main interface ─────────────────────────────────────────────────────────

    def call(
        self,
        contents: list,
        task: str = "default",
        tools: list | None = None,
        system: str = "",
    ) -> tuple:
        """
        Make a Gemini API call. Automatically selects the best available model,
        throttles for RPM, and falls through to the next model if one is saturated.

        contents: list of types.Content objects or compatible dicts
        Returns (response, model_key_used)
        """
        key, cfg = self.select(task)
        self._wait_rpm(key)

        config = types.GenerateContentConfig(
            system_instruction=system or None,
            tools=tools or None,
        )

        self._record(key)
        try:
            response = self._client.models.generate_content(
                model=cfg.api_id,
                contents=contents,
                config=config,
            )
            return response, key
        except (ClientError, ServerError) as e:
            code = getattr(e, "code", None)
            if self._rpm_log[key]:
                self._rpm_log[key].pop()  # undo record — call didn't succeed
            if code == 404:
                self._dead.add(key)
                print(f"  [{key}] model not found (404) — permanently excluding {REGISTRY[key].api_id}")
                return self.call(contents, task, tools, system)
            if code == 400:
                err_str = str(e).lower()
                if any(phrase in err_str for phrase in ("context", "token", "too large", "too long", "length", "size")):
                    # Context too long — back off this model temporarily, not permanently
                    self._tpm_backoff[key] = time.monotonic() + 30
                    print(f"  [{key}] context too long (400) — backing off 30s, trying next model")
                else:
                    self._dead.add(key)
                    print(f"  [{key}] invalid argument (400) — permanently excluding {REGISTRY[key].api_id}")
                return self.call(contents, task, tools, system)
            if code in (429, 500, 503):
                match = re.search(r"retry in (\d+)", str(e), re.IGNORECASE)
                backoff = int(match.group(1)) + 5 if match else (70 if code == 429 else 30)
                self._tpm_backoff[key] = time.monotonic() + backoff
                label = "TPM limit" if code == 429 else "internal error" if code == 500 else "unavailable"
                print(f"  [{key}] {label} ({code}) — backing off {backoff}s, trying next model")
                return self.call(contents, task, tools, system)
            raise

    # ── status ─────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            key: {
                "api_id": REGISTRY[key].api_id,
                "rpm": f"{self._rpm_used(key)}/{REGISTRY[key].rpm}",
                "rpd": f"{self._rpd_used(key)}/{REGISTRY[key].rpd}",
                "available": self.available(key),
            }
            for key in REGISTRY
            if REGISTRY[key].rpm > 0
        }

    def status_str(self) -> str:
        lines = ["Model                  RPM          RPD          Status"]
        lines.append("-" * 58)
        for key, s in self.status().items():
            tag = "OK" if s["available"] else "EXHAUSTED"
            lines.append(f"  {key:<20s}  {s['rpm']:<12s} {s['rpd']:<12s} {tag}")
        return "\n".join(lines)
