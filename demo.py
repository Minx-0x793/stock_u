"""데모/테스트 스크립트 — API 키 없이 결과물을 미리 본다.

샘플 데이터로 집계 → 입장변화 감지 → 이메일(HTML) → Obsidian 노트를 생성한다.
YouTube/Claude API 호출이 없으므로 키 없이 바로 실행할 수 있다.

실행:
  python demo.py            # HTML 미리보기 + Obsidian 노트를 demo_output/ 에 생성
  python demo.py --send     # 위 + config.yaml 의 Gmail 계정으로 실제 메일 발송

생성물:
  demo_output/briefing.html        ← 데일리 브리핑 (SC-01) — 브라우저로 열어보세요
  demo_output/briefing_empty.html  ← 빈 상태 (SC-01-E)
  demo_output/error.html           ← 오류 알림 (SC-03)
  demo_output/vault/주식인사이트/<오늘>.md  ← Obsidian 노트 (SC-02)
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from datetime import datetime

from src import aggregator, notifier
from src.analyzer import Mention
from src.collector import VideoItem

OUT = "demo_output"
TODAY = f"{datetime.now():%Y-%m-%d}"


def sample_mentions() -> list[Mention]:
    """다양한 케이스(신뢰도 상/중/하, 긍/부정)를 담은 샘플 분석 결과."""
    return [
        Mention("삼성전자", "긍정", "하반기 반도체 업황 개선이 기대된다", "상",
                "주식하는토끼", "삼성전자 하반기 대전망", "https://youtu.be/aaa111"),
        Mention("삼성전자", "긍정", "외국인 순매수가 이어지고 있다", "상",
                "여의도불개미", "오늘의 시황 브리핑", "https://youtu.be/bbb222"),
        Mention("삼성전자", "부정", "단기적으로 고점 부담이 있다", "중",
                "차트의신", "삼전 지금 사도 될까", "https://youtu.be/ccc333"),
        Mention("SK하이닉스", "긍정", "HBM 수요가 폭발적이다", "상",
                "주식하는토끼", "삼성전자 하반기 대전망", "https://youtu.be/aaa111"),
        Mention("SK하이닉스", "중립", "자막이 부정확해 판단이 어려움", "하",
                "노이즈채널", "하닉 단타 가능?", "https://youtu.be/ddd444"),
        Mention("카카오뱅크", "부정", "성장성 둔화 우려가 크다", "상",
                "여의도불개미", "오늘의 시황 브리핑", "https://youtu.be/bbb222"),
    ]


def sample_items() -> list[VideoItem]:
    """분석 제외(자막 없음) 1건을 포함한 샘플 영상 목록."""
    return [
        VideoItem("aaa111", "삼성전자 하반기 대전망", "@toki", "주식하는토끼",
                  TODAY, "https://youtu.be/aaa111", transcript="x" * 800),
        VideoItem("zzz999", "자막 없는 라이브 다시보기", "@toki", "주식하는토끼",
                  TODAY, "https://youtu.be/zzz999", skipped=True, skip_reason="자막 없음"),
    ]


def write(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="주식 트래커 데모")
    parser.add_argument("--send", action="store_true",
                        help="config.yaml 의 Gmail 계정으로 실제 메일도 발송")
    parser.add_argument("--no-open", action="store_true",
                        help="생성 후 브라우저 자동 열기 비활성화")
    args = parser.parse_args()

    mentions = sample_mentions()
    items = sample_items()

    # 입장 변화(FR-13·14)를 보여주기 위해 과거 이력을 미리 심는다.
    # '주식하는토끼'가 삼성전자에 대해 이전엔 '부정'이었다고 가정 → 오늘 '긍정'으로 변화.
    history_path = os.path.join("data", "history.json")
    os.makedirs("data", exist_ok=True)
    import json
    seed = {"주식하는토끼::삼성전자": {"sentiment": "부정", "date": "2026-06-01", "video_url": "https://youtu.be/old"}}
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)

    # 집계 + 입장 변화
    result = aggregator.aggregate(mentions, TODAY)

    # 1) 데일리 브리핑 HTML (SC-01)
    _, html, _ = notifier.build_briefing(result, mentions, items, TODAY)
    p1 = write(os.path.join(OUT, "briefing.html"), html)

    # 2) 빈 상태 (SC-01-E)
    _, empty_html, _ = notifier.build_briefing(result, [], [], TODAY)
    p2 = write(os.path.join(OUT, "briefing_empty.html"), empty_html)

    # 3) 오류 알림 (SC-03)
    _, err_html, _ = notifier.build_error_alert("Claude API 분석", "Rate limit exceeded")
    p3 = write(os.path.join(OUT, "error.html"), err_html)

    # 4) Obsidian 노트 (SC-02) — demo_output/vault 에 저장
    demo_vault = os.path.join(OUT, "vault")
    md = notifier._render_markdown(result, mentions, items, TODAY)
    p4 = write(os.path.join(demo_vault, "주식인사이트", f"{TODAY}.md"), md)

    print("\n✅ 데모 결과물 생성 완료:")
    for p in (p1, p2, p3, p4):
        print("   -", os.path.abspath(p))

    print("\n📊 집계 결과(콘솔):")
    for s in result.summaries:
        print("   ", s.format_line())
    for c in result.stance_changes:
        print(f"    ⚠ 입장변화: [{c.channel_name}] {c.stock} {c.previous}→{c.current}")

    # 실제 Gmail 발송 (옵션)
    if args.send:
        from src.config import load_config
        cfg = load_config("config.yaml")
        notifier.send_briefing(cfg, result, mentions, items, TODAY)
        print("\n📧 실제 Gmail 발송 완료 →", ", ".join(cfg.gmail_to))

    # 브라우저로 미리보기 열기
    if not args.no_open:
        webbrowser.open("file://" + os.path.abspath(p1))

    print("\n👉 briefing.html 을 브라우저로 열면 실제 이메일 모양을 볼 수 있습니다.")


if __name__ == "__main__":
    main()
