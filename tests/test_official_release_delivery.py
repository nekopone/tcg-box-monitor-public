from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from freezegun import freeze_time

from tcg_monitor import cli
from tcg_monitor.config import load_config
from tcg_monitor.models import Release, SourceTier
from tcg_monitor.parsers.onepiece_official import parse_onepiece_official_products
from tcg_monitor.source_priority import merge_releases


def eb05() -> Release:
    config = load_config("sites.yaml")
    source = next(s for s in config.sources if s.id == "onepiece_official_products")
    _, releases, _ = parse_onepiece_official_products(
        Path("tests/fixtures/onepiece_official_products.html").read_text(),
        source.discovery_urls[0],
        source,
        config,
    )
    return next(r for r in releases if r.canonical_product_key == "EB-05")


@pytest.fixture
def delivery(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """外部には送らず、本番と同じCLI・状態保存・重複判定を通す。"""
    releases: list[Release] = [eb05()]
    messages: list[tuple[str, str]] = []
    calendar_calls: list[tuple[str, date]] = []

    class FakeDiscord:
        def send(self, title: str, description: str) -> dict[str, str]:
            messages.append((title, description))
            return {"status": "sent"}

    class FakeCalendar:
        def reconcile(
            self, kind, internal_id, summary, when, description, **kwargs
        ):  # type: ignore[no-untyped-def]
            assert kind == "release"
            calendar_calls.append((internal_id, when))
            return {"status": "inserted", "event_id": "test-event"}

    monkeypatch.setattr(cli, "run_pipeline", lambda *a, **kw: ([], releases, []))
    monkeypatch.setattr(cli, "DiscordAdapter", FakeDiscord)
    monkeypatch.setattr(cli, "CalendarAdapter", FakeCalendar)
    state_path = tmp_path / "state.json"
    state = cli.MonitorState.load(state_path)
    # EB-05は既に検出済みで通知だけ未送信、という実際の状態を再現。
    state.data["seen_releases"][releases[0].release_id] = releases[0].__dict__
    state.mark_baseline()
    state.arm()

    def run(mode="run"):  # type: ignore[no-untyped-def]
        with freeze_time("2026-09-09 02:00:00"):
            assert cli.main(["--state", str(state_path), mode]) == 0

    return releases, messages, calendar_calls, state_path, run


def test_known_month_is_notified_once_then_exact_date_is_delivered(delivery):  # type: ignore[no-untyped-def]
    releases, messages, calendar_calls, state_path, run = delivery
    original_id = releases[0].release_id
    run()
    run()
    assert len(messages) == 1
    assert "新弾発売月" in messages[0][0]
    assert "2026年10月（公式発表・日にち未確定）" in messages[0][1]
    assert "https://www.onepiece-cardgame.com/products/eb05.html" in messages[0][1]
    assert calendar_calls == []
    state = cli.MonitorState.load(state_path)
    assert state.delivered(f"release-month:{original_id}:2026-10")
    assert not state.delivered(f"release:{original_id}")

    # これは将来の公式更新を模したテスト値。実際の発売日ではない。
    releases[:] = [replace(releases[0], release_date=date(2026, 10, 20), release_month=None)]
    run()
    run()
    assert len(messages) == 2
    assert "発売日: 2026/10/20" in messages[1][1]
    assert calendar_calls == [(original_id, date(2026, 10, 20))] * 2
    assert cli.MonitorState.load(state_path).delivered(f"release:{original_id}")


def test_month_change_is_notified_once_without_calendar(delivery):  # type: ignore[no-untyped-def]
    releases, messages, calendar_calls, _, run = delivery
    run()
    releases[:] = [replace(releases[0], release_month="2026-11")]
    run()
    run()
    assert len(messages) == 2
    assert "2026年11月" in messages[-1][1]
    assert calendar_calls == []


@pytest.mark.parametrize("month", [None, "2026-00", "2026-13", "2026-1", "2026-08", "2027-10"])
def test_invalid_past_or_distant_month_is_not_notified(delivery, month):  # type: ignore[no-untyped-def]
    releases, messages, calendar_calls, _, run = delivery
    releases[:] = [replace(releases[0], release_month=month)]
    run()
    assert messages == []
    assert calendar_calls == []


@pytest.mark.parametrize("tier", [SourceTier.SECONDARY, SourceTier.OFFICIAL_INDIRECT])
@pytest.mark.parametrize("exact", [True, False])
def test_unconfirmed_release_is_neither_displayed_nor_delivered(
    delivery, capsys, tier, exact
):  # type: ignore[no-untyped-def]
    releases, messages, calendar_calls, state_path, run = delivery
    rumor = replace(
        releases[0],
        canonical_product_key="unconfirmed",
        product_name="公式未確認の新商品",
        source_tier=tier,
        release_date=date(2026, 10, 20) if exact else None,
    ).with_id()
    releases[:] = [rumor]
    run("dry-run")
    assert json.loads(capsys.readouterr().out)["releases"] == []
    run()
    assert messages == []
    assert calendar_calls == []
    assert rumor.release_id not in cli.MonitorState.load(state_path).data["seen_releases"]


def test_secondary_day_cannot_replace_official_month(delivery):  # type: ignore[no-untyped-def]
    releases, messages, calendar_calls, _, run = delivery
    secondary = replace(
        releases[0], source_tier=SourceTier.SECONDARY, release_date=date(2026, 10, 20)
    )
    releases[:], alerts = merge_releases([secondary, releases[0]])
    assert alerts == []
    run()
    assert len(messages) == 1
    assert "日にち未確定" in messages[0][1]
    assert calendar_calls == []


def test_failed_month_notification_retries_without_marking_delivered(
    delivery, monkeypatch
):  # type: ignore[no-untyped-def]
    releases, messages, _, state_path, run = delivery
    original_adapter = cli.DiscordAdapter

    class FailingDiscord:
        def send(self, title, description):  # type: ignore[no-untyped-def]
            raise RuntimeError("test delivery failure")

    monkeypatch.setattr(cli, "DiscordAdapter", FailingDiscord)
    with pytest.raises(RuntimeError, match="test delivery failure"):
        run()
    key = f"release-month:{releases[0].release_id}:2026-10"
    assert not cli.MonitorState.load(state_path).delivered(key)
    monkeypatch.setattr(cli, "DiscordAdapter", original_adapter)
    run()
    assert len(messages) == 1
    assert cli.MonitorState.load(state_path).delivered(key)


def test_mixed_catalog_keeps_only_booster_products() -> None:
    config = load_config("sites.yaml")
    source = next(s for s in config.sources if s.id == "onepiece_official_products")
    # 同じ一覧に周辺用品やセットが増えても、発売月対応で対象を広げない。
    products = [
        ("op17", "ブースターパック 世界最強の戦士【OP-17】"),
        ("eb05", "エクストラブースター Heroines Edition vol.2【EB-05】"),
        ("prb02", "プレミアムブースター テスト【PRB-02】"),
        ("st36", "スタートデッキ【ST-36】"),
        ("sleeve16", "オフィシャルカードスリーブ【EB-05】"),
        ("cardcase", "カードケース 1BOX【EB-05】"),
        ("collection", "プレミアムカードコレクション 1BOX"),
        ("preciousbox", "ONE PIECE HEROINES PRECIOUS BOX"),
        ("eb90", "エクストラブースター同梱 スペシャルセット【EB-90】"),
        ("eb91", "エクストラブースター同梱 プレイマット【EB-91】"),
    ]
    html = "".join(
        f'<a href="/products/{code}.html">{title} 発売日 2026.10</a>'
        for code, title in products
    )
    _, releases, alerts = parse_onepiece_official_products(
        html, source.discovery_urls[0], source, config
    )
    assert {r.canonical_product_key for r in releases} == {"OP-17", "EB-05", "PRB-02"}
    assert alerts == []
