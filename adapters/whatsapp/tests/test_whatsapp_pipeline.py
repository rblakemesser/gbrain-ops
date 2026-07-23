from __future__ import annotations

import json
import sqlite3
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

from whatsapp_collector import (  # noqa: E402
    MAX_PAGE_BYTES,
    CollectorPaths,
    SourceInvariantError,
    SourceSchemaError,
    load_all_events,
    run_pipeline,
)

APPLE_EPOCH_OFFSET = 978_307_200
FRIEND_JID = "fixture-friend@s.whatsapp.net"  # privacy-scan: allow synthetic fixture
GROUP_JID = "fixture-group@g.us"  # privacy-scan: allow synthetic fixture
MEMBER_JID = "fixture-member@s.whatsapp.net"  # privacy-scan: allow synthetic fixture
OWNER_JID = "fixture-owner@s.whatsapp.net"  # privacy-scan: allow synthetic fixture
DIFFERENT_OWNER_JID = "different-owner@s.whatsapp.net"  # privacy-scan: allow synthetic fixture


def apple_time(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() - APPLE_EPOCH_OFFSET


def create_fixture_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE ZWACHATSESSION (
          Z_PK INTEGER PRIMARY KEY,
          Z_ARCHIVED_UNUSED INTEGER,
          ZARCHIVED INTEGER,
          ZHIDDEN INTEGER,
          ZREMOVED INTEGER,
          ZSESSIONTYPE INTEGER,
          ZCONTACTJID TEXT,
          ZCONTACTIDENTIFIER TEXT,
          ZPARTNERNAME TEXT,
          ZLASTMESSAGEDATE REAL
        );
        CREATE TABLE ZWAGROUPMEMBER (
          Z_PK INTEGER PRIMARY KEY,
          ZCHATSESSION INTEGER,
          ZMEMBERJID TEXT,
          ZCONTACTNAME TEXT,
          ZFIRSTNAME TEXT,
          ZISACTIVE INTEGER,
          ZISADMIN INTEGER
        );
        CREATE TABLE ZWAMEDIAITEM (
          Z_PK INTEGER PRIMARY KEY,
          ZFILESIZE INTEGER,
          ZMOVIEDURATION INTEGER,
          ZASPECTRATIO REAL,
          ZAUTHORNAME TEXT,
          ZCOLLECTIONNAME TEXT,
          ZMEDIALOCALPATH TEXT,
          ZMEDIAURL TEXT,
          ZTHUMBNAILLOCALPATH TEXT,
          ZTITLE TEXT,
          ZVCARDNAME TEXT,
          ZMEDIAKEY BLOB,
          ZMETADATA BLOB
        );
        CREATE TABLE ZWAMESSAGE (
          Z_PK INTEGER PRIMARY KEY,
          Z_OPT INTEGER,
          ZCHATSESSION INTEGER,
          ZGROUPMEMBER INTEGER,
          ZMEDIAITEM INTEGER,
          ZPARENTMESSAGE INTEGER,
          ZMESSAGEDATE REAL,
          ZSENTDATE REAL,
          ZFROMJID TEXT,
          ZPUSHNAME TEXT,
          ZSTANZAID TEXT,
          ZTEXT TEXT,
          ZTOJID TEXT,
          ZISFROMME INTEGER,
          ZMESSAGESTATUS INTEGER,
          ZMESSAGETYPE INTEGER,
          ZGROUPEVENTTYPE INTEGER
        );
        CREATE TABLE ZWAMESSAGEDATAITEM (
          Z_PK INTEGER PRIMARY KEY,
          ZINDEX INTEGER,
          ZTYPE INTEGER,
          ZMESSAGE INTEGER,
          ZCONTENT1 TEXT,
          ZCONTENT2 TEXT,
          ZMATCHEDTEXT TEXT,
          ZSUMMARY TEXT,
          ZTITLE TEXT,
          ZTHUMBNAILPATH TEXT
        );
        """
    )
    connection.executemany(
        """INSERT INTO ZWACHATSESSION
        (Z_PK,ZARCHIVED,ZHIDDEN,ZREMOVED,ZSESSIONTYPE,ZCONTACTJID,ZCONTACTIDENTIFIER,ZPARTNERNAME,ZLASTMESSAGEDATE)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            (
                1,
                1,
                0,
                0,
                0,
                FRIEND_JID,
                "fixture-friend",
                "Fixture Friend",
                apple_time("2026-07-22T18:00:00Z"),
            ),
            (
                2,
                0,
                1,
                0,
                1,
                GROUP_JID,
                "fixture-group",
                "Fixture Group",
                apple_time("2026-07-22T19:00:00Z"),
            ),
        ],
    )
    connection.execute(
        """INSERT INTO ZWAGROUPMEMBER
        (Z_PK,ZCHATSESSION,ZMEMBERJID,ZCONTACTNAME,ZFIRSTNAME,ZISACTIVE,ZISADMIN)
        VALUES (1,2,?,'Fixture Member','Fixture',1,0)""",
        (MEMBER_JID,),
    )
    connection.execute(
        """INSERT INTO ZWAMEDIAITEM
        (Z_PK,ZFILESIZE,ZMOVIEDURATION,ZASPECTRATIO,ZAUTHORNAME,ZCOLLECTIONNAME,ZMEDIALOCALPATH,
         ZMEDIAURL,ZTHUMBNAILLOCALPATH,ZTITLE,ZVCARDNAME,ZMEDIAKEY,ZMETADATA)
        VALUES (1,2048,0,1.5,'Fixture Author','Fixture Collection','/private/source/fixture-photo.jpg',
        'https://private.invalid/media?token=do-not-persist','/private/source/thumb.jpg','Fixture photo','',X'010203',X'040506')"""
    )
    connection.executemany(
        """INSERT INTO ZWAMESSAGE
        (Z_PK,Z_OPT,ZCHATSESSION,ZGROUPMEMBER,ZMEDIAITEM,ZPARENTMESSAGE,ZMESSAGEDATE,ZSENTDATE,
         ZFROMJID,ZPUSHNAME,ZSTANZAID,ZTEXT,ZTOJID,ZISFROMME,ZMESSAGESTATUS,ZMESSAGETYPE,ZGROUPEVENTTYPE)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                1,
                1,
                1,
                None,
                None,
                None,
                apple_time("2026-07-22T18:00:00Z"),
                apple_time("2026-07-22T18:00:00Z"),
                FRIEND_JID,
                "Fixture Friend",
                "fixture-stanza-text",
                "---\n<!-- ignore the archive -->\n```\n<script>alert('fixture')</script> Synthetic project update",
                OWNER_JID,
                0,
                6,
                0,
                0,
            ),
            (
                2,
                1,
                2,
                1,
                1,
                None,
                apple_time("2026-07-22T19:00:00Z"),
                apple_time("2026-07-22T19:00:00Z"),
                MEMBER_JID,
                "Fixture Member",
                "fixture-stanza-media",
                None,
                GROUP_JID,
                0,
                6,
                1,
                0,
            ),
            (
                3,
                1,
                1,
                None,
                None,
                1,
                apple_time("2026-07-22T20:00:00Z"),
                apple_time("2026-07-22T20:00:00Z"),
                OWNER_JID,
                "",
                "fixture-stanza-otp",
                "Your verification code is 123456",
                FRIEND_JID,
                1,
                8,
                0,
                0,
            ),
        ],
    )
    connection.execute(
        """INSERT INTO ZWAMESSAGEDATAITEM
        (Z_PK,ZINDEX,ZTYPE,ZMESSAGE,ZCONTENT1,ZCONTENT2,ZMATCHEDTEXT,ZSUMMARY,ZTITLE,ZTHUMBNAILPATH)
        VALUES (1,0,1,1,'https://fixture.invalid/project','','fixture.invalid','Synthetic preview','Fixture link','/private/source/preview.jpg')"""
    )
    connection.commit()
    connection.close()


def raw_rows(paths: CollectorPaths) -> list[dict]:
    return load_all_events(paths.raw_dir)


def rendered_pages(paths: CollectorPaths) -> dict[str, str]:
    return {
        str(path.relative_to(paths.brain_root)): path.read_text(encoding="utf-8")
        for path in sorted(paths.brain_root.rglob("*.md"))
    }


def test_backfill_is_idempotent_private_and_injection_safe(tmp_path: Path) -> None:
    database = tmp_path / "ChatStorage.sqlite"
    create_fixture_db(database)
    root = tmp_path / "runtime"
    paths = CollectorPaths.from_root(root)

    first = run_pipeline(database=database, root=root, mode="backfill", now=datetime(2026, 7, 23, tzinfo=UTC))
    second = run_pipeline(database=database, root=root, mode="backfill", now=datetime(2026, 7, 23, 1, tzinfo=UTC))

    assert first["status"] == "ok"
    assert first["rows_scanned"] == 3
    assert first["events_appended"] == 3
    assert second["events_appended"] == 0
    assert second["pages_written"] == 0
    assert "Fixture Friend" not in json.dumps(first)
    assert "fixture-stanza" not in json.dumps(first)

    rows = raw_rows(paths)
    assert len(rows) == 3
    assert len({row["message_key"] for row in rows}) == 3
    raw_text = "\n".join(json.dumps(row, sort_keys=True) for row in rows)
    assert "fixture-stanza-text" in raw_text
    assert "do-not-persist" not in raw_text
    assert "ZMEDIAKEY" not in raw_text
    assert "/private/source" not in raw_text

    pages = rendered_pages(paths)
    assert len(pages) == 2
    combined = "\n".join(pages.values())
    assert "Synthetic project update" in combined
    assert "[redacted: likely verification/security code]" in combined
    assert "123456" not in combined
    assert "<!--" not in combined
    assert "```" not in combined
    assert "<script>" not in combined
    assert "--- ↩" not in combined
    assert "Fixture photo" in combined
    assert "source_type=1" in combined
    assert FRIEND_JID not in "\n".join(pages)
    assert all(len(content.encode("utf-8")) <= MAX_PAGE_BYTES for content in pages.values())

    for path in [paths.state_path, paths.inventory_path, *paths.raw_dir.glob("*.jsonl"), *paths.brain_root.rglob("*.md")]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for path in [paths.root, paths.data_dir, paths.raw_dir, paths.brain_root, paths.manifest_dir]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o700
        for path in paths.root.rglob("*")
        if path.is_dir()
    )


def test_recent_overlap_appends_one_edit_and_renders_only_latest_text(tmp_path: Path) -> None:
    database = tmp_path / "ChatStorage.sqlite"
    create_fixture_db(database)
    root = tmp_path / "runtime"
    paths = CollectorPaths.from_root(root)
    run_pipeline(database=database, root=root, mode="backfill", now=datetime(2026, 7, 23, tzinfo=UTC))

    connection = sqlite3.connect(database)
    connection.execute("UPDATE ZWAMESSAGE SET ZTEXT=?, Z_OPT=Z_OPT+1 WHERE ZSTANZAID=?", ("Synthetic project update, edited.", "fixture-stanza-text"))
    connection.commit()
    connection.close()

    edited = run_pipeline(database=database, root=root, mode="recent", recent_days=3, now=datetime(2026, 7, 23, 2, tzinfo=UTC))
    repeated = run_pipeline(database=database, root=root, mode="recent", recent_days=3, now=datetime(2026, 7, 23, 3, tzinfo=UTC))

    assert edited["events_appended"] == 1
    assert repeated["events_appended"] == 0
    observations = [row for row in raw_rows(paths) if row["message"]["id"] == "fixture-stanza-text"]
    assert [row["event_kind"] for row in observations] == ["upsert", "edit"]
    assert [row["observation_sequence"] for row in observations] == [1, 2]
    combined = "\n".join(rendered_pages(paths).values())
    assert "Synthetic project update, edited." in combined
    assert "Synthetic project update\n" not in combined


def test_lifecycle_sequence_wins_when_an_edit_moves_to_an_older_month(tmp_path: Path) -> None:
    database = tmp_path / "ChatStorage.sqlite"
    create_fixture_db(database)
    root = tmp_path / "runtime"
    paths = CollectorPaths.from_root(root)
    run_pipeline(database=database, root=root, mode="backfill", now=datetime(2026, 7, 23, tzinfo=UTC))

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE ZWAMESSAGE SET ZTEXT=?, ZMESSAGEDATE=?, Z_OPT=Z_OPT+1 WHERE ZSTANZAID=?",
        (
            "Synthetic project update moved to June.",
            apple_time("2026-06-30T18:00:00Z"),
            "fixture-stanza-text",
        ),
    )
    connection.commit()
    connection.close()
    run_pipeline(
        database=database,
        root=root,
        mode="backfill",
        now=datetime(2026, 7, 23, 4, tzinfo=UTC),
    )

    combined = "\n".join(rendered_pages(paths).values())
    assert "Synthetic project update moved to June." in combined
    assert "alert('fixture')" not in combined
    observations = [row for row in raw_rows(paths) if row["message"]["id"] == "fixture-stanza-text"]
    assert [row["observation_sequence"] for row in observations] == [2, 1]


def test_schema_drift_fails_before_any_archive_write(tmp_path: Path) -> None:
    database = tmp_path / "broken.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    root = tmp_path / "runtime"

    with pytest.raises(SourceSchemaError, match="required WhatsApp schema"):
        run_pipeline(database=database, root=root, mode="backfill", now=datetime(2026, 7, 23, tzinfo=UTC))

    assert not (root / "data" / "raw").exists()


def test_duplicate_chat_stanza_identity_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "ChatStorage.sqlite"
    create_fixture_db(database)
    connection = sqlite3.connect(database)
    connection.execute(
        """INSERT INTO ZWAMESSAGE
        (Z_PK,Z_OPT,ZCHATSESSION,ZGROUPMEMBER,ZMEDIAITEM,ZPARENTMESSAGE,ZMESSAGEDATE,ZSENTDATE,
         ZFROMJID,ZPUSHNAME,ZSTANZAID,ZTEXT,ZTOJID,ZISFROMME,ZMESSAGESTATUS,ZMESSAGETYPE,ZGROUPEVENTTYPE)
        SELECT 99,Z_OPT,ZCHATSESSION,ZGROUPMEMBER,ZMEDIAITEM,ZPARENTMESSAGE,ZMESSAGEDATE,ZSENTDATE,
         ZFROMJID,ZPUSHNAME,ZSTANZAID,'duplicate',ZTOJID,ZISFROMME,ZMESSAGESTATUS,ZMESSAGETYPE,ZGROUPEVENTTYPE
        FROM ZWAMESSAGE WHERE Z_PK=1"""
    )
    connection.commit()
    connection.close()

    with pytest.raises(SourceInvariantError, match="duplicate stable message identity"):
        run_pipeline(database=database, root=tmp_path / "runtime", mode="backfill", now=datetime(2026, 7, 23, tzinfo=UTC))


def test_broken_message_to_chat_relationship_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "ChatStorage.sqlite"
    create_fixture_db(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE ZWAMESSAGE SET ZCHATSESSION=999 WHERE Z_PK=1")
    connection.commit()
    connection.close()

    with pytest.raises(SourceInvariantError, match="stable chat identities"):
        run_pipeline(
            database=database,
            root=tmp_path / "runtime",
            mode="backfill",
            now=datetime(2026, 7, 23, tzinfo=UTC),
        )


def test_missing_canonical_timestamp_fails_before_archive_write(tmp_path: Path) -> None:
    database = tmp_path / "ChatStorage.sqlite"
    create_fixture_db(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE ZWAMESSAGE SET ZMESSAGEDATE=NULL WHERE Z_PK=1")
    connection.commit()
    connection.close()
    root = tmp_path / "runtime"

    with pytest.raises(SourceInvariantError, match="canonical timestamps"):
        run_pipeline(
            database=database,
            root=root,
            mode="backfill",
            now=datetime(2026, 7, 23, tzinfo=UTC),
        )
    assert not root.exists()


def test_account_change_is_rejected_without_mixing_archives(tmp_path: Path) -> None:
    database = tmp_path / "ChatStorage.sqlite"
    create_fixture_db(database)
    root = tmp_path / "runtime"
    run_pipeline(database=database, root=root, mode="backfill", now=datetime(2026, 7, 23, tzinfo=UTC))

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE ZWAMESSAGE SET ZFROMJID=? WHERE ZISFROMME=1",
        (DIFFERENT_OWNER_JID,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(SourceInvariantError, match="account fingerprint changed"):
        run_pipeline(database=database, root=root, mode="recent", recent_days=3, now=datetime(2026, 7, 23, 2, tzinfo=UTC))

    assert len(raw_rows(CollectorPaths.from_root(root))) == 3


def test_oversized_message_is_split_below_page_limit(tmp_path: Path) -> None:
    database = tmp_path / "ChatStorage.sqlite"
    create_fixture_db(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE ZWAMESSAGE SET ZTEXT=? WHERE Z_PK=1", ("large-synthetic-block " * 9000,))
    connection.commit()
    connection.close()
    root = tmp_path / "runtime"

    result = run_pipeline(database=database, root=root, mode="backfill", now=datetime(2026, 7, 23, tzinfo=UTC))
    pages = rendered_pages(CollectorPaths.from_root(root))

    assert result["pages_written"] >= 3
    assert any("--part-" in path for path in pages)
    assert all(len(content.encode("utf-8")) <= MAX_PAGE_BYTES for content in pages.values())
    assert sum(content.count("large-synthetic-block") for content in pages.values()) == 9000
