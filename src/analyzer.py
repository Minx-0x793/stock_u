"""AI 분석기 — 종목·감성 추출. (FR-11 ~ FR-14, FR-17 ~ FR-18)

1) 내장 약어 사전으로 1차 매칭 (FR-11-2)
2) Claude API 로 종목·감성·신뢰도 추출 (FR-11, FR-11-1, FR-12)
자막 품질이 낮아 불확실하면 신뢰도 "하"로 표기한다. (FR-18, NFR-07)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import anthropic

from .abbreviations import match_builtin
from .collector import VideoItem
from .config import Config
from .logger import get_logger

log = get_logger("analyzer")

# 신뢰도 표기 표준값
CONFIDENCE_HIGH = "상"
CONFIDENCE_MID = "중"
CONFIDENCE_LOW = "하"

_SYSTEM_PROMPT = """당신은 한국 주식 시장 분석 보조 도구입니다.
주어진 유튜브 영상 자막에서, 사용자가 관심 있는 종목에 대한 '언급'과 '감성'만 추출합니다.

핵심 원칙(매우 중요):
- 자막은 유튜브 자동 생성 자막이라 부정확하거나 누락이 있을 수 있습니다.
- 확실한 것만 분석하고, 애매하면 신뢰도를 "하"로 솔직히 표기하세요.
- 추측으로 사실을 단정하지 마세요. 오탐(잘못된 긍/부정)보다 보수적 판단을 우선합니다.

별칭 정규화:
- "삼전"→삼성전자, "하닉"→SK하이닉스 등 줄임말을 정식 종목명으로 정규화하세요.
- 사전에 없는 별명도 문맥으로 추론할 수 있으면 정규화하되, 그렇게 추론한 종목은 신뢰도를 "중"으로 표기하세요. (예: 문맥상 "네카오"→네이버, 카카오)

각 종목 언급에 대해 다음을 산출하세요:
- stock: 정식 종목명 (반드시 관심 종목 목록 중 하나)
- sentiment: "긍정" | "부정" | "중립"
- evidence: 판단 근거가 된 핵심 문장(자막 인용, 1~2문장)
- confidence: "상" | "중" | "하"

반드시 아래 JSON 스키마로만 응답하세요. 설명 문장 없이 JSON만 출력합니다.
{"mentions": [{"stock": "...", "sentiment": "...", "evidence": "...", "confidence": "..."}]}
관심 종목 언급이 전혀 없으면 {"mentions": []} 를 반환하세요."""


@dataclass
class Mention:
    """한 영상에서 추출된 종목 언급 1건."""

    stock: str
    sentiment: str  # 긍정 / 부정 / 중립
    evidence: str
    confidence: str  # 상 / 중 / 하
    channel_name: str = ""
    video_title: str = ""
    video_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _build_user_prompt(item: VideoItem, config: Config, prematched: dict) -> str:
    stock_lines = []
    for s in config.stocks:
        alias_str = f" (별칭: {', '.join(s.aliases)})" if s.aliases else ""
        stock_lines.append(f"- {s.name}{alias_str}")
    prematch_hint = ""
    if prematched:
        prematch_hint = (
            "\n[내장 사전 1차 매칭 힌트] 다음 종목이 자막에 등장한 것으로 보입니다: "
            + ", ".join(prematched.keys())
        )
    # 자막이 너무 길면 비용·토큰 절약을 위해 앞부분 위주로 자른다.
    transcript = item.transcript[:6000]
    return (
        f"[관심 종목 목록]\n" + "\n".join(stock_lines) + "\n"
        f"{prematch_hint}\n\n"
        f"[영상 제목]\n{item.title}\n\n"
        f"[자막]\n{transcript}"
    )


def _parse_response(text: str) -> list[dict]:
    """Claude 응답에서 JSON 을 안전하게 파싱한다."""
    text = text.strip()
    # 코드펜스 제거
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        return data.get("mentions", [])
    except json.JSONDecodeError:
        # 본문 내 첫 { ... } 블록 추출 시도
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start : end + 1]).get("mentions", [])
            except json.JSONDecodeError:
                pass
    log.warning("Claude 응답 JSON 파싱 실패 — 빈 결과로 처리")
    return []


def analyze(items: list[VideoItem], config: Config) -> list[Mention]:
    """분석 대상 영상들을 Claude 로 분석해 Mention 목록을 반환한다.

    개별 영상 분석 실패는 전체를 중단시키지 않고 건너뛴다. (NFR-04)
    """
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    stock_name_set = set(config.stock_names)
    alias_map = config.alias_map()
    all_mentions: list[Mention] = []

    targets = [it for it in items if not it.skipped]
    log.info("AI 분석 시작 — 대상 %d건", len(targets))

    for item in targets:
        # 1차: 내장 약어 사전 매칭 (FR-11-2)
        prematched = match_builtin(item.transcript, stock_name_set)
        item.matched_aliases = prematched

        try:
            resp = client.messages.create(
                model=config.claude_model,
                max_tokens=1500,
                system=_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": _build_user_prompt(item, config, prematched),
                    }
                ],
            )
            raw = resp.content[0].text if resp.content else ""
        except Exception as e:  # API 오류 — 다음 영상으로 계속 (NFR-04)
            log.error("Claude 분석 실패 (video=%s): %s", item.video_id, e)
            continue

        for m in _parse_response(raw):
            stock = (m.get("stock") or "").strip()
            # 관심 종목 외 결과는 별칭 맵으로 한 번 더 정규화 시도
            canonical = alias_map.get(stock.lower(), stock)
            if canonical not in stock_name_set:
                log.info("  관심 종목 외 결과 무시: %s", stock)
                continue

            sentiment = (m.get("sentiment") or "중립").strip()
            confidence = (m.get("confidence") or CONFIDENCE_LOW).strip()
            if confidence not in (CONFIDENCE_HIGH, CONFIDENCE_MID, CONFIDENCE_LOW):
                confidence = CONFIDENCE_LOW

            all_mentions.append(
                Mention(
                    stock=canonical,
                    sentiment=sentiment,
                    evidence=(m.get("evidence") or "").strip(),
                    confidence=confidence,
                    channel_name=item.channel_name,
                    video_title=item.title,
                    video_url=item.url,
                )
            )
            log.info(
                "  분석: %s | %s | 신뢰도 %s | %s",
                canonical,
                sentiment,
                confidence,
                item.channel_name,
            )

    log.info("AI 분석 종료 — 총 %d개 언급 추출", len(all_mentions))
    return all_mentions
