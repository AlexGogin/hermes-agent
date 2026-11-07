"""Fetched-but-undispatched update journal for the Telegram adapter.

PTB 22.x polling confirms fetched updates server-side (``_last_update_id`` advance +
shutdown-time cleanup getUpdates) while dispatch happens asynchronously from
``Application.update_queue``. Any restart/generation-rebuild between fetch and dispatch
destroys the queued updates silently — Telegram will never redeliver them.

This journal makes that window at-least-once:

- the adapter's instrumented getUpdates seam writes every message-family update to the
  journal BEFORE dispatch (``put``);
- admission removes it when the update completes and is receipted (``remove``);
- a fresh Application instance replays surviving entries into its ``update_queue`` on
  start, so a crashed/rebuilt instance still delivers what it had fetched.

Entries carry an attempt counter: replayed updates that never complete (poison) are
dropped after ``JOURNAL_MAX_ATTEMPTS`` with an ERROR log, never retried forever.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

JOURNAL_TTL_SECONDS = 1800.0
JOURNAL_MAX_ATTEMPTS = 3
_MESSAGE_FAMILY_KEYS = ("message", "edited_message", "channel_post", "edited_channel_post")


def is_journalable_update(update_dict: dict) -> bool:
    """Only message-family updates: callback/inline replays could double-act on user state."""
    return any(isinstance(update_dict.get(k), dict) for k in _MESSAGE_FAMILY_KEYS)


class FetchedUpdateJournal:
    """Per-bot journal of fetched updates not yet known to be processed.

    Loop-confined like the update-receipt store: ``put``/``remove``/``note_attempt`` are
    called from loop callbacks only; persistence is coalesced through one flush task so
    the fsync stays off the event loop.
    """

    def __init__(self, adapter, bot_id: int):
        self._adapter = adapter
        self._bot_id = bot_id
        self._path = adapter._update_receipt_dir / f"telegram_fetched_journal_{bot_id}.json"
        self._entries: dict[str, dict] = {}
        self._dirty = False
        self._flush_task: Optional[asyncio.Task] = None
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            logger.warning("[Telegram] Ignoring unreadable fetched-update journal at %s", self._path, exc_info=True)
            return
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return
        cutoff = time.time() - JOURNAL_TTL_SECONDS
        for uid, rec in entries.items():
            if (isinstance(rec, dict) and isinstance(rec.get("raw"), dict)
                    and isinstance(rec.get("ts"), (int, float)) and rec["ts"] > cutoff):
                self._entries[str(uid)] = {"ts": float(rec["ts"]), "raw": rec["raw"],
                                           "attempts": int(rec.get("attempts") or 0)}

    def put(self, update_dict: dict) -> None:
        uid = update_dict.get("update_id")
        if uid is None or not is_journalable_update(update_dict):
            return
        self._load()
        self._entries[str(uid)] = {"ts": time.time(), "raw": update_dict, "attempts": 0}
        self._schedule_flush()

    def remove(self, update_id) -> None:
        if update_id is None:
            return
        key = str(update_id)
        if key in self._entries:
            del self._entries[key]
            self._schedule_flush()

    def note_attempt(self, update_id) -> None:
        key = str(update_id)
        rec = self._entries.get(key)
        if rec is not None:
            rec["attempts"] = int(rec.get("attempts") or 0) + 1
            self._schedule_flush()

    def sweep(self) -> None:
        """Drop expired and poison-spent entries."""
        self._load()
        cutoff = time.time() - JOURNAL_TTL_SECONDS
        expired = [k for k, v in self._entries.items() if v["ts"] <= cutoff]
        poison = [k for k, v in self._entries.items()
                  if int(v.get("attempts") or 0) >= JOURNAL_MAX_ATTEMPTS]
        for k in poison:
            logger.error(
                "[Telegram] Fetched-update journal dropping poisoned update_id=%s after %d "
                "replay attempts without completion", k, JOURNAL_MAX_ATTEMPTS)
        if expired or poison:
            for k in {*expired, *poison}:
                self._entries.pop(k, None)
            self._schedule_flush()

    def entries_for_replay(self) -> list[tuple[str, dict]]:
        self.sweep()
        return sorted(((k, v["raw"]) for k, v in self._entries.items()), key=lambda kv: kv[1].get("ts", 0))

    def __len__(self) -> int:
        return len(self._entries)

    def _schedule_flush(self) -> None:
        self._dirty = True
        if self._flush_task is None or self._flush_task.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._flush_task = loop.create_task(self._flush(), name="telegram-fetched-journal")

    async def _flush(self) -> None:
        while self._dirty:
            self._dirty = False
            snapshot = {k: v for k, v in self._entries.items()}
            try:
                from utils import atomic_json_write
                from hermes_constants import mkdir_under_hermes_home
                mkdir_under_hermes_home(self._path.parent)
                await asyncio.to_thread(atomic_json_write, self._path,
                                        {"entries": snapshot}, indent=None)
            except Exception:
                logger.warning("[Telegram] Failed to persist fetched-update journal to %s",
                               self._path, exc_info=True)
                self._dirty = True
                await asyncio.sleep(1.0)

    async def flush_now(self) -> None:
        """Disconnect-path landing: a replacement instance must see the final state."""
        if self._flush_task is not None and not self._flush_task.done():
            try:
                await asyncio.shield(self._flush_task)
            except Exception:
                pass
        if self._dirty:
            await self._flush()
