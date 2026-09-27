"""从环境变量和 TOML 加载运行配置，不引入第三方运行依赖。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import tomllib


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
    max_model_calls: int = 100
    max_tokens: int = 100000
    max_step_actions: int = 15
    max_no_ops: int = 5
    max_slow_calls: int = 5
    max_replans: int = 2


def _load_dotenv() -> None:
    """加载本地 .env，已有进程环境变量优先，不覆盖外部注入的配置。"""
    configured = os.getenv("KOI_ENV_FILE")
    candidates = (
        [Path(configured).expanduser()]
        if configured
        else [
            Path(".env"),
            Path(__file__).resolve().parents[1] / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ]
    )
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
    """按配置文件、环境变量、默认值的优先级解析模型服务。"""
    values = data.get(section, {})
    return Provider(
        name=section,
        base_url=str(values.get("base_url") or os.getenv(f"{prefix}_BASE_URL", defaults[0])),
        api_key=str(values.get("api_key") or os.getenv(f"{prefix}_API_KEY", "")),
        model=str(values.get("model") or os.getenv(f"{prefix}_MODEL", defaults[1])),
        timeout=float(values.get("timeout") or os.getenv(f"{prefix}_TIMEOUT", 30)),
    )


def _number(data: dict, key: str, env: str, default, cast):
    """解析数值配置，格式无效时使用约定默认值。"""
    try:
        return cast(data.get(key, os.getenv(env, default)))
    except (TypeError, ValueError):
        return cast(default)


def load_settings(path: str | None = None) -> Settings:
    """加载 dotenv 与 TOML，再构造不可变的运行配置。"""
    _load_dotenv()
    config_path = Path(path or os.getenv("KOI_CONFIG", "~/.koi/config.toml")).expanduser()
    data = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)

    runtime = data.get("runtime", {})
    return Settings(
        planner=_provider(
            "planner", "KOI_PLANNER", ("https://api.openai.com/v1", "gpt-4o-mini"), data
        ),
        decision=_provider(
            "decision", "KOI_DECISION", ("https://api.typesafe.ai/v1", "jev-latest"), data
        ),
        memory_path=str(
            data.get("memory_path", os.getenv("KOI_MEMORY_PATH", "~/.koi/memory.jsonl"))
        ),
        skills_path=str(
            data.get("skills_path", os.getenv("KOI_SKILLS_PATH", "~/.koi/skills.jsonl"))
        ),
        max_steps=_number(runtime, "max_steps", "KOI_MAX_STEPS", 30, int),
        max_failures=_number(runtime, "max_failures", "KOI_MAX_FAILURES", 3, int),
        max_model_calls=_number(runtime, "max_model_calls", "KOI_MAX_MODEL_CALLS", 100, int),
        max_tokens=_number(runtime, "max_tokens", "KOI_MAX_TOKENS", 100000, int),
        max_step_actions=_number(runtime, "max_step_actions", "KOI_MAX_STEP_ACTIONS", 15, int),
        max_no_ops=_number(runtime, "max_no_ops", "KOI_MAX_NO_OPS", 5, int),
        max_slow_calls=_number(runtime, "max_slow_calls", "KOI_MAX_SLOW_CALLS", 5, int),
        max_replans=_number(runtime, "max_replans", "KOI_MAX_REPLANS", 2, int),
        max_seconds=_number(runtime, "max_seconds", "KOI_MAX_SECONDS", 300, float),
    )
