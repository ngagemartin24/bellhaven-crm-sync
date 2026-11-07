"""Settings from config.toml; the CRM key only from the environment or a git-ignored .env file."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

TOKEN_VAR = "BELLHAVEN_CRM_TOKEN"


@dataclass(frozen=True)
class Config:
    site_url: str
    crm_url: str
    crm_browser_url: str
    operator_parent_name: str
    db_path: Path
    token: str | None


def _token_from_env_file(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == TOKEN_VAR and value.strip():
            return value.strip().strip('"').strip("'")
    return None


def load_config(path: Path = Path("config.toml")) -> Config:
    data = tomllib.loads(path.read_text())
    token = os.environ.get(TOKEN_VAR) or _token_from_env_file(path.parent / ".env")
    return Config(
        site_url=data["site_url"].rstrip("/"),
        crm_url=data["crm_url"].rstrip("/"),
        crm_browser_url=data["crm_browser_url"],
        operator_parent_name=data["operator_parent_name"],
        db_path=path.parent / data["db_path"],
        token=token or None,
    )
