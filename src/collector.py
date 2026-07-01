"""데이터 수집기 — 신규 영상 목록 + 자막 수집. (FR-07 ~ FR-10, FR-17)

채널은 @핸들(설계서 SC-00) 또는 채널ID(UCxxx)로 등록한다.
채널의 '업로드 재생목록'을 통해 신규 영상을 가져온다(search 대비 할당량 절약, NFR-02).
youtube-transcript-api 로 자막을 수집하고, 자막 품질(길이)을 1차 검증한다.
processed_ids.json 으로 이미 처리한 영상을 건너뛴다. (FR-08)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from .config import Channel, Config
from .logger import get_logger

log = get_logger("collector")

_PROCESSED_PATH = os.path.join("data", "processed_ids.json")

# 자막 요청 사이 지연(초) — 429(Too Many Requests) 예방 (NFR-02)
_TRANSCRIPT_DELAY = 1.5
# 자막 수집 결과 구분용 센티널: 요청 자체가 반복 실패(429 등)
_REQUEST_BLOCKED = object()


def _build_transcript_api(config: Config) -> YouTubeTranscriptApi:
    """설정된 프록시로 youtube-transcript-api 1.0+ 클라이언트를 만든다.

    proxy.type 이 none 이면 프록시 없이 동작한다. Webshare/Generic 프록시를
    쓰면 YouTube 의 IP 단위 자막 차단(429)을 우회할 수 있다.
    """
    ptype = config.proxy_type
    if ptype in ("", "none"):
        return YouTubeTranscriptApi()

    try:
        from youtube_transcript_api.proxies import (
            GenericProxyConfig,
            WebshareProxyConfig,
        )
    except ImportError:
        log.warning("이 버전은 프록시를 지원하지 않습니다 — 프록시 없이 진행합니다.")
        return YouTubeTranscriptApi()

    if ptype == "webshare":
        if not (config.proxy_webshare_username and config.proxy_webshare_password):
            log.warning("webshare 프록시 자격증명이 비어 있음 — 프록시 없이 진행합니다.")
            return YouTubeTranscriptApi()
        log.info("Webshare 프록시로 자막을 수집합니다.")
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=config.proxy_webshare_username,
                proxy_password=config.proxy_webshare_password,
            )
        )

    if ptype == "generic":
        if not (config.proxy_http_url or config.proxy_https_url):
            log.warning("generic 프록시 URL이 비어 있음 — 프록시 없이 진행합니다.")
            return YouTubeTranscriptApi()
        log.info("Generic 프록시로 자막을 수집합니다.")
        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(
                http_url=config.proxy_http_url or None,
                https_url=config.proxy_https_url or None,
            )
        )

    log.warning("알 수 없는 proxy.type='%s' — 프록시 없이 진행합니다.", ptype)
    return YouTubeTranscriptApi()


@dataclass
class VideoItem:
    """수집된 한 편의 영상 + 자막."""

    video_id: str
    title: str
    channel_id: str
    channel_name: str
    published_at: str
    url: str
    transcript: str = ""
    skipped: bool = False
    skip_reason: str = ""
    matched_aliases: dict = field(default_factory=dict)  # analyzer 단계에서 채움


def _load_processed_ids() -> set[str]:
    """이미 처리한 영상 ID 집합을 로드한다. (FR-08)"""
    if not os.path.exists(_PROCESSED_PATH):
        return set()
    try:
        with open(_PROCESSED_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        log.warning("processed_ids.json 읽기 실패 — 빈 집합으로 시작합니다.")
        return set()


def save_processed_ids(ids: set[str]) -> None:
    """처리 완료한 영상 ID 집합을 저장한다. (FR-08)"""
    os.makedirs(os.path.dirname(_PROCESSED_PATH), exist_ok=True)
    with open(_PROCESSED_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


def _resolve_channel(youtube, ch: Channel) -> tuple[str, str] | None:
    """@핸들/채널ID 를 (uploads_playlist_id, channel_title) 로 해석한다.

    실패 시 None 을 반환하고 로그를 남긴다.
    """
    try:
        if ch.is_handle:
            resp = (
                youtube.channels()
                .list(part="contentDetails,snippet", forHandle=ch.ref)
                .execute()
            )
        else:
            resp = (
                youtube.channels()
                .list(part="contentDetails,snippet", id=ch.ref)
                .execute()
            )
    except HttpError as e:
        log.error("채널 해석 실패 (%s): %s", ch.ref, e)
        return None

    items = resp.get("items", [])
    if not items:
        log.warning("채널을 찾을 수 없음: %s", ch.ref)
        return None

    item = items[0]
    uploads = item["contentDetails"]["relatedPlaylists"]["uploads"]
    title = item["snippet"]["title"]
    return uploads, title


def _fetch_recent_videos(
    youtube, uploads_playlist: str, since: datetime
) -> list[dict]:
    """업로드 재생목록에서 since 이후 신규 영상 메타데이터를 가져온다. (FR-07)

    업로드는 최신순으로 정렬되므로, since 이전 영상을 만나면 조기 종료한다.
    """
    try:
        resp = (
            youtube.playlistItems()
            .list(part="snippet", playlistId=uploads_playlist, maxResults=20)
            .execute()
        )
    except HttpError as e:
        log.error("재생목록 조회 실패 (%s): %s", uploads_playlist, e)
        return []

    items = []
    for it in resp.get("items", []):
        snip = it["snippet"]
        published = snip["publishedAt"]
        pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if pub_dt < since:
            break  # 최신순 — 이후는 모두 더 오래된 영상
        items.append(
            {
                "video_id": snip["resourceId"]["videoId"],
                "title": snip["title"],
                "published_at": published,
                "channel_title": snip.get("channelTitle", ""),
            }
        )
    return items


def _fetch_transcript(ytt: YouTubeTranscriptApi, video_id: str,
                      language: str = "ko", retries: int = 3):
    """영상 자막을 한국어 우선으로 수집한다. (FR-09, FR-10)

    youtube-transcript-api 1.0+ 의 인스턴스 fetch API 를 사용한다.

    Returns:
        - str : 자막 텍스트 (빈 문자열이면 '자막 없음/비활성화')
        - _REQUEST_BLOCKED : 429 등으로 요청이 반복 실패 (자막 없음과 구분)
    """
    langs = list(dict.fromkeys([language, "ko", "en"]))  # 중복 제거, 순서 유지
    for attempt in range(retries):
        try:
            fetched = ytt.fetch(video_id, languages=langs)
            return " ".join(s.text for s in fetched).strip()
        except (TranscriptsDisabled, NoTranscriptFound):
            return ""  # 진짜 자막 없음
        except Exception as e:  # 비공식 라이브러리 — 다양한 예외 방어 (NFR-04)
            msg = str(e).replace("\n", " ")
            if "429" in msg or "Too Many Requests" in msg or "blocked" in msg.lower():
                wait = 4 * (2 ** attempt)  # 4, 8, 16초 백오프
                log.warning(
                    "YouTube 요청 제한(429) — %d초 대기 후 재시도 (%d/%d) video=%s",
                    wait, attempt + 1, retries, video_id,
                )
                time.sleep(wait)
                continue
            log.warning("자막 수집 실패 (video=%s): %s", video_id, msg[:120])
            return ""
    return _REQUEST_BLOCKED


def collect(config: Config) -> list[VideoItem]:
    """전체 채널을 순회하며 신규 영상 + 자막을 수집해 반환한다.

    - processed_ids.json 으로 중복 제거 (FR-08)
    - 자막 없음 → skip (FR-10)
    - 자막 길이 < min_transcript_length → skip (FR-17)
    - max_videos_per_day 까지만 분석 대상으로 수집 (FR-03)
    """
    youtube = build("youtube", "v3", developerKey=config.youtube_api_key)
    ytt = _build_transcript_api(config)
    processed = _load_processed_ids()
    since = datetime.now(timezone.utc) - timedelta(hours=config.lookback_hours)

    collected: list[VideoItem] = []
    analyzable_count = 0

    for ch in config.channels:
        resolved = _resolve_channel(youtube, ch)
        if not resolved:
            continue
        uploads, ch.name = resolved
        log.info("채널 수집: %s (%s)", ch.name, ch.ref)

        for meta in _fetch_recent_videos(youtube, uploads, since):
            vid = meta["video_id"]
            if vid in processed:
                log.info("  건너뜀(이미 처리): %s", vid)
                continue

            item = VideoItem(
                video_id=vid,
                title=meta["title"],
                channel_id=ch.ref,
                channel_name=ch.name or meta["channel_title"],
                published_at=meta["published_at"],
                url=f"https://www.youtube.com/watch?v={vid}",
            )

            if analyzable_count >= config.max_videos_per_day:
                log.info(
                    "  일일 처리 한도(%d) 도달 — 이후 영상 보류",
                    config.max_videos_per_day,
                )
                break

            transcript = _fetch_transcript(ytt, vid, config.analysis_lang)
            if transcript is _REQUEST_BLOCKED:
                item.skipped = True
                item.skip_reason = "YouTube 접속 제한(429)"
                log.warning("  분석 제외(YouTube 429 반복): %s | %s", vid, item.title)
            elif not transcript:
                item.skipped = True
                item.skip_reason = "자막 없음"
                log.info("  분석 제외(자막 없음): %s | %s", vid, item.title)
            elif len(transcript) < config.min_transcript_length:
                item.skipped = True
                item.skip_reason = f"자막 짧음({len(transcript)}자)"
                log.info("  분석 제외(자막 짧음 %d자): %s", len(transcript), vid)
            else:
                item.transcript = transcript
                analyzable_count += 1
                log.info("  수집 완료(%d자): %s | %s", len(transcript), vid, item.title)

            collected.append(item)
            time.sleep(_TRANSCRIPT_DELAY)  # 429 예방용 지연

    log.info(
        "수집 종료 — 총 %d건 (분석대상 %d / 제외 %d) ✓",
        len(collected),
        analyzable_count,
        len(collected) - analyzable_count,
    )
    return collected
