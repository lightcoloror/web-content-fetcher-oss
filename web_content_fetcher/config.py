from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "schema_version": 1,
    "web_ui": {},
    "web_access": {},
    "wechat_download_api": {},
}


@dataclass(frozen=True)
class WebContentFetcherConfig:
    path: str
    data: dict[str, Any]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config_path() -> Path:
    source_checkout_config = project_root() / "config" / "web_content_fetcher.json"
    if source_checkout_config.exists():
        return source_checkout_config
    try:
        return Path(resources.files("web_content_fetcher").joinpath("default_config.json"))
    except (ModuleNotFoundError, TypeError):
        return source_checkout_config


def load_config(path: str = "") -> WebContentFetcherConfig:
    config_path = Path(path or os.environ.get("WEB_CONTENT_FETCHER_CONFIG", "") or default_config_path())
    data = json.loads(json.dumps(DEFAULT_CONFIG))
    if not config_path.exists():
        raise FileNotFoundError(f"web-content-fetcher config not found: {config_path}")
    loaded = json.loads(config_path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        _deep_update(data, loaded)
    return WebContentFetcherConfig(path=str(config_path), data=data)


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def web_ui_host(config: WebContentFetcherConfig | None = None) -> str:
    cfg = config or load_config()
    return str(_required((cfg.data.get("web_ui") or {}).get("host"), "web_ui.host"))


def web_ui_port(config: WebContentFetcherConfig | None = None) -> int:
    cfg = config or load_config()
    return int(_required((cfg.data.get("web_ui") or {}).get("port"), "web_ui.port"))


def web_access_base_url(config: WebContentFetcherConfig | None = None) -> str:
    cfg = config or load_config()
    return str(_required((cfg.data.get("web_access") or {}).get("base_url"), "web_access.base_url"))


def wechat_download_api_base_url(config: WebContentFetcherConfig | None = None) -> str:
    cfg = config or load_config()
    configured = (cfg.data.get("wechat_download_api") or {}).get("base_url")
    return str(configured or "http://127.0.0.1:5000")


def _required(value: Any, key: str) -> Any:
    if value in {None, ""}:
        raise KeyError(f"missing required web-content-fetcher config value: {key}")
    return value
