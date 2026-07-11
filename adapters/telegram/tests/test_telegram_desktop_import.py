from __future__ import annotations

from pathlib import Path

from telegram_desktop_import import import_export, parse_export


HTML = """<!doctype html><html><body>
<div class="page_header"><div class="text bold">Synthetic Ship Chat</div></div>
<div class="history">
  <div class="message default clearfix" id="message10">
    <div class="body">
      <div class="pull_right date details" title="09.07.2026 23:30:00 UTC-06:00">23:30</div>
      <div class="from_name">Fixture User</div>
      <div class="text">First &lt;!-- value --&gt;</div>
    </div>
  </div>
  <div class="message default clearfix joined" id="message11">
    <div class="body">
      <div class="pull_right date details" title="09.07.2026 23:31:00 UTC-06:00">23:31</div>
      <div class="reply_to details"><a href="#go_to_message10">Reply</a></div>
      <div class="text">Joined reply</div>
      <div class="media_wrap clearfix"><a class="document_wrap" href="files/example.txt">file</a></div>
      <span class="reactions"><span class="reaction">👍 2</span></span>
    </div>
  </div>
  <div class="message service" id="message-1"><div class="body details">10 July 2026</div></div>
</div></body></html>"""


def _export(tmp_path: Path) -> Path:
    root = tmp_path / "export"
    (root / "files").mkdir(parents=True)
    (root / "messages.html").write_text(HTML, encoding="utf-8")
    (root / "files" / "example.txt").write_text("artifact", encoding="utf-8")
    return root


def test_parse_html_export_inherits_sender_and_normalizes_utc(tmp_path):
    events, manifest = parse_export(
        _export(tmp_path),
        account_id="fixture-account",
        peer_kind="chat",
        peer_raw_id="123",
        peer_marked_id="-123",
    )

    assert manifest["message_count"] == 2
    assert [event["message"]["id"] for event in events] == [10, 11]
    assert events[0]["message"]["date"] == "2026-07-10T05:30:00Z"
    assert events[0]["message"]["source_date"] == "09.07.2026 23:30:00 UTC-06:00"
    assert events[0]["observed_at"] == "2026-07-10T05:31:00Z"
    assert events[1]["message"]["sender"]["display_name"] == "Fixture User"
    assert events[1]["message"]["reply_to_msg_id"] == 10
    assert events[1]["message"]["reply_to_resolved"] is True
    assert events[1]["message"]["reactions"] == [{"display": "👍 2"}]
    assert events[1]["message"]["media"]["attachments"][0]["present"] is True
    assert events[0]["provenance"]["capture_method"] == "desktop_html"
    assert events[0]["provenance"]["export_utc_offset"] == "-06:00"
    assert manifest["service_record_count"] == 1
    assert manifest["joined_message_count"] == 1


def test_import_html_export_writes_raw_manifest_and_parser_projection(tmp_path):
    output = tmp_path / "output"
    summary = import_export(
        _export(tmp_path),
        output,
        account_id="fixture-account",
        peer_kind="chat",
        peer_raw_id="123",
        peer_marked_id="-123",
    )

    assert summary["raw_artifacts"] == 1
    assert summary["markdown_pages"] == 1
    raw = output / "data/raw/chat-123/2026-07.jsonl"
    page = output / "brain/telegram/chats/chat-123/2026/2026-07-10.md"
    manifest = output / "data/exports/ship-ship-ship-2026-07-10.manifest.json"
    assert raw.is_file() and page.is_file() and manifest.is_file()
    rendered = page.read_text(encoding="utf-8")
    assert "source_id: \"telegram\"" in rendered
    assert "**[05:30] 👤 Fixture User:** First < !-- value -- >" in rendered
    assert "**[05:31] 👤 Fixture User:** Joined reply [reply_to: 10]" in rendered
    assert "access_hash" not in raw.read_text(encoding="utf-8")


def test_import_html_export_is_byte_deterministic(tmp_path):
    export_root = _export(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    identity = {
        "account_id": "fixture-account",
        "peer_kind": "chat",
        "peer_raw_id": "123",
        "peer_marked_id": "-123",
    }

    import_export(export_root, first, **identity)
    import_export(export_root, second, **identity)

    first_files = {
        path.relative_to(first): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files
