"""Regression: rich-message blocks may carry text as styled-run lists (10.11.2026).

CEO's 21-block rich message ghosted: ``_handle_rich_message`` crashed on
``(block.get("text") or "").strip()`` with ``AttributeError: 'list' object
has no attribute 'strip'`` when a paragraph block delivered ``text`` as a
list of styled runs. The update died in dispatch — no error handler, no
delivery. ``_rich_text_str`` must coerce str / list-of-runs / dict / None.
"""

from plugins.platforms.telegram.adapter import _rich_text_str


class TestRichTextStr:
    def test_plain_string_passthrough(self):
        assert _rich_text_str("привет") == "привет"

    def test_none_and_missing(self):
        assert _rich_text_str(None) == ""

    def test_list_of_styled_runs_joins_text(self):
        runs = [
            {"type": "text", "text": "Заголовок: "},
            {"type": "bold", "text": "важно"},
            {"type": "text", "text": " и дальше"},
        ]
        assert _rich_text_str(runs) == "Заголовок: важно и дальше"

    def test_list_with_bare_strings(self):
        assert _rich_text_str(["a", {"text": "b"}, None]) == "ab"

    def test_dict_payload_unwraps_text(self):
        assert _rich_text_str({"type": "paragraph", "text": "тело"}) == "тело"

    def test_nested_list_in_dict(self):
        payload = {"text": [{"text": "x"}, {"text": "y"}]}
        assert _rich_text_str(payload) == "xy"

    def test_numbers_coerce(self):
        assert _rich_text_str(0) == "0"
