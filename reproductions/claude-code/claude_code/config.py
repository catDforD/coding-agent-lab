"""Claude Code cleanroom 的 live API 配置加载。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """配置不完整或无效。"""


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    model: str
    base_url: str | None


def reproduction_root() -> Path:
    return Path(__file__).resolve().parent.parent


def workspace_root() -> Path:
    override = os.environ.get("CLAUDE_CODE_WORKSPACE_ROOT")
    if override:
        return Path(override).resolve()
    return Path.cwd()


def env_file_path() -> Path:
    override = os.environ.get("CLAUDE_CODE_ENV_FILE")
    if override:
        return Path(override).resolve()
    return reproduction_root() / ".env"


def load_openai_settings() -> OpenAISettings:
    file_values = _read_env_file(env_file_path())
    api_key = os.environ.get("OPENAI_API_KEY") or file_values.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL") or file_values.get("OPENAI_MODEL")
    base_url = os.environ.get("OPENAI_BASE_URL") or file_values.get("OPENAI_BASE_URL")

    if not api_key:
        raise ConfigError(
            "missing OPENAI_API_KEY; set it in the environment or reproductions/claude-code/.env"
        )
    if not model:
        raise ConfigError(
            "missing OPENAI_MODEL; set it in the environment or reproductions/claude-code/.env"
        )

    return OpenAISettings(
        api_key=api_key,
        model=model,
        base_url=base_url or None,
    )


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_quotes(value.strip())
    return values


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
