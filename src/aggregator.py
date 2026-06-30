"""여론 집계기 — 종목별 감성 집계 + 입장 변화 감지. (FR-13 ~ FR-16, FR-19)

- 종목별 긍정/중립/부정 비율 집계 (FR-15, FR-16)
- 신뢰도 "하" 항목은 집계에서 제외 (FR-19, NFR-07)
- history.json 의 과거 입장과 비교해 입장 변화 감지 (FR-13, FR-14)
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .analyzer import CONFIDENCE_LOW, Mention
from .logger import get_logger

log = get_logger("aggregator")

_HISTORY_PATH = os.path.join("data", "history.json")


@dataclass
class StockSummary:
    """한 종목의 당일 여론 집계."""

    stock: str
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    low_confidence_count: int = 0  # 집계 제외된 신뢰도 '하' 건수 (별도 표기용)

    @property
    def total(self) -> int:
        return self.positive + self.neutral + self.negative

    @property
    def positive_ratio(self) -> int:
        return round(self.positive / self.total * 100) if self.total else 0

    def format_line(self) -> str:
        """'삼성전자: 긍정 3 / 중립 1 / 부정 1 (60% 긍정)' 형식. (FR-16)"""
        line = (
            f"{self.stock}: 긍정 {self.positive} / 중립 {self.neutral} / "
            f"부정 {self.negative} ({self.positive_ratio}% 긍정)"
        )
        if self.low_confidence_count:
            line += f"  · 참고용(신뢰도 하) {self.low_confidence_count}건 별도"
        return line


@dataclass
class StanceChange:
    """유튜버의 종목 입장 변화 1건. (FR-13, FR-14)"""

    channel_name: str
    stock: str
    previous: str
    current: str
    evidence: str
    video_url: str


@dataclass
class AggregateResult:
    summaries: list[StockSummary] = field(default_factory=list)
    stance_changes: list[StanceChange] = field(default_factory=list)


def _load_history() -> dict:
    if not os.path.exists(_HISTORY_PATH):
        return {}
    try:
        with open(_HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        log.warning("history.json 읽기 실패 — 빈 이력으로 시작")
        return {}


def _save_history(history: dict) -> None:
    os.makedirs(os.path.dirname(_HISTORY_PATH), exist_ok=True)
    with open(_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _history_key(channel_name: str, stock: str) -> str:
    return f"{channel_name}::{stock}"


def aggregate(mentions: list[Mention], today: str | None = None) -> AggregateResult:
    """언급 목록을 집계하고 입장 변화를 감지한다.

    신뢰도 '하' 항목은 비율 집계에서 제외하되 건수만 별도 표기한다. (FR-19)
    입장 변화 비교 및 history 갱신에도 신뢰도 '하'는 사용하지 않는다.
    """
    today = today or f"{datetime.now():%Y-%m-%d}"
    history = _load_history()

    summaries: dict[str, StockSummary] = {}
    # 같은 (채널, 종목) 의 당일 대표 입장 — 마지막(최신) 신뢰 가능 언급 사용
    current_stance: dict[str, Mention] = {}

    for m in mentions:
        summ = summaries.setdefault(m.stock, StockSummary(stock=m.stock))

        if m.confidence == CONFIDENCE_LOW:
            summ.low_confidence_count += 1
            continue  # 비율 집계·입장 비교에서 제외 (FR-19)

        if m.sentiment == "긍정":
            summ.positive += 1
        elif m.sentiment == "부정":
            summ.negative += 1
        else:
            summ.neutral += 1

        current_stance[_history_key(m.channel_name, m.stock)] = m

    # 입장 변화 감지 (FR-13, FR-14)
    stance_changes: list[StanceChange] = []
    for key, m in current_stance.items():
        prev = history.get(key)
        if prev and prev.get("sentiment") and prev["sentiment"] != m.sentiment:
            # 중립↔중립이 아닌 의미 있는 변화만 경고 (긍↔부 등)
            stance_changes.append(
                StanceChange(
                    channel_name=m.channel_name,
                    stock=m.stock,
                    previous=prev["sentiment"],
                    current=m.sentiment,
                    evidence=m.evidence,
                    video_url=m.video_url,
                )
            )
            log.info(
                "입장 변화 감지: %s / %s : %s → %s",
                m.channel_name,
                m.stock,
                prev["sentiment"],
                m.sentiment,
            )
        # history 갱신 (신뢰 가능한 최신 입장만 기록)
        history[key] = {
            "sentiment": m.sentiment,
            "date": today,
            "video_url": m.video_url,
        }

    _save_history(history)

    result = AggregateResult(
        summaries=sorted(summaries.values(), key=lambda s: s.total, reverse=True),
        stance_changes=stance_changes,
    )
    log.info(
        "집계 종료 — 종목 %d개 / 입장변화 %d건",
        len(result.summaries),
        len(result.stance_changes),
    )
    return result
