"""주식 유튜버 인사이트 트래커 — 파이프라인 진입점.

실행: python main.py [--config config.yaml]

흐름 (SRS 6 / 화면 설계서 SC-RUN):
  config → collector → analyzer → aggregator → notifier(Gmail + Obsidian)

오류 발생 시 전체 중단 없이 가능한 범위까지 진행하고(NFR-04),
치명적 오류는 실패 단계와 함께 Gmail 오류 알림을 발송한다. (SC-03, FR-23)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime

from src import aggregator, analyzer, collector, notifier
from src.collector import save_processed_ids
from src.config import ConfigError, load_config
from src.logger import get_logger

log = get_logger("main")


def _load_processed() -> set[str]:
    p = os.path.join("data", "processed_ids.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def run(config_path: str) -> int:
    date_str = f"{datetime.now():%Y-%m-%d}"
    log.info("===== 주식 인사이트 트래커 시작 (%s) =====", date_str)

    # 설정 로드 (실패 시 이메일 발송 불가하므로 즉시 종료)
    try:
        config = load_config(config_path)
        log.info("config 로드 완료 ✓")
    except ConfigError as e:
        log.error("설정 오류: %s", e)
        return 1

    stage = "초기화"
    try:
        # 1) 수집 (FR-07 ~ FR-10, FR-17)
        stage = "영상·자막 수집"
        items = collector.collect(config)

        # 2) AI 분석 (FR-11 ~ FR-14)
        stage = "Claude API 분석"
        mentions = analyzer.analyze(items, config)

        # 3) 집계 + 입장 변화 (FR-13 ~ FR-16, FR-19)
        stage = "여론 집계·입장변화"
        result = aggregator.aggregate(mentions, date_str)

        # 4) 발신 — Obsidian 먼저, 그다음 Gmail (FR-20 ~ FR-26)
        #    분석 0건이어도 send_briefing 이 빈 상태(SC-01-E) 메일을 발송한다.
        stage = "Obsidian 저장"
        notifier.save_obsidian(config, result, mentions, items, date_str)
        stage = "Gmail 발송"
        notifier.send_briefing(config, result, mentions, items, date_str)

        # 5) 처리 완료 영상 ID 기록 (FR-08)
        #    분석을 끝까지 마친 영상만 기록 → 중간 실패 영상은 다음 실행에 재시도
        processed = _load_processed()
        processed.update(it.video_id for it in items)
        save_processed_ids(processed)

        log.info("===== 정상 종료 (총 %d건 처리) ✓ =====", len(items))
        return 0

    except Exception as e:  # 치명적 오류 → 오류 알림 (SC-03, FR-23, NFR-04)
        tb = traceback.format_exc()
        log.error("치명적 오류 발생 [단계: %s]:\n%s", stage, tb)
        notifier.send_error_alert(config, stage, f"{e}\n\n{tb}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="주식 유튜버 인사이트 트래커")
    parser.add_argument(
        "--config", default="config.yaml", help="설정 파일 경로 (기본: config.yaml)"
    )
    args = parser.parse_args()
    sys.exit(run(args.config))


if __name__ == "__main__":
    main()
