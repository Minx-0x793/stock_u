"""한국 주식 시장 공통 약어 내장 사전. (FR-11-2)

config 의 사용자 별칭과 별개로, 시장에서 널리 통용되는 줄임말을 내장하여
Claude API 호출 전 1차 매칭에 사용한다. 매칭 성공 시 Claude 호출을 생략해
API 비용과 처리 시간을 절감한다.

주의: 이 사전은 "별명 → 정식 종목명" 정규화만 담당한다. 실제 분석 대상은
config.stocks 에 등록된 종목으로 한정되므로, 여기서 정규화된 이름이
사용자 관심 종목과 일치할 때만 의미를 갖는다.
"""
from __future__ import annotations

# 별명(소문자) → 정식 종목명
COMMON_ABBREVIATIONS: dict[str, str] = {
    "삼전": "삼성전자",
    "삼성전자우": "삼성전자",
    "하닉": "SK하이닉스",
    "하이닉스": "SK하이닉스",
    "sk하닉": "SK하이닉스",
    "엘전": "LG전자",
    "엘지전자": "LG전자",
    "현차": "현대차",
    "현대자동차": "현대차",
    "기차": "기아",
    "카뱅": "카카오뱅크",
    "삼바": "삼성바이오로직스",
    "엘지엔솔": "LG에너지솔루션",
    "엘엔솔": "LG에너지솔루션",
    "포스코": "POSCO홀딩스",
    "포스코홀딩스": "POSCO홀딩스",
    "네이버": "NAVER",
    "셀트": "셀트리온",
    "삼화": "삼성화재",
    "삼생": "삼성생명",
    "현모비스": "현대모비스",
    "에코프로비엠": "에코프로비엠",
    "에코비엠": "에코프로비엠",
}


def match_builtin(text: str, stock_names: set[str]) -> dict[str, str]:
    """자막 텍스트에서 내장 약어를 1차 매칭한다.

    Args:
        text: 검사할 자막 텍스트.
        stock_names: 사용자 관심 종목 정식명 집합. 이 집합에 속하는 종목만 반환한다.

    Returns:
        {정식 종목명: 매칭된 약어} 형태의 딕셔너리.
    """
    found: dict[str, str] = {}
    lower = text.lower()
    for alias, canonical in COMMON_ABBREVIATIONS.items():
        if canonical not in stock_names:
            continue
        if alias.lower() in lower:
            found.setdefault(canonical, alias)
    return found
