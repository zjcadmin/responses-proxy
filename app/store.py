from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass
class StoredConversation:
    response: dict[str, Any]
    conversation: list[dict[str, Any]]


class ConversationStore:
    def __init__(self) -> None:
        self._items: dict[str, StoredConversation] = {}
        self._lock = Lock()

    def save(
        self,
        response_id: str,
        response: dict[str, Any],
        conversation: list[dict[str, Any]],
        conversation_key: str | None = None,
        save_response_id: bool = True,
    ) -> None:
        record = StoredConversation(
            response=deepcopy(response),
            conversation=deepcopy(conversation),
        )
        with self._lock:
            if save_response_id:
                self._items[response_id] = record
            if conversation_key:
                self._items[conversation_key] = record

    def get_response(self, response_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._items.get(response_id)
            return None if record is None else deepcopy(record.response)

    def get_conversation(self, response_id: str) -> list[dict[str, Any]] | None:
        with self._lock:
            record = self._items.get(response_id)
            return None if record is None else deepcopy(record.conversation)
