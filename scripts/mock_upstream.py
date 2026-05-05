from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="mock-upstream")


class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    stream: bool = False


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest) -> dict:
    last_message = request.messages[-1]["content"] if request.messages else ""
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": 1714444444,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"mock upstream received: {last_message}",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 6,
            "total_tokens": 16,
        },
    }
