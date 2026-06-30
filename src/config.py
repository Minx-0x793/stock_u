"""설정 로더 (config.yaml).

화면 설계서(SC-00) 기준의 평면 스키마를 따른다.
config.yaml 을 읽어 검증하고, 종목 별칭 사전을 구성한다. (FR-01 ~ FR-06, NFR-03)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


class ConfigError(Exception):
    """설정 파일 누락·필수값 누락 시 발생."""


@dataclass
class Stock:
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class Channel:
    """추적 대상 채널. handle(@xxx) 또는 채널ID(UCxxx)로 등록한다.

    name 은 수집 단계에서 YouTube API 로 해석해 채워진다.
    """

    ref: str          # 사용자가 입력한 원본 (@handle 또는 UCID)
    name: str = ""    # 런타임에 해석된 채널명

    @property
    def is_handle(self) -> bool:
        return self.ref.startswith("@")


@dataclass
class Config:
    youtube_api_key: str
    anthropic_api_key: str
    channels: list[Channel]
    stocks: list[Stock]
    max_videos_per_day: int
    analysis_lang: str
    min_transcript_length: int
    lookback_hours: int
    claude_model: str
    gmail_sender: str
    gmail_app_password: str
    gmail_to: list[str]
    vault_path: str
    raw: dict[str, Any] = field(default_factory=dict)

    def alias_map(self) -> dict[str, str]:
        """별칭(소문자) → 정식 종목명 매핑. (FR-02-1)

        aliases 에는 줄임말뿐 아니라 종목코드(예: 005930)도 포함될 수 있다.
        """
        mapping: dict[str, str] = {}
        for stock in self.stocks:
            mapping[stock.name.lower()] = stock.name
            for alias in stock.aliases:
                mapping[str(alias).lower()] = stock.name
        return mapping

    @property
    def stock_names(self) -> list[str]:
        return [s.name for s in self.stocks]


def _require(d: dict[str, Any], path: str) -> Any:
    """중첩 키(a.b)를 따라가며 필수값을 가져온다. 없으면 ConfigError."""
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur or cur[part] in (None, ""):
            raise ConfigError(f"config.yaml 필수 항목 누락: '{path}'")
        cur = cur[part]
    return cur


def load_config(path: str = "config.yaml") -> Config:
    """config.yaml 을 로드하여 Config 객체로 반환한다. (NFR-03)

    api_keys.claude / api_keys.anthropic 둘 다 허용한다(설계서는 claude 표기).
    """
    if not os.path.exists(path):
        raise ConfigError(
            f"설정 파일을 찾을 수 없습니다: {path}\n"
            "→ config.example.yaml 을 config.yaml 로 복사한 뒤 값을 채워주세요."
        )

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # 채널: 문자열(@handle / UCID) 리스트 또는 {ref/id} 딕셔너리 모두 허용
    channels: list[Channel] = []
    for c in raw.get("channels", []):
        if isinstance(c, str):
            ref = c.strip()
        elif isinstance(c, dict):
            ref = str(c.get("ref") or c.get("id") or c.get("handle") or "").strip()
        else:
            ref = ""
        if ref:
            channels.append(Channel(ref=ref))
    if not channels:
        raise ConfigError("config.yaml 에 추적할 channels 가 하나도 없습니다. (FR-01)")

    stocks = [
        Stock(name=s["name"], aliases=[str(a) for a in s.get("aliases", [])])
        for s in raw.get("stocks", [])
        if isinstance(s, dict) and s.get("name")
    ]
    if not stocks:
        raise ConfigError("config.yaml 에 관심 stocks 가 하나도 없습니다. (FR-02)")

    api_keys = raw.get("api_keys", {}) or {}
    claude_key = api_keys.get("claude") or api_keys.get("anthropic")
    if not claude_key:
        raise ConfigError("config.yaml 필수 항목 누락: 'api_keys.claude'")
    if not api_keys.get("youtube"):
        raise ConfigError("config.yaml 필수 항목 누락: 'api_keys.youtube'")

    return Config(
        youtube_api_key=api_keys["youtube"],
        anthropic_api_key=claude_key,
        channels=channels,
        stocks=stocks,
        max_videos_per_day=int(raw.get("max_videos_per_day", 30)),
        analysis_lang=raw.get("analysis_lang", "ko"),
        min_transcript_length=int(raw.get("min_transcript_length", 100)),
        lookback_hours=int(raw.get("lookback_hours", 24)),
        claude_model=raw.get("claude_model", "claude-haiku-4-5"),
        gmail_sender=_require(raw, "gmail_sender"),
        gmail_app_password=_require(raw, "gmail_app_password"),
        gmail_to=list(_require(raw, "gmail_to")),
        vault_path=raw.get("vault_path", "") or "",
        raw=raw,
    )
