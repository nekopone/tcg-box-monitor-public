from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from tcg_monitor.config import validate_config
from tcg_monitor.discord import DiscordAdapter
from tcg_monitor.expedition_mode import (
    DEFAULT_EXPEDITION_MODE_PATH,
    ExpeditionModeError,
    load_expedition_modes,
)
from tcg_monitor.game_modes import (
    DEFAULT_GAME_MODES_PATH,
    LEGACY_ENABLED_GAME_IDS,
    GameModeError,
    load_enabled_game_ids,
)
from tcg_monitor.google_calendar import RELEASE_EVENT_COLOR_ID, CalendarAdapter
from tcg_monitor.identity import release_dedupe_key
from tcg_monitor.logging_config import configure_logging, log_event
from tcg_monitor.models import (
    Alert,
    Config,
    LotteryCase,
    OpportunityKind,
    Release,
    SourceTier,
    stable_url_identity,
)
from tcg_monitor.parsers.local_lottery import preserve_first_detection_start
from tcg_monitor.pipeline import run_pipeline
from tcg_monitor.source_groups import active_source_filter
from tcg_monitor.state import MonitorState


def _cleanup_records(
    runtime: Mapping[str, object],
    name: str,
) -> dict[str, dict[str, str]]:
    """Read confirmed cleanup identities from the runtime configuration."""

    raw = runtime.get(name, {})
    if not isinstance(raw, dict):
        raise ValueError(f"runtime {name} must be a mapping")
    records: dict[str, dict[str, str]] = {}
    for internal_id, expected in raw.items():
        if not isinstance(internal_id, str) or not isinstance(expected, dict):
            raise ValueError(f"runtime {name} contains an invalid record")
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in expected.items()
        ):
            raise ValueError(f"runtime {name} contains an invalid field")
        records[internal_id] = dict(expected)
    return records


def _emit(cases: list[LotteryCase], releases: list[Release], alerts: list[Alert]) -> None:
    print(
        json.dumps(
            {
                "lotteries": [case.__dict__ for case in cases],
                "releases": [release.__dict__ for release in releases],
                "alerts": [alert.__dict__ for alert in alerts],
            },
            ensure_ascii=False,
            default=str,
            indent=2,
        )
    )


def _release_description(release: Release, detected_at: datetime) -> str:
    # Detection time changes on every catalog crawl and therefore made an
    # otherwise identical all-day event look dirty forever. Keep release event
    # payloads stable; the state file already records each synchronization time.
    del detected_at
    return "\n".join(
        [
            f"公式商品ページ: {release.official_url}",
            f"商品分類: {release.product_category}",
            f"抽出方法: {release.extraction_method}",
            f"抽出確度: {release.confidence}",
            f"内部ID: {release.release_id}",
        ]
    )


def _remember_release(state: MonitorState, release: Release) -> None:
    state.data["seen_releases"][release.release_id] = release.__dict__


def _remember_case(state: MonitorState, case: LotteryCase) -> None:
    state.data["seen_cases"][case.case_id] = case.__dict__


def _previous_enabled_game_ids(state: MonitorState) -> frozenset[str]:
    raw = state.data.get("enabled_game_ids", sorted(LEGACY_ENABLED_GAME_IDS))
    if not isinstance(raw, list):
        return LEGACY_ENABLED_GAME_IDS
    return frozenset(str(value) for value in raw)


def _baseline_newly_enabled_lotteries(
    state: MonitorState,
    cases: list[LotteryCase],
    newly_enabled_game_ids: frozenset[str],
) -> set[str]:
    """Remember currently visible lotteries without sending a backlog burst."""

    baseline_case_ids: set[str] = set()
    journal = state.data.setdefault("delivery_journal", {})
    timestamp = datetime.now(UTC).isoformat()
    for case in cases:
        if case.game_id not in newly_enabled_game_ids:
            continue
        _remember_case(state, case)
        journal[f"lottery:started:{case.case_id}"] = {
            "status": "complete",
            "updated_at": timestamp,
        }
        baseline_case_ids.add(case.case_id)
    if baseline_case_ids:
        state.save()
    return baseline_case_ids


def _store_enabled_game_ids(state: MonitorState, game_ids: frozenset[str]) -> None:
    state.data["enabled_game_ids"] = sorted(game_ids)
    state.save()


def _cleanup_confirmed_false_positive_cases(
    state: MonitorState,
    calendar: CalendarAdapter,
    confirmed_cases: Mapping[str, Mapping[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Delete only confirmed false events and their state records."""

    seen_cases = state.data.setdefault("seen_cases", {})
    calendar_sync = state.data.setdefault("calendar_sync", {})
    journal = state.data.setdefault("delivery_journal", {})
    migrations = state.data.setdefault("case_id_migrations", {})
    results: list[dict[str, str]] = []

    if confirmed_cases is None:
        runtime = validate_config().system.get("runtime", {})
        confirmed_cases = _cleanup_records(
            runtime,
            "confirmed_false_positive_cases",
        )
    for case_id, expected in confirmed_cases.items():
        sync_key = f"lottery:{case_id}"
        journal_keys = (
            f"lottery:started:{case_id}",
            f"lottery:scheduled:{case_id}",
        )
        case_record = seen_cases.get(case_id)
        sync_record = calendar_sync.get(sync_key)
        has_history = any(key in journal for key in journal_keys)
        if case_record is None and sync_record is None and not has_history:
            continue
        if not isinstance(case_record, dict) or (
            sync_record is not None and not isinstance(sync_record, dict)
        ):
            raise RuntimeError(
                f"誤検知清掃対象の監視状態が不完全です: retailer={expected['retailer_name']}"
            )

        checked_fields = (
            "retailer_id",
            "retailer_name",
            "product_name",
            "start_at",
            "source_url",
        )
        mismatched = [
            field
            for field in checked_fields
            if str(case_record.get(field) or "") != expected[field]
        ]
        event_id = (
            str(sync_record.get("event_id") or "")
            if isinstance(sync_record, dict)
            else ""
        )
        if mismatched or (sync_record is not None and event_id != expected["event_id"]):
            detail = ",".join(mismatched) if mismatched else "event_id"
            raise RuntimeError(
                "誤検知清掃対象が確認済み記録と一致しません: "
                f"retailer={expected['retailer_name']} mismatch={detail}"
            )

        result: dict[str, str] = {"status": "state_only"}
        if sync_record is not None:
            result = calendar.delete_owned_event(
                event_id,
                kind="lottery",
                internal_id=state.calendar_case_identity(case_id),
            )
            if result.get("status") not in {"deleted", "not_found"}:
                raise RuntimeError(
                    "Google Calendar誤予定の削除が完了しませんでした: "
                    f"retailer={expected['retailer_name']} result={result}"
                )

        seen_cases.pop(case_id, None)
        calendar_sync.pop(sync_key, None)
        migrations.pop(case_id, None)
        for key in journal_keys:
            journal.pop(key, None)
        results.append(
            {
                "retailer": expected["retailer_name"],
                "product": expected["product_name"],
                "status": str(result["status"]),
            }
        )

    if results:
        state.save()
    return results


def _cleanup_confirmed_false_positive_releases(
    state: MonitorState,
    calendar: CalendarAdapter,
    confirmed_releases: Mapping[str, Mapping[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Delete confirmed false or duplicate releases after strict identity checks."""

    seen_releases = state.data.setdefault("seen_releases", {})
    calendar_sync = state.data.setdefault("calendar_sync", {})
    journal = state.data.setdefault("delivery_journal", {})
    results: list[dict[str, str]] = []

    if confirmed_releases is None:
        runtime = validate_config().system.get("runtime", {})
        confirmed_releases = _cleanup_records(
            runtime,
            "confirmed_false_positive_releases",
        )
    for release_id, expected in confirmed_releases.items():
        sync_key = f"release:{release_id}"
        release_record = seen_releases.get(release_id)
        sync_record = calendar_sync.get(sync_key)
        has_history = sync_key in journal
        if release_record is None and sync_record is None and not has_history:
            continue

        expected_event_id = expected["event_id"]
        if not isinstance(release_record, dict) or (
            expected_event_id and not isinstance(sync_record, dict)
        ):
            raise RuntimeError(
                f"清掃対象発売日の監視状態が不完全です: product={expected['product_name']}"
            )
        if not expected_event_id and sync_record is not None:
            raise RuntimeError(
                f"状態のみの清掃対象に未確認の予定があります: product={expected['product_name']}"
            )

        checked_fields = (
            "game_id",
            "product_name",
            "release_date",
            "source_url",
        )
        mismatched = [
            field
            for field in checked_fields
            if str(release_record.get(field) or "") != expected[field]
        ]
        event_id = str(sync_record.get("event_id") or "") if isinstance(sync_record, dict) else ""
        if mismatched or event_id != expected_event_id:
            detail = ",".join(mismatched) if mismatched else "event_id"
            raise RuntimeError(
                "清掃対象発売日の記録が確認済み内容と一致しません: "
                f"product={expected['product_name']} mismatch={detail}"
            )

        result: dict[str, str] = {"status": "state_only"}
        if event_id:
            result = calendar.delete_owned_event(
                event_id,
                kind="release",
                internal_id=release_id,
            )
            if result.get("status") not in {"deleted", "not_found"}:
                raise RuntimeError(
                    "Google Calendar発売日の削除が完了しませんでした: "
                    f"product={expected['product_name']} result={result}"
                )

        seen_releases.pop(release_id, None)
        calendar_sync.pop(sync_key, None)
        journal.pop(sync_key, None)
        results.append(
            {
                "product": expected["product_name"],
                "status": str(result["status"]),
            }
        )

    if results:
        state.save()
    return results


def _migrate_existing_release_event_colors(
    state: MonitorState,
    calendar: CalendarAdapter,
) -> list[dict[str, str]]:
    """Apply the release color once to every monitor-owned historical event."""

    marker_key = "release_event_color_id"
    if state.data.get(marker_key) == RELEASE_EVENT_COLOR_ID:
        return []

    calendar_sync = state.data.setdefault("calendar_sync", {})
    results: list[dict[str, str]] = []
    for sync_key in sorted(calendar_sync):
        if not sync_key.startswith("release:"):
            continue
        sync_record = calendar_sync[sync_key]
        if not isinstance(sync_record, dict):
            continue
        event_id = str(sync_record.get("event_id") or "")
        if not event_id:
            continue
        release_id = sync_key.removeprefix("release:")
        result = calendar.set_owned_event_color(
            event_id,
            kind="release",
            internal_id=release_id,
            color_id=RELEASE_EVENT_COLOR_ID,
        )
        if result.get("status") not in {"updated", "not_found"}:
            raise RuntimeError(f"Google Calendar発売日色の更新が完了しませんでした: {result}")
        results.append({"status": str(result["status"])})

    state.data[marker_key] = RELEASE_EVENT_COLOR_ID
    state.save()
    return results


def _reuse_first_detection_start(state: MonitorState, case: LotteryCase) -> LotteryCase:
    previous = state.data.get("seen_cases", {}).get(case.case_id, {})
    prepared = preserve_first_detection_start(case, previous)
    first_delivery_start_offsets = {
        "snkrdunk_open_invitation_seen": 0,
        "yahoo_realtime_detected_next_day": 1,
        "furuichi_official_open_detected": 0,
        "hobby_search_active_lottery_detected": 0,
    }
    day_offset = first_delivery_start_offsets.get(case.extraction_method)
    if day_offset is None or not previous:
        return prepared

    # The first successful Discord delivery is the durable first-detection clock.
    # It repairs both mutable article dates and store records where OCR
    # previously promoted the winner-announcement date to a start date.
    delivery = state.data.get("delivery_journal", {}).get(f"lottery:started:{case.case_id}", {})
    raw_delivered_at = delivery.get("updated_at") if isinstance(delivery, dict) else None
    if not raw_delivered_at:
        return prepared
    try:
        delivered_at = datetime.fromisoformat(str(raw_delivered_at))
    except ValueError:
        return prepared
    if delivered_at.tzinfo is None:
        delivered_at = delivered_at.replace(tzinfo=UTC)
    first_detection_start = delivered_at.astimezone(ZoneInfo("Asia/Tokyo")).date() + timedelta(
        days=day_offset
    )
    prepared_date = (
        prepared.start_at.date() if isinstance(prepared.start_at, datetime) else prepared.start_at
    )
    if case.extraction_method == "yahoo_realtime_detected_next_day":
        return replace(prepared, start_at=first_detection_start)
    if first_detection_start < prepared_date:
        return replace(prepared, start_at=first_detection_start)
    return prepared


def _prepare_cases(state: MonitorState, cases: list[LotteryCase]) -> tuple[list[LotteryCase], int]:
    prepared: list[LotteryCase] = []
    new_count = 0
    for case in cases:
        already_known = case.case_id in state.data.get("seen_cases", {})
        migrated_from = state.migrate_case_identity(case)
        if not already_known and migrated_from is None:
            new_count += 1
        prepared.append(_reuse_first_detection_start(state, case))
    return prepared, new_count


def _prepare_releases(state: MonitorState, releases: list[Release]) -> tuple[list[Release], int]:
    """Reuse one persistent ID for the same physical BOX across source URLs."""

    prepared: list[Release] = []
    new_count = 0
    for release in releases:
        # 発売情報はメーカー公式で確認できたものだけを採用する。
        # 店舗・まとめサイトだけの日付は、表示・通知・予定登録へ流さない。
        # 抽選情報の補完経路はこの処理とは独立している。
        if release.source_tier != SourceTier.OFFICIAL:
            log_event(
                phase="release_filter",
                outcome="skipped",
                reason_code="official_release_required",
                product=release.canonical_product_key,
            )
            continue
        canonical_id = state.canonical_release_identity(release)
        already_known = canonical_id in state.data.get("seen_releases", {})
        if not already_known:
            new_count += 1
        prepared.append(
            release
            if canonical_id == release.release_id
            else replace(release, release_id=canonical_id)
        )
    return prepared, new_count


def _calendar_payload_hash(
    summary: str,
    when: date | datetime,
    description: str,
    dedupe_key: str | None = None,
    color_id: str | None = None,
) -> str:
    stable_description = "\n".join(
        line for line in description.splitlines() if not line.startswith("検出日時:")
    )
    payload: dict[str, object] = {
        "summary": summary,
        "when": str(when),
        "description": stable_description,
        "dedupe_key": dedupe_key,
    }
    # Noneは従来と同じJSON形を保ち、抽選予定を不要に再同期しない。
    if color_id is not None:
        payload["color_id"] = color_id
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(serialized.encode()).hexdigest()


def _summary_markdown(state: MonitorState) -> str:
    summary = state.data.get("last_run_summary", {})
    enabled_games = summary.get("enabled_game_ids", [])
    enabled_game_label = (
        ", ".join(str(value) for value in enabled_games)
        if isinstance(enabled_games, list) and enabled_games
        else "なし"
    )
    duration_ms = summary.get("duration_ms", 0)
    try:
        duration_seconds = max(0, int(duration_ms)) / 1_000
    except (TypeError, ValueError):
        duration_seconds = 0
    rows = (
        ("成功監視", summary.get("successful_monitors", 0)),
        ("代替経路で継続", summary.get("degraded_monitors", 0)),
        ("失敗監視", summary.get("failed_monitors", 0)),
        ("異常", summary.get("alerts", 0)),
        ("新規抽選", summary.get("new_lotteries", 0)),
        ("新規発売", summary.get("new_releases", 0)),
    )
    lines = [
        "## TCG BOX monitor 実行結果",
        "",
        f"🎴 監視中の作品: **{enabled_game_label}**",
        "",
        f"処理時間: {duration_seconds:.1f}秒",
        "",
        "| 指標 | 件数 |",
        "|---|---:|",
        *(f"| {label} | {value} |" for label, value in rows),
    ]
    monitors = state.data.get("monitors", {})
    if isinstance(monitors, dict) and monitors:
        lines.extend(
            [
                "",
                (
                    "| 監視先 | 結果 | HTTP | 取得方法 | 原因 | 代替経路 | "
                    "時間(ms) | ページ | 解析 | 除外 | 連続失敗 |"
                ),
                "|---|---|---:|---|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for source_id, raw in sorted(monitors.items()):
            record = raw if isinstance(raw, dict) else {}
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(source_id),
                        str(record.get("outcome") or "-"),
                        str(record.get("http_status") or "-"),
                        str(record.get("fetch_method") or "-"),
                        str(record.get("failure_cause") or "-"),
                        ", ".join(str(value) for value in record.get("healthy_fallbacks", []))
                        if (
                            isinstance(record.get("healthy_fallbacks"), list)
                            and record.get("healthy_fallbacks")
                        )
                        else "-",
                        str(record.get("duration_ms") or 0),
                        str(record.get("fetched_pages") or 0),
                        str(record.get("parsed_count") or 0),
                        str(record.get("excluded_count") or 0),
                        str(record.get("consecutive_failures") or 0),
                    )
                )
                + " |"
            )
    if isinstance(monitors, dict):
        unproven = [
            source_id for source_id, record in monitors.items()
            if isinstance(record, dict) and record.get("evidence_since")
            and not record.get("last_candidate_at")
        ]
        lines.extend([
            "", "### 検知実証（取得成功とは別）", "",
            "候補実績は本機能導入以降。0件は故障とも抽選なしとも断定できません。",
            f"候補生成の実証なし: {len(unproven)}経路", "",
            *[f"- {source_id}" for source_id in sorted(unproven)],
        ])
    return "\n".join(lines) + "\n"


def _write_github_summary(state: MonitorState) -> None:
    markdown = _summary_markdown(state)
    print(markdown, end="")
    destination = os.getenv("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as handle:
            handle.write(markdown)


_AMAZON_INVITATION_METHODS = {
    "snkrdunk_open_invitation_seen",
    "yahoo_realtime_amazon_invitation_seen",
}


def _is_amazon_invitation(case: LotteryCase) -> bool:
    return case.retailer_id == "amazon_jp" and case.extraction_method in _AMAZON_INVITATION_METHODS


def _lottery_application_label(case: LotteryCase) -> str:
    if _is_amazon_invitation(case):
        return "Amazon招待リクエストページ"
    if case.retailer_id == "furuichi":
        return "公式応募案内ページ"
    if case.opportunity_kind != OpportunityKind.LOTTERY:
        return "公式購入ページ"
    if (
        case.retailer_id == "edion_online"
        and case.extraction_method == "nyuka_now_priority_retailer_application_start"
    ):
        return "応募予定ページ（受付開始前は未公開の場合あり）"
    return "公式応募ページ"


def _lottery_description(case: LotteryCase, detected_at: datetime) -> str:
    opportunity_label = (
        "Amazon招待"
        if _is_amazon_invitation(case)
        else ("公式販売" if case.opportunity_kind != OpportunityKind.LOTTERY else "抽選")
    )
    return "\n".join(
        [
            f"種別: {opportunity_label}",
            f"店舗・サービス: {case.retailer_name}",
            f"{_lottery_application_label(case)}: {case.official_url}",
            f"確認元ページ: {case.source_url}",
            f"商品分類: {case.product_category}",
            f"検出日時: {detected_at.isoformat()}",
            f"抽出方法: {case.extraction_method}",
            f"抽出確度: {case.confidence}",
            f"内部ID: {case.case_id}",
        ]
    )


def _alert_description(alert: Alert, detected_at: datetime) -> str:
    return "\n".join(
        [
            f"監視元: {alert.source_id}",
            f"理由: {alert.reason_code}",
            f"内容: {alert.change_summary}",
            f"確認URL: {alert.manual_check_url}",
            f"検出日時: {detected_at.isoformat()}",
        ]
    )


def _saved_datetime(value: object, timezone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone)


_TRANSPORT_ALERT_REASONS = {
    "http_fetch_failed",
    "repeated_http_error",
    "browser_fallback_failed",
    "page_fetch_failed",
    "host_circuit_open",
}
_TRANSPORT_REMINDER_HOURS = 24 * 7
_TRANSPORT_NOTIFY_AFTER_CONSECUTIVE_RUNS = 2
_ALERT_RESOLVE_AFTER_MISSING_RUNS = 2
_MAX_ALERTS_PER_DIGEST = 10


def _compact(value: str, limit: int) -> str:
    folded = " ".join(value.split())
    return folded if len(folded) <= limit else folded[: limit - 1] + "…"


def _format_user_datetime(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d %H:%M")
    return value.strftime("%Y/%m/%d")


def _lottery_date_in_delivery_window(
    case_date: date,
    today: date,
    last_day: date,
    late_grace_days: int = 1,
) -> bool:
    """Allow a newly recovered lottery to notify for one day after its start."""

    first_day = today - timedelta(days=max(0, late_grace_days))
    return first_day <= case_date <= last_day


def _lottery_discord_description(case: LotteryCase) -> str:
    if case.opportunity_kind == OpportunityKind.DIRECT_SALE_SEEN:
        date_label = "販売を確認した日（開始日時不明）"
    elif case.opportunity_kind == OpportunityKind.DIRECT_SALE:
        date_label = "販売開始"
    elif _is_amazon_invitation(case):
        date_label = "招待受付の確認日（開始日時不明）"
    else:
        date_label = "受付開始"
    application_label = _lottery_application_label(case)
    lines = [
        f"店舗: {case.retailer_name}",
        f"商品: {case.product_name}",
        f"{date_label}: {_format_user_datetime(case.start_at)}",
        f"{application_label}: {case.official_url or case.source_url}",
    ]
    if case.source_tier == SourceTier.SECONDARY:
        if case.source_url and case.source_url != case.official_url:
            lines.append(f"確認元ページ: {case.source_url}")
        lines.append("情報元: 二次情報（応募前に公式ページで確認）")
    return "\n".join(lines)


def _opportunity_title_prefix(case: LotteryCase, config: Config) -> str:
    if _is_amazon_invitation(case):
        return f"【{config.games[case.game_id].short_name}Amazon招待】"
    if case.opportunity_kind == OpportunityKind.LOTTERY:
        return config.games[case.game_id].lottery_start_prefix
    return f"【{config.games[case.game_id].short_name}公式販売】"


def _opportunity_uses_calendar(case: LotteryCase) -> bool:
    return case.opportunity_kind != OpportunityKind.DIRECT_SALE_SEEN and not _is_amazon_invitation(
        case
    )


def _opportunity_is_still_open(case: LotteryCase, today: date) -> bool:
    """Let a newly added official source report an already-open application."""

    if case.end_at is None:
        return False
    end_date = case.end_at.date() if isinstance(case.end_at, datetime) else case.end_at
    start_date = case.start_at.date() if isinstance(case.start_at, datetime) else case.start_at
    return start_date <= today <= end_date


def _release_discord_description(
    release: Release,
    *,
    date_changed: bool,
) -> str:
    release_value = "発売日: 未確定"
    if release.release_date:
        release_value = f"発売日: {_format_user_datetime(release.release_date)}"
    elif release.release_month:
        year, month = release.release_month.split("-")
        release_value = f"発売月: {year}年{int(month)}月（公式発表・日にち未確定）"
    lines = [
        f"商品: {release.product_name}",
        release_value,
        f"公式ページ: {release.official_url or release.source_url}",
    ]
    if release.release_month and not release.release_date:
        lines.append("発売日が公式発表されたら、改めて通知してカレンダーに登録します。")
    if date_changed:
        lines.append("更新: 発売日が変更されました")
    if release.source_tier == SourceTier.SECONDARY:
        lines.append("情報元: 二次情報（公式ページで最終確認）")
    return "\n".join(lines)


def _alert_reason_label(reason_code: str) -> str:
    if reason_code in _TRANSPORT_ALERT_REASONS:
        return "通信・ページ取得の障害"
    if "ocr" in reason_code:
        return "画像の文字読み取り失敗"
    if "parser" in reason_code:
        return "ページ解析の失敗"
    if "missing" in reason_code or "structure" in reason_code:
        return "ページ構造の変化を検知"
    if "state" in reason_code:
        return "監視状態の保存失敗"
    return "監視内容の要確認"


def _alert_digest_description(alerts: list[Alert], detected_at: datetime) -> str:
    lines = [
        f"検出日時: {_format_user_datetime(detected_at)}",
        "同じ実行で発生した異常をまとめています。",
    ]
    for index, alert in enumerate(alerts[:_MAX_ALERTS_PER_DIGEST], 1):
        lines.extend(
            [
                "",
                f"{index}. {_compact(alert.title, 90)}",
                f"状況: {_alert_reason_label(alert.reason_code)}",
                f"内容: {_compact(alert.change_summary, 140)}",
                f"確認: {_compact(alert.manual_check_url, 180)}",
            ]
        )
    remaining = len(alerts) - _MAX_ALERTS_PER_DIGEST
    if remaining > 0:
        lines.extend(["", f"ほか {remaining}件（次回も個別に状態追跡します）"])
    return "\n".join(lines)


def _deliver_alerts(
    state: MonitorState,
    discord: DiscordAdapter,
    alerts: list[Alert],
    detected_at: datetime,
    reminder_hours: int,
    evaluated_source_ids: set[str] | None = None,
) -> None:
    """Track failures individually but send at most one digest per monitor run."""
    records = state.data.setdefault("alerts", {})
    active: set[str] = set()
    pending_notifications: list[tuple[str, Alert]] = []
    timezone = ZoneInfo(str(detected_at.tzinfo or "Asia/Tokyo"))
    existing_incidents = {
        (
            str(record.get("source_id") or ""),
            str(record.get("reason_code") or ""),
            stable_url_identity(str(record.get("manual_check_url") or "")),
        ): str(fingerprint)
        for fingerprint, record in records.items()
        if isinstance(record, dict) and record.get("status") == "active"
    }
    unique: dict[str, Alert] = {}
    for alert in alerts:
        incident = (
            alert.source_id,
            alert.reason_code,
            stable_url_identity(alert.manual_check_url or alert.target_url),
        )
        fingerprint = existing_incidents.get(
            incident,
            alert.fingerprint or alert.with_fingerprint().fingerprint,
        )
        existing_incidents[incident] = fingerprint
        unique[fingerprint] = alert

    for fingerprint, alert in unique.items():
        active.add(fingerprint)
        previous = records.get(fingerprint, {})
        consecutive_runs = (
            int(previous.get("consecutive_runs", 0)) + 1
            if previous.get("status") == "active"
            else 1
        )
        last_notified = _saved_datetime(previous.get("last_notified_at"), timezone)
        effective_hours = max(reminder_hours, 1)
        if alert.reason_code in _TRANSPORT_ALERT_REASONS:
            effective_hours = max(effective_hours, _TRANSPORT_REMINDER_HOURS)
        reminder = timedelta(hours=effective_hours)
        reminder_due = (
            reminder_hours > 0
            and last_notified is not None
            and detected_at - last_notified >= reminder
        )
        incident_is_mature = (
            alert.reason_code not in _TRANSPORT_ALERT_REASONS
            or consecutive_runs >= _TRANSPORT_NOTIFY_AFTER_CONSECUTIVE_RUNS
        )
        should_notify = incident_is_mature and (
            previous.get("status") != "active" or not last_notified or reminder_due
        )
        if should_notify:
            pending_notifications.append((fingerprint, alert))
        records[fingerprint] = {
            "status": "active",
            "first_seen_at": previous.get("first_seen_at") or detected_at.isoformat(),
            "last_seen_at": detected_at.isoformat(),
            "last_notified_at": previous.get("last_notified_at"),
            "reason_code": alert.reason_code,
            "source_id": alert.source_id,
            "title": alert.title,
            "manual_check_url": alert.manual_check_url,
            "consecutive_runs": consecutive_runs,
            "missing_runs": 0,
        }

    for fingerprint, record in records.items():
        if record.get("status") != "active" or fingerprint in active:
            continue
        source_id = str(record.get("source_id") or "")
        if evaluated_source_ids is not None and source_id not in evaluated_source_ids:
            continue
        missing_runs = int(record.get("missing_runs", 0)) + 1
        record["missing_runs"] = missing_runs
        if missing_runs < _ALERT_RESOLVE_AFTER_MISSING_RUNS:
            continue
        record["status"] = "resolved"
        record["resolved_at"] = detected_at.isoformat()

    if pending_notifications:
        digest_alerts = [alert for _, alert in pending_notifications]
        discord.send(
            f"【監視異常まとめ】{len(digest_alerts)}件",
            _alert_digest_description(digest_alerts, detected_at),
        )
        for fingerprint, _ in pending_notifications:
            records[fingerprint]["last_notified_at"] = detected_at.isoformat()
    # Alert timestamps may be supplied by a replay or deterministic test run.
    # Use the same clock for retention instead of pruning against wall time.
    state.save(detected_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="sites.yaml")
    parser.add_argument("--state", default="monitor_state.json")
    parser.add_argument("--fixture-dir")
    parser.add_argument(
        "--game-switch",
        default=DEFAULT_GAME_MODES_PATH,
        help="作品別監視のON/OFFファイル",
    )
    parser.add_argument(
        "--expedition-switch",
        default=DEFAULT_EXPEDITION_MODE_PATH,
        help="遠征監視のON/OFFファイル",
    )
    parser.add_argument("--source", action="append")
    parser.add_argument("--game", action="append")
    parser.add_argument("--source-tier", action="append")
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("validate-config")
    baseline = commands.add_parser("baseline")
    baseline.add_argument("--include-future-releases", action="store_true")
    commands.add_parser("dry-run")
    commands.add_parser("run")
    commands.add_parser("arm")
    commands.add_parser("status")
    commands.add_parser("summary")
    args = parser.parse_args(argv)
    configure_logging()
    config = validate_config(args.config)
    try:
        switch_enabled_game_ids = load_enabled_game_ids(
            set(config.games),
            args.game_switch,
        )
    except GameModeError as exc:
        print(f"監視モード設定エラー: {exc}", file=sys.stderr)
        return 2
    runtime = config.system.get("runtime", {})
    try:
        expedition_modes = load_expedition_modes(
            args.expedition_switch,
        )
    except ExpeditionModeError as exc:
        print(f"遠征モード設定エラー: {exc}", file=sys.stderr)
        return 2
    confirmed_false_positive_cases = _cleanup_records(
        runtime,
        "confirmed_false_positive_cases",
    )
    confirmed_false_positive_releases = _cleanup_records(
        runtime,
        "confirmed_false_positive_releases",
    )
    requested_game_ids = set(args.game or [])
    unknown_game_ids = requested_game_ids - set(config.games)
    if unknown_game_ids:
        print(
            "作品指定エラー: 未知の作品ID: " + ", ".join(sorted(unknown_game_ids)),
            file=sys.stderr,
        )
        return 2
    configured_enabled_game_ids = frozenset(switch_enabled_game_ids)
    active_game_ids = configured_enabled_game_ids
    if requested_game_ids:
        active_game_ids &= requested_game_ids
    config = replace(config, enabled_game_ids=frozenset(active_game_ids))
    enabled_game_names = [
        config.games[game_id].short_name
        for game_id in config.games
        if game_id in config.active_game_ids
    ]
    state = MonitorState.load(args.state)
    if args.cmd == "validate-config":
        print(
            f"OK schema_version={config.schema_version} "
            f"sources={len(config.sources)} "
            f"enabled_games={','.join(sorted(config.active_game_ids)) or 'none'}"
        )
        return 0
    if args.cmd == "status":
        print(json.dumps(state.data, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "summary":
        _write_github_summary(state)
        return 0
    if args.cmd == "arm":
        state.arm()
        print(f"armed enabled_games={','.join(enabled_game_names) or 'none'}")
        return 0

    requested_source_ids = set(args.source or []) or None
    may_commit_game_mode = requested_source_ids is None and not requested_game_ids
    newly_enabled_game_ids = (
        configured_enabled_game_ids - _previous_enabled_game_ids(state)
        if may_commit_game_mode
        else frozenset()
    )
    source_filter = active_source_filter(
        config.sources,
        requested_source_ids,
        enabled_expedition_groups=expedition_modes.enabled_groups,
    )
    ocr_cache = state.data.setdefault("ocr_cache", {})
    if args.cmd == "baseline":
        cases, releases, alerts = run_pipeline(
            config,
            args.fixture_dir,
            source_filter,
            ocr_cache=ocr_cache,
            monitor_state=state,
        )
        cases, new_case_count = _prepare_cases(state, cases)
        releases, new_release_count = _prepare_releases(state, releases)
        state.record_run_summary(
            {
                **state.data.get("last_run_summary", {}),
                "alerts": len(alerts),
                "new_lotteries": new_case_count,
                "new_releases": new_release_count,
                "enabled_game_ids": sorted(config.active_game_ids),
            }
        )
        for case in cases:
            _remember_case(state, case)
        if may_commit_game_mode:
            state.data["enabled_game_ids"] = sorted(configured_enabled_game_ids)
        detected_at = datetime.now(ZoneInfo(config.timezone))
        today = detected_at.date()
        last_day = today + timedelta(days=int(config.system.get("max_future_days", 365)))
        calendar_results: list[dict[str, str]] = []
        calendar = CalendarAdapter() if args.include_future_releases else None
        for release in releases:
            _remember_release(state, release)
            if not calendar or not release.release_date:
                continue
            if release.source_tier != SourceTier.OFFICIAL:
                continue
            if not today <= release.release_date <= last_day:
                continue
            key = f"release:{release.release_id}"
            summary = config.games[release.game_id].release_calendar_prefix + release.product_name
            description = _release_description(release, detected_at)
            dedupe_key = release_dedupe_key(release)
            payload_hash = _calendar_payload_hash(
                summary,
                release.release_date,
                description,
                dedupe_key,
                color_id=RELEASE_EVENT_COLOR_ID,
            )
            result: dict[str, str] = {"status": "unchanged"}
            if not state.delivered(key) or state.calendar_payload_changed(key, payload_hash):
                result = calendar.upsert(
                    "release",
                    release.release_id,
                    summary,
                    release.release_date,
                    description,
                    dedupe_key=dedupe_key,
                )
                if result.get("status") not in {"inserted", "updated"}:
                    raise RuntimeError(f"Google Calendar登録が完了しませんでした: {result}")
                state.mark_calendar_synced(key, payload_hash, result)
                state.mark_delivered(key)
            calendar_results.append(
                {"product": release.product_name, "date": str(release.release_date), **result}
            )
        state.mark_baseline()
        _emit(cases, releases, alerts)
        if args.include_future_releases:
            print(
                json.dumps(
                    {"calendar_imports": calendar_results},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0

    cases, releases, alerts = run_pipeline(
        config,
        args.fixture_dir,
        source_filter,
        ocr_cache=ocr_cache,
        monitor_state=state,
    )
    cases, new_case_count = _prepare_cases(state, cases)
    releases, new_release_count = _prepare_releases(state, releases)
    state.record_run_summary(
        {
            **state.data.get("last_run_summary", {}),
            "alerts": len(alerts),
            "new_lotteries": new_case_count,
            "new_releases": new_release_count,
            "enabled_game_ids": sorted(config.active_game_ids),
        }
    )
    if args.cmd == "dry-run":
        _emit(cases, releases, alerts)
        return 0
    if args.cmd == "run":
        if not state.data.get("armed"):
            log_event(
                run_id=datetime.now().isoformat(),
                phase="delivery",
                outcome="error",
                reason_code="not_armed",
            )
            return 2
        calendar = CalendarAdapter()
        cleanup_results = [
            *_cleanup_confirmed_false_positive_cases(
                state,
                calendar,
                confirmed_false_positive_cases,
            ),
            *_cleanup_confirmed_false_positive_releases(
                state,
                calendar,
                confirmed_false_positive_releases,
            ),
        ]
        if cleanup_results:
            print(
                json.dumps(
                    {"calendar_cleanup": cleanup_results},
                    ensure_ascii=False,
                )
            )
        color_migration_results = _migrate_existing_release_event_colors(
            state,
            calendar,
        )
        if color_migration_results:
            print(
                json.dumps(
                    {
                        "calendar_color_migration": {
                            "release_events": len(color_migration_results),
                        }
                    },
                    ensure_ascii=False,
                )
            )
        discord = DiscordAdapter()
        baseline_case_ids = _baseline_newly_enabled_lotteries(
            state,
            cases,
            newly_enabled_game_ids,
        )
        detected_at = datetime.now(ZoneInfo(config.timezone))
        today = detected_at.date()
        last_day = today + timedelta(days=int(config.system.get("max_future_days", 365)))
        late_grace_days = int(config.system.get("lottery_late_detection_grace_days", 1))
        for case in cases:
            if case.case_id in baseline_case_ids:
                continue
            previous_case = state.data["seen_cases"].get(case.case_id, {})
            _remember_case(state, case)
            case_date = (
                case.start_at.date() if isinstance(case.start_at, datetime) else case.start_at
            )
            key = f"lottery:started:{case.case_id}"
            already_delivered = state.delivered(key)
            sync_key = f"lottery:{case.case_id}"
            calendar_already_exists = sync_key in state.data.get("calendar_sync", {})
            in_delivery_window = _lottery_date_in_delivery_window(
                case_date,
                today,
                last_day,
                late_grace_days,
            )
            if (
                not in_delivery_window
                and not _opportunity_is_still_open(case, today)
                and not (already_delivered or calendar_already_exists)
            ):
                continue
            calendar_fields = (
                "game_id",
                "retailer_name",
                "product_name",
                "product_category",
                "start_at",
                "end_at",
                "opportunity_kind",
                "official_url",
                "source_url",
            )
            metadata_changed = bool(previous_case) and any(
                str(previous_case.get(field, "")) != str(getattr(case, field))
                for field in calendar_fields
            )
            title_prefix = _opportunity_title_prefix(case, config)
            calendar_summary = title_prefix + case.retailer_name + "／" + case.product_name
            calendar_description = _lottery_description(case, detected_at)
            payload_hash = _calendar_payload_hash(
                calendar_summary,
                case.start_at,
                calendar_description,
            )
            calendar_result: dict[str, str] = {"status": "unchanged"}
            uses_calendar = _opportunity_uses_calendar(case)
            if uses_calendar and (
                not already_delivered
                or metadata_changed
                or state.calendar_payload_changed(sync_key, payload_hash)
            ):
                calendar_result = calendar.upsert(
                    "lottery",
                    state.calendar_case_identity(case.case_id),
                    calendar_summary,
                    case.start_at,
                    calendar_description,
                )
                if calendar_result.get("status") not in {"inserted", "updated"}:
                    raise RuntimeError(
                        f"Google Calendar登録が完了しませんでした: {calendar_result}"
                    )
                state.mark_calendar_synced(sync_key, payload_hash, calendar_result)
            if not already_delivered:
                discord.send(
                    title_prefix + case.retailer_name + "／" + case.product_name,
                    _lottery_discord_description(case),
                )
                state.mark_delivered(key)
        release_calendar_results: list[dict[str, str]] = []
        for release in releases:
            previous = state.data["seen_releases"].get(release.release_id, {})
            previous_date = str(previous.get("release_date") or "")
            current_date = str(release.release_date or "")
            date_changed = bool(previous_date and current_date and previous_date != current_date)
            _remember_release(state, release)
            if not release.release_date:
                # 月しか決まっていない商品も予告する。1日や月末を仮の
                # 発売日にすると誤った予定になるため、カレンダーは触らない。
                month = release.release_month or ""
                if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", month):
                    continue
                if not today.strftime("%Y-%m") <= month <= last_day.strftime("%Y-%m"):
                    continue
                # 確定日通知と別の送信記録にする。既知化済みのEB-05も
                # 救済でき、毎回の重複と後日の確定日通知漏れを防げる。
                month_key = f"release-month:{release.release_id}:{month}"
                if not state.delivered(month_key):
                    discord.send(
                        f"【{config.games[release.game_id].short_name}新弾発売月】"
                        + release.product_name,
                        _release_discord_description(release, date_changed=False),
                    )
                    state.mark_delivered(month_key)
                    log_event(
                        phase="release_delivery",
                        outcome="sent",
                        reason_code="official_release_month_announced",
                        product=release.canonical_product_key,
                        release_month=month,
                    )
                continue
            if not today <= release.release_date <= last_day:
                continue
            key = f"release:{release.release_id}"
            calendar_summary = (
                config.games[release.game_id].release_calendar_prefix + release.product_name
            )
            calendar_description = _release_description(release, detected_at)
            dedupe_key = release_dedupe_key(release)
            payload_hash = _calendar_payload_hash(
                calendar_summary,
                release.release_date,
                calendar_description,
                dedupe_key,
                color_id=RELEASE_EVENT_COLOR_ID,
            )
            raw_sync_record = state.data.setdefault("calendar_sync", {}).get(key, {})
            sync_record = raw_sync_record if isinstance(raw_sync_record, dict) else {}
            known_event_id = str(sync_record.get("event_id") or "") or None
            # Reconcile every currently relevant exact release. A successful
            # state record is not proof that the event still exists: it may
            # have been deleted manually or the target calendar may have
            # changed since the previous run.
            release_calendar_result = calendar.reconcile(
                "release",
                release.release_id,
                calendar_summary,
                release.release_date,
                calendar_description,
                dedupe_key=dedupe_key,
                known_event_id=known_event_id,
            )
            if release_calendar_result.get("status") not in {
                "inserted",
                "updated",
                "unchanged",
            }:
                raise RuntimeError(
                    f"Google Calendar照合が完了しませんでした: {release_calendar_result}"
                )
            if (
                release_calendar_result.get("status") != "unchanged"
                or state.calendar_payload_changed(key, payload_hash)
                or release_calendar_result.get("event_id") != known_event_id
            ):
                state.mark_calendar_synced(
                    key,
                    payload_hash,
                    release_calendar_result,
                )
            release_calendar_results.append(
                {
                    "product": release.product_name,
                    "date": str(release.release_date),
                    **release_calendar_result,
                }
            )
            if not state.delivered(key) or date_changed:
                notification_title = (
                    config.games[release.game_id].release_notification_prefix + release.product_name
                )
                discord.send(
                    notification_title,
                    _release_discord_description(
                        release,
                        date_changed=date_changed,
                    ),
                )
                state.mark_delivered(key)
        if release_calendar_results:
            print(
                json.dumps(
                    {"calendar_release_reconciliation": release_calendar_results},
                    ensure_ascii=False,
                )
            )
        _deliver_alerts(
            state,
            discord,
            alerts,
            detected_at,
            int(config.system.get("unresolved_alert_reminder_hours", 0)),
            None if requested_source_ids is None else source_filter,
        )
        if may_commit_game_mode:
            _store_enabled_game_ids(state, configured_enabled_game_ids)
        state.save()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
