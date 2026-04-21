from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


if load_dotenv:
    load_dotenv()


@dataclass
class AppConfig:
    dashscope_api_key: str | None
    bailian_model: str
    requested_mode: str
    tencent_map_server_key: str | None
    tencent_map_js_key: str | None

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY"),
            bailian_model=os.getenv("BAILIAN_MODEL", "qwen-plus"),
            requested_mode=os.getenv("TRAVEL_APP_MODE", "online").strip().lower(),
            tencent_map_server_key=os.getenv("TENCENT_MAP_SERVER_KEY"),
            tencent_map_js_key=os.getenv("TENCENT_MAP_JS_KEY"),
        )

    def resolve_mode(self) -> str:
        return "fallback" if self.requested_mode == "fallback" else "online"

    def has_tencent_server(self) -> bool:
        return bool(self.tencent_map_server_key)

    def has_tencent_js(self) -> bool:
        return bool(self.tencent_map_js_key)
