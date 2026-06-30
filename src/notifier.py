"""발신기 — Gmail 데일리 브리핑 + Obsidian md 아카이브. (FR-20 ~ FR-26)

화면 설계서 SC-01 / SC-01-E / SC-02 / SC-03 의 출력 형태를 따른다.
- Gmail: HTML 이메일 (빨간 헤더 · 감성 막대 · 신뢰도 뱃지)
- Obsidian: 제목 + 전/후 날짜 링크 · 여론 표 · 종목 백링크 · 해시태그
이메일 본문 구성: ① 종목별 여론 집계 ② 입장 변화 경고 ③ 오늘의 주요 영상 요약 (FR-22)
"""
from __future__ import annotations

import os
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape

from .aggregator import AggregateResult, StockSummary
from .analyzer import CONFIDENCE_LOW, CONFIDENCE_MID, Mention
from .collector import VideoItem
from .config import Config
from .logger import get_logger

log = get_logger("notifier")

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587

# 브랜드 색상 — 유튜브 공식 레드 (유튜브 기반 프로젝트)
_BRAND_RED = "#FF0000"
# 감성별 색상 (한국 증시 관습: 긍정=빨강 / 부정=파랑 / 중립=회색)
_SENTIMENT_COLOR = {"긍정": _BRAND_RED, "부정": "#1C5FE8", "중립": "#888888"}

# 폰트 — Noto Sans Korean
_FONT = "'Noto Sans KR', sans-serif"
_FONT_IMPORT = (
    "<style>@import url('https://fonts.googleapis.com/css2?"
    "family=Noto+Sans+KR:wght@400;500;700;800&display=swap');</style>"
)


# ---------------------------------------------------------------------------
# 집계 보조
# ---------------------------------------------------------------------------
def _dominant(summary: StockSummary) -> tuple[str, int, int]:
    """대표 감성, 해당 건수, 비율(%)을 반환한다."""
    counts = {"긍정": summary.positive, "중립": summary.neutral, "부정": summary.negative}
    label = max(counts, key=counts.get)
    cnt = counts[label]
    ratio = round(cnt / summary.total * 100) if summary.total else 0
    return label, cnt, ratio


# ---------------------------------------------------------------------------
# 공통 HTML 조각
# ---------------------------------------------------------------------------
def _html_header(date_str: str) -> str:
    return (
        f'<div style="background:{_BRAND_RED};color:#fff;padding:18px 22px;'
        f'border-radius:10px 10px 0 0;font-family:{_FONT}">'
        '<div style="font-size:20px;font-weight:800">📊 주식 인사이트 데일리 브리핑</div>'
        f'<div style="font-size:13px;opacity:.9;margin-top:4px">[주식 인사이트] {date_str} 데일리 브리핑</div>'
        "</div>"
    )


def _badge(confidence: str) -> str:
    """신뢰도 뱃지. 상=일반 / 중=(추정) / 하=회색 참고용."""
    if confidence == CONFIDENCE_LOW:
        return ('<span style="background:#eee;color:#888;border-radius:4px;'
                'padding:1px 7px;font-size:11px">참고용·하</span>')
    if confidence == CONFIDENCE_MID:
        return ('<span style="background:#fff4d6;color:#a07700;border-radius:4px;'
                'padding:1px 7px;font-size:11px">신뢰도 중(추정)</span>')
    return ('<span style="background:#ffe3e0;color:#c0271a;border-radius:4px;'
            'padding:1px 7px;font-size:11px">신뢰도 상</span>')


def _aggregate_block(result: AggregateResult) -> str:
    """① 종목별 여론 집계 — 감성 막대 포함. (FR-15, FR-16)"""
    rows = []
    for s in result.summaries:
        label, cnt, ratio = _dominant(s)
        color = _SENTIMENT_COLOR.get(label, "#888")
        extra = (f' · <span style="color:#999">참고용 {s.low_confidence_count}건 별도</span>'
                 if s.low_confidence_count else "")
        rows.append(
            '<div style="margin:8px 0">'
            f'<div style="font-size:14px"><b>{escape(s.stock)}</b> '
            f'<span style="color:{color};font-weight:700">{label}</span> '
            f'<span style="color:#666">{cnt}/{s.total} {ratio}%</span>{extra}</div>'
            '<div style="background:#eee;border-radius:6px;height:10px;margin-top:4px">'
            f'<div style="background:{color};width:{ratio}%;height:10px;border-radius:6px"></div>'
            "</div></div>"
        )
    body = "".join(rows) or '<div style="color:#999">집계된 종목 언급이 없습니다.</div>'
    return _section("① 종목별 여론 집계", body)


def _stance_block(result: AggregateResult) -> str:
    """② 입장 변화 경고 — 빨간 박스. (FR-13, FR-14)"""
    if not result.stance_changes:
        return _section("② 입장 변화 경고", '<div style="color:#999">감지된 입장 변화가 없습니다.</div>')
    cards = []
    for c in result.stance_changes:
        ev = f'<div style="color:#666;font-size:12px;margin-top:3px">근거: "{escape(c.evidence)}"</div>' if c.evidence else ""
        cards.append(
            f'<div style="border:1px solid {_BRAND_RED};background:#fff5f4;'
            'border-radius:8px;padding:10px 12px;margin:6px 0">'
            f'<div style="font-size:14px"><b>[{escape(c.channel_name)}]</b> {escape(c.stock)}</div>'
            f'<div style="margin-top:3px">이전 <b>{c.previous}</b> → 현재 '
            f'<b style="color:{_BRAND_RED}">{c.current}</b></div>'
            f'{ev}<div style="margin-top:4px"><a href="{escape(c.video_url)}" '
            f'style="color:{_BRAND_RED};text-decoration:none">▶ 영상 보기</a></div></div>'
        )
    return _section("② 입장 변화 경고 ⚠️", "".join(cards))


def _detail_block(mentions: list[Mention]) -> str:
    """③ 오늘의 주요 영상 요약. (FR-12, FR-22)"""
    if not mentions:
        return _section("③ 오늘의 주요 영상 요약", '<div style="color:#999">분석된 언급이 없습니다.</div>')
    # 신뢰도 '하'는 회색 처리해 아래로
    ordered = sorted(mentions, key=lambda m: m.confidence == CONFIDENCE_LOW)
    rows = []
    for m in ordered:
        color = _SENTIMENT_COLOR.get(m.sentiment, "#888")
        dim = "color:#aaa" if m.confidence == CONFIDENCE_LOW else ""
        ev = f'<div style="font-size:12px;color:#666;{dim}">"{escape(m.evidence)}"</div>' if m.evidence else ""
        rows.append(
            f'<div style="margin:9px 0;{dim}">'
            f'<div style="font-size:14px"><b>[{escape(m.channel_name)}]</b> {escape(m.video_title)}</div>'
            f'<div style="margin:2px 0"><b style="color:{color}">{escape(m.stock)} {m.sentiment}</b> '
            f'&nbsp;{_badge(m.confidence)} '
            f'&nbsp;<a href="{escape(m.video_url)}" style="color:{_BRAND_RED};text-decoration:none">▶ 보기</a></div>'
            f"{ev}</div>"
        )
    return _section("③ 오늘의 주요 영상 요약", "".join(rows))


def _section(title: str, inner: str) -> str:
    return (
        f'<div style="padding:14px 22px;border-bottom:1px solid #eee">'
        f'<div style="font-size:15px;font-weight:800;margin-bottom:6px">{title}</div>'
        f"{inner}</div>"
    )


def _wrap(*blocks: str, footer: str = "") -> str:
    inner = "".join(blocks)
    return (
        f"{_FONT_IMPORT}"
        '<div style="max-width:560px;margin:0 auto;border:1px solid #eee;border-radius:10px;'
        f'overflow:hidden;font-family:{_FONT};color:#222">'
        f'{inner}<div style="padding:12px 22px;background:#fafafa;color:#999;font-size:11px">{footer}</div></div>'
    )


# ---------------------------------------------------------------------------
# 플레인 텍스트 폴백
# ---------------------------------------------------------------------------
def _plain_body(result: AggregateResult, mentions: list[Mention], items: list[VideoItem], date_str: str) -> str:
    out = [f"[주식 인사이트] {date_str} 데일리 브리핑", "=" * 40, "", "① 종목별 여론 집계"]
    out += [f" - {s.format_line()}" for s in result.summaries] or [" (없음)"]
    out += ["", "② 입장 변화 경고"]
    if result.stance_changes:
        out += [f" ⚠ [{c.channel_name}] {c.stock}: {c.previous} → {c.current}" for c in result.stance_changes]
    else:
        out += [" (없음)"]
    out += ["", "③ 오늘의 주요 영상 요약"]
    out += [f" - [{m.stock}/{m.sentiment}/신뢰도 {m.confidence}] {m.channel_name} — {m.video_title}\n   {m.video_url}" for m in mentions] or [" (없음)"]
    skipped = [it for it in items if it.skipped]
    if skipped:
        out += ["", f"※ 분석 제외 {len(skipped)}건 (자막 없음/짧음)"]
    out += ["", "본 브리핑은 공개 유튜브 자막 자동 분석 참고 자료이며, 투자 권유가 아닙니다."]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Gmail 발송
# ---------------------------------------------------------------------------
def _send(config: Config, subject: str, html: str, plain: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("주식 인사이트 트래커", config.gmail_sender))
    msg["To"] = ", ".join(config.gmail_to)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
        server.starttls()
        server.login(config.gmail_sender, config.gmail_app_password)
        server.sendmail(config.gmail_sender, config.gmail_to, msg.as_string())


def send_briefing(
    config: Config,
    result: AggregateResult,
    mentions: list[Mention],
    items: list[VideoItem],
    date_str: str | None = None,
) -> None:
    """데일리 브리핑 이메일을 발송한다. (SC-01, FR-20 ~ FR-22)

    분석 대상이 0건이면 빈 상태(SC-01-E) 안내 메일을 발송한다. (FR-10·22)
    """
    date_str = date_str or f"{datetime.now():%Y-%m-%d}"
    subject, html, plain = build_briefing(result, mentions, items, date_str)
    _send(config, subject, html, plain)
    log.info("Gmail 브리핑 발송 완료 ✓ → %s", ", ".join(config.gmail_to))


def build_briefing(
    result: AggregateResult,
    mentions: list[Mention],
    items: list[VideoItem],
    date_str: str | None = None,
) -> tuple[str, str, str]:
    """브리핑 이메일의 (제목, HTML, 플레인텍스트)를 만들어 반환한다.

    발송과 미리보기(demo)에서 공용으로 쓰며, 분석 0건이면 빈 상태(SC-01-E)를 만든다.
    """
    date_str = date_str or f"{datetime.now():%Y-%m-%d}"
    subject = f"[주식 인사이트] {date_str} 데일리 브리핑"

    has_analyzable = any(not it.skipped for it in items)
    if not has_analyzable and not mentions:
        html = _wrap(
            _html_header(date_str),
            '<div style="padding:40px 22px;text-align:center">'
            '<div style="font-size:40px">📭</div>'
            '<div style="font-size:17px;font-weight:800;margin-top:10px">오늘 분석할 신규 영상이 없습니다</div>'
            '<div style="color:#888;font-size:13px;margin-top:6px">24시간 내 신규 업로드가 없거나 모두 중복/제외되었습니다.</div>'
            "</div>",
            footer="메일은 정상 발송됨 — '오늘은 조용함'을 알려드립니다.",
        )
        plain = f"[주식 인사이트] {date_str} 데일리 브리핑\n\n오늘 분석할 신규 영상이 없습니다.\n24시간 내 신규 업로드가 없거나 모두 중복/제외되었습니다."
        return subject, html, plain

    skipped = [it for it in items if it.skipped]
    footer = "본 브리핑은 공개 유튜브 자막 자동 분석 참고 자료이며, 투자 권유가 아닙니다."
    if skipped:
        footer = f"분석 제외 {len(skipped)}건 (자막 없음/짧음) · " + footer

    html = _wrap(
        _html_header(date_str),
        _aggregate_block(result),
        _stance_block(result),
        _detail_block(mentions),
        footer=footer,
    )
    plain = _plain_body(result, mentions, items, date_str)
    return subject, html, plain


def build_error_alert(stage: str, error_text: str) -> tuple[str, str, str]:
    """오류 알림 이메일의 (제목, HTML, 플레인텍스트)를 만든다. (SC-03, FR-23)"""
    now = f"{datetime.now():%Y-%m-%d %H:%M}"
    today = f"{datetime.now():%Y-%m-%d}"
    subject = f"[주식 트래커] ⚠️ 실행 오류 알림 ({now})"
    html = _wrap(
        f'<div style="background:#c0185a;color:#fff;padding:16px 22px;border-radius:10px 10px 0 0;'
        f'font-weight:800;font-size:17px;font-family:{_FONT}">⚠️ [주식 트래커] 실행 오류 알림</div>',
        f'<div style="padding:16px 22px;font-family:{_FONT}">'
        f'<div style="margin:4px 0"><b>실행 시각</b> &nbsp;{now}</div>'
        f'<div style="margin:4px 0"><b>실패 단계</b> &nbsp;{escape(stage)}</div>'
        f'<div style="margin:4px 0"><b>오류 내용</b> &nbsp;<code>{escape(error_text.splitlines()[0] if error_text else "")}</code></div>'
        '<div style="color:#888;font-size:12px;margin-top:10px">※ 일부 영상은 정상 처리되었을 수 있습니다.</div>'
        f'<div style="color:#888;font-size:12px">로그: logs/{today}.log 에서 상세 원인 확인</div>'
        "</div>",
        footer="파이프라인 전체 실패 시에만 발송됩니다. (NFR-04)",
    )
    plain = (f"[주식 트래커] 실행 오류 알림\n실행 시각: {now}\n실패 단계: {stage}\n오류 내용: {error_text}\n\n"
             f"※ 일부 영상은 정상 처리되었을 수 있습니다.\n로그: logs/{today}.log")
    return subject, html, plain


def send_error_alert(config: Config, stage: str, error_text: str) -> None:
    """오류 알림 이메일을 발송한다. (SC-03, FR-23)"""
    try:
        subject, html, plain = build_error_alert(stage, error_text)
        _send(config, subject, html, plain)
        log.info("오류 알림 이메일(SC-03) 발송 완료 ✓")
    except Exception as e:
        log.error("오류 알림 이메일 발송 실패: %s", e)


# ---------------------------------------------------------------------------
# Obsidian 아카이브 (SC-02)
# ---------------------------------------------------------------------------
def _render_markdown(
    result: AggregateResult,
    mentions: list[Mention],
    items: list[VideoItem],
    date_str: str,
) -> str:
    """Obsidian 마크다운 — 전/후 날짜 링크 · 여론 표 · 종목 백링크 · 해시태그. (FR-24·25)"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    prev_d = (d - timedelta(days=1)).strftime("%Y-%m-%d")
    next_d = (d + timedelta(days=1)).strftime("%Y-%m-%d")

    md: list[str] = []
    md.append(f"# {date_str} 주식 인사이트")
    md.append(f"\n← [[{prev_d}]] | [[{next_d}]] →\n")

    # 오늘의 종목별 여론 — 표 (FR-15·16)
    md.append("## 📊 오늘의 종목별 여론")
    if result.summaries:
        md.append("\n| 종목 | 긍정 | 중립 | 부정 | 비율 |")
        md.append("|------|------|------|------|------|")
        for s in result.summaries:
            _, _, ratio = _dominant(s)
            note = f" ·참고용 {s.low_confidence_count}" if s.low_confidence_count else ""
            md.append(
                f"| [[{s.stock}]] | {s.positive} | {s.neutral} | {s.negative} | {s.positive_ratio}%{note} |"
            )
    else:
        md.append("\n- (집계된 종목 언급 없음)")

    # 입장 변화 (FR-13·14)
    md.append("\n## ⚠️ 입장 변화")
    if result.stance_changes:
        for c in result.stance_changes:
            md.append(f"- [[{c.stock}]] · [{c.channel_name}]: `{c.previous}` → `{c.current}`")
            if c.evidence:
                md.append(f"  > {c.evidence}")
            md.append(f"  - [영상 링크]({c.video_url})")
    else:
        md.append("- (입장 변화 없음)")

    # 영상별 분석 (FR-12)
    md.append("\n## 🎬 영상별 분석")
    if mentions:
        for m in mentions:
            tag = " `참고용`" if m.confidence == CONFIDENCE_LOW else ""
            md.append(f"### [{m.channel_name}] {m.video_title}")
            md.append(f"- 종목: [[{m.stock}]] / 감성: {m.sentiment} / 신뢰도: {m.confidence}{tag}")
            if m.evidence:
                md.append(f"- 근거: \"{m.evidence}\"")
            md.append(f"- [🔗 영상 링크]({m.video_url})")
    else:
        md.append("- (분석된 언급 없음)")

    # 분석 제외 (FR-10)
    skipped = [it for it in items if it.skipped]
    if skipped:
        md.append(f"\n## 분석 제외 ({len(skipped)}건)")
        for it in skipped:
            md.append(f"- {it.channel_name}: {it.title} ({it.skip_reason})")

    # 태그 (FR-25)
    stocks_today = sorted({m.stock for m in mentions})
    if stocks_today:
        md.append("\n## 🏷️ 태그")
        md.append(" ".join(f"#{s.replace(' ', '')}" for s in stocks_today))

    md.append("\n---")
    md.append("> 공개 유튜브 자막 자동 분석 참고 자료. 투자 권유 아님.")
    return "\n".join(md)


def save_obsidian(
    config: Config,
    result: AggregateResult,
    mentions: list[Mention],
    items: list[VideoItem],
    date_str: str | None = None,
) -> bool:
    """Obsidian vault 에 날짜별 md 파일을 저장한다. (SC-02, FR-24, FR-26)

    vault 경로가 없거나 잘못되면 False 를 반환하고 Gmail 만 발송하도록 경고 로그를 남긴다.
    """
    vault = config.vault_path
    date_str = date_str or f"{datetime.now():%Y-%m-%d}"

    if not vault or not os.path.isdir(vault):
        log.warning(
            "Obsidian vault 경로가 없거나 잘못됨(%s) — Gmail 만 발송합니다. (FR-26)",
            vault or "(미설정)",
        )
        return False

    target_dir = os.path.join(vault, "주식인사이트")
    try:
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, f"{date_str}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_render_markdown(result, mentions, items, date_str))
        log.info("Obsidian 아카이브 저장 완료 ✓ → %s", path)
        return True
    except OSError as e:
        log.warning("Obsidian 저장 실패(%s) — Gmail 만 발송합니다. (FR-26)", e)
        return False
