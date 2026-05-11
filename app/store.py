from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time
from typing import Any


@dataclass
class StoredConversation:
    response: dict[str, Any]
    conversation: list[dict[str, Any]]
    input_items: list[dict[str, Any]]


class ConversationStore:
    def __init__(self, path: str | None = None) -> None:
        self._items: dict[str, StoredConversation] = {}
        self._lock = Lock()
        self._path = Path(path).expanduser() if path else None
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def save(
        self,
        response_id: str,
        response: dict[str, Any],
        conversation: list[dict[str, Any]],
        input_items: list[dict[str, Any]] | None = None,
        conversation_key: str | None = None,
        save_response_id: bool = True,
    ) -> None:
        record = StoredConversation(
            response=deepcopy(response),
            conversation=deepcopy(conversation),
            input_items=deepcopy(input_items or []),
        )
        with self._lock:
            if save_response_id:
                self._items[response_id] = record
                self._save_db_record(response_id, record)
            if conversation_key:
                self._items[conversation_key] = record
                self._save_db_record(conversation_key, record)

    def get_response(self, response_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._get_record(response_id)
            return None if record is None else deepcopy(record.response)

    def get_conversation(self, response_id: str) -> list[dict[str, Any]] | None:
        with self._lock:
            record = self._get_record(response_id)
            return None if record is None else deepcopy(record.conversation)

    def get_input_items(self, response_id: str) -> list[dict[str, Any]] | None:
        with self._lock:
            record = self._get_record(response_id)
            return None if record is None else deepcopy(record.input_items)

    def delete_response(self, response_id: str) -> bool:
        with self._lock:
            existed = self._items.pop(response_id, None) is not None
            db_existed = self._delete_db_record(response_id)
            return existed or db_existed

    def cancel_response(self, response_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._get_record(response_id)
            if record is None:
                return None
            response = deepcopy(record.response)
            if response.get("status") in {"queued", "in_progress"}:
                response["status"] = "cancelled"
                response["completed_at"] = int(time.time())
                response["error"] = None
                record.response = deepcopy(response)
                self._items[response_id] = record
                self._save_db_record(response_id, record)
            return deepcopy(record.response)

    def update_response(self, response_id: str, response: dict[str, Any]) -> None:
        with self._lock:
            record = self._get_record(response_id)
            if record is None:
                record = StoredConversation(response=deepcopy(response), conversation=[], input_items=[])
            else:
                record.response = deepcopy(response)
            self._items[response_id] = record
            self._save_db_record(response_id, record)

    def _init_db(self) -> None:
        if not self._path:
            return
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS responses (
                    key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    conversation_json TEXT NOT NULL,
                    input_items_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _get_record(self, key: str) -> StoredConversation | None:
        record = self._items.get(key)
        if record is not None:
            return record
        if not self._path:
            return None
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                "SELECT response_json, conversation_json, input_items_json FROM responses WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        record = StoredConversation(
            response=json.loads(row[0]),
            conversation=json.loads(row[1]),
            input_items=json.loads(row[2]),
        )
        self._items[key] = record
        return record

    def _save_db_record(self, key: str, record: StoredConversation) -> None:
        if not self._path:
            return
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO responses (key, response_json, conversation_json, input_items_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    response_json = excluded.response_json,
                    conversation_json = excluded.conversation_json,
                    input_items_json = excluded.input_items_json,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    json.dumps(record.response, ensure_ascii=False),
                    json.dumps(record.conversation, ensure_ascii=False),
                    json.dumps(record.input_items, ensure_ascii=False),
                    int(time.time()),
                ),
            )

    def _delete_db_record(self, key: str) -> bool:
        if not self._path:
            return False
        with sqlite3.connect(self._path) as conn:
            cursor = conn.execute("DELETE FROM responses WHERE key = ?", (key,))
            return cursor.rowcount > 0
