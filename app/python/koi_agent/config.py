"""Load Koi's environment configuration without third-party dependencies."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 30
    protocol: str = "responses"
    enable_thinking: bool = True


@dataclass(frozen=True)
class Settings:
    planner: Provider
    decision: Provider
    memory_path: str = "~/.koi/memory.jsonl"
    skills_path: str = "~/.koi/skills.jsonl"
    max_steps: int = 30
    max_failures: int = 3
    max_seconds: float = 300


def _load_dotenv() -> None:
    """Load .env once, without overwriting variables supplied by the process."""
    configured = os.getenv("KOI_ENV_FILE")
    candidates = [Path(configured).expanduser()] if configured else [
        Path(".env"),
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    env_file = next((path for path in candidates if path.exists()), None)
    if env_file is None:
        return
    for raw in env_file.read_text(encoding="utf8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _provider(section: str, prefix: str, defaults: tuple[str, str], data: dict) -> Provider:
    values = data.get(section, {})
    return Provider(
        name=section,
        base_url=str(values.get("base_url") or os.getenv(f"{prefix}_BASE_URL", defaults[0])),
        api_key=str(values.get("api_key") or os.getenv(f"{prefix}_API_KEY", "")),
        model=str(values.get("model") or os.getenv(f"{prefix}_MODEL", defaults[1])),
        timeout=float(values.get("timeout") or os.getenv(f"{prefix}_TIMEOUT", 30)),
    )


def _number(data: dict, key: str, env: str, default, cast):
    try:
        return cast(data.get(key, os.getenv(env, default)))
    except (TypeError, ValueError):
        return cast(default)


def load_settings(path: str | None = None) -> Settings:
    _load_dotenv()
    config_path = Path(path or os.getenv("KOI_CONFIG", "~/.koi/config.toml")).expanduser()
    data = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)

    runtime = data.get("runtime", {})
    return Settings(
        planner=_provider("planner", "KOI_PLANNER", ("https://api.openai.com/v1", "gpt-4o-mini"), data),
        decision=_provider("decision", "KOI_DECISION", ("https://api.typesafe.ai/v1", "jev-latest"), data),
        memory_path=str(data.get("memory_path", os.getenv("KOI_MEMORY_PATH", "~/.koi/memory.jsonl"))),
        skills_path=str(data.get("skills_path", os.getenv("KOI_SKILLS_PATH", "~/.koi/skills.jsonl"))),
        max_steps=_number(runtime, "max_steps", "KOI_MAX_STEPS", 30, int),
        max_failures=_number(runtime, "max_failures", "KOI_MAX_FAILURES", 3, int),
        max_seconds=_number(runtime, "max_seconds", "KOI_MAX_SECONDS", 300, float),
    )
