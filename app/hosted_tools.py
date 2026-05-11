from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
import re
import uuid
from typing import Any

import httpx

from app.config import Settings

HOSTED_TOOL_TYPES = {
    "web_search",
    "web_search_preview",
    "file_search",
    "computer_use",
    "computer_use_preview",
}

TEXT_FILE_SUFFIXES = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".html",
    ".css",
}

INTERNAL_CONTEXT_BLOCKS = (
    "permissions instructions",
    "app-context",
    "environment_context",
    "collaboration_mode",
    "personality_spec",
    "apps_instructions",
    "skills_instructions",
    "plugins_instructions",
)


@dataclass
class HostedToolContext:
    messages: list[dict[str, str]] = field(default_factory=list)
    output_items: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class HostedToolSection:
    content: str
    output_item: dict[str, Any] | None = None
    annotations: list[dict[str, Any]] = field(default_factory=list)


def make_tool_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


async def build_hosted_tool_context(
    payload: dict[str, Any],
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HostedToolContext:
    tool_types = collect_hosted_tool_types(payload.get("tools"))
    if not tool_types:
        return HostedToolContext()

    query = extract_query_text(payload.get("input"))
    sections: list[HostedToolSection] = []
    if "web_search" in tool_types or "web_search_preview" in tool_types:
        sections.append(await build_web_search_section(query, settings, transport))
    if "file_search" in tool_types:
        sections.append(build_file_search_section(query, settings))
    if "computer_use" in tool_types or "computer_use_preview" in tool_types:
        sections.append(build_computer_use_section(payload))

    content = "\n\n".join(section.content for section in sections if section.content)
    if not content:
        return HostedToolContext()
    return HostedToolContext(
        messages=[{"role": "system", "content": content}],
        output_items=[section.output_item for section in sections if section.output_item],
        annotations=[
            annotation
            for section in sections
            for annotation in section.annotations
        ],
    )


async def build_hosted_tool_context_messages(
    payload: dict[str, Any],
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, str]]:
    return (await build_hosted_tool_context(payload, settings, transport)).messages


def collect_hosted_tool_types(tools: Any) -> set[str]:
    if not isinstance(tools, list):
        return set()
    found: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if tool_type in HOSTED_TOOL_TYPES:
            found.add(str(tool_type))
        if tool_type == "namespace":
            nested = tool.get("tools") or tool.get("items") or tool.get("children")
            found.update(collect_hosted_tool_types(nested))
    return found


async def build_web_search_section(
    query: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
) -> HostedToolSection:
    backend = settings.web_search_backend.lower()
    if not query:
        return HostedToolSection("Local web search results:\nNo query text was available.")
    try:
        if backend == "searxng" and settings.web_search_searxng_url:
            results = await search_searxng(query, settings, transport)
        elif backend == "tavily" and settings.web_search_tavily_api_key:
            results = await search_tavily(query, settings, transport)
        else:
            return HostedToolSection("Local web search results:\nNo web search backend is configured.")
    except httpx.HTTPStatusError as exc:
        return HostedToolSection(
            f"Local web search results for `{query}`:\n"
            f"Search backend unavailable: HTTP {exc.response.status_code}. Continue without external search results."
        )
    except (httpx.HTTPError, ValueError):
        return HostedToolSection(
            f"Local web search results for `{query}`:\n"
            "Search backend unavailable. Continue without external search results."
        )
    if not results:
        return HostedToolSection(f"Local web search results for `{query}`:\nNo results.")
    return HostedToolSection(
        "Local web search results for `{}`:\n{}".format(
            query,
            "\n".join(format_result(index, result) for index, result in enumerate(results, start=1)),
        ),
        output_item={
            "id": make_tool_id("ws"),
            "type": "web_search_call",
            "status": "completed",
            "action": {"type": "search", "query": query},
            "results": results,
        },
        annotations=[
            {
                "type": "url_citation",
                "url": result.get("url", ""),
                "title": result.get("title", ""),
            }
            for result in results
            if result.get("url")
        ],
    )


async def search_searxng(
    query: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
) -> list[dict[str, str]]:
    json_error: Exception | None = None
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, transport=transport) as client:
        try:
            response = await client.get(
                settings.web_search_searxng_url,
                params={"q": query, "format": "json"},
                headers=searxng_headers("application/json,text/html;q=0.9,*/*;q=0.8"),
            )
            response.raise_for_status()
            payload = response.json()
            results = normalize_search_results(
                payload.get("results", []),
                max_results=settings.web_search_max_results,
            )
            if results:
                return results
        except (httpx.HTTPError, ValueError) as exc:
            json_error = exc

        html_results = await search_searxng_html(query, settings, client)
        if html_results:
            return html_results

    if json_error:
        raise json_error
    return []


async def search_searxng_html(
    query: str,
    settings: Settings,
    client: httpx.AsyncClient,
) -> list[dict[str, str]]:
    response = await client.get(
        settings.web_search_searxng_url,
        params={"q": query},
        headers=searxng_headers("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    )
    response.raise_for_status()
    return parse_searxng_html_results(response.text, max_results=settings.web_search_max_results)


def searxng_headers(accept: str) -> dict[str, str]:
    return {
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
    }


def parse_searxng_html_results(html_text: str, max_results: int = 5) -> list[dict[str, str]]:
    blocks = re.findall(
        r"<(?:article|div|li)\b[^>]*class=[\"'][^\"']*result[^\"']*[\"'][^>]*>(.*?)</(?:article|div|li)>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    results: list[dict[str, str]] = []
    for block in blocks:
        anchor = re.search(
            r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not anchor:
            continue
        url = unescape(anchor.group(1)).strip()
        title = clean_html_text(anchor.group(2))
        snippet_match = re.search(
            r"<p\b[^>]*class=[\"'][^\"']*(?:content|snippet)[^\"']*[\"'][^>]*>(.*?)</p>",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        content = clean_html_text(snippet_match.group(1)) if snippet_match else clean_html_text(block)
        if title or url or content:
            results.append({"title": title, "url": url, "content": content})
        if len(results) >= max(1, max_results):
            break
    return results


def clean_html_text(value: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


async def search_tavily(
    query: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, transport=transport) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.web_search_tavily_api_key,
                "query": query,
                "max_results": settings.web_search_max_results,
            },
        )
        response.raise_for_status()
        payload = response.json()
    return normalize_search_results(payload.get("results", []), max_results=settings.web_search_max_results)


def normalize_search_results(raw_results: Any, max_results: int = 5) -> list[dict[str, str]]:
    if not isinstance(raw_results, list):
        return []
    results: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or item.get("snippet") or "").strip()
        if title or url or content:
            results.append({"title": title, "url": url, "content": content})
    return results[:max(1, max_results)]


def format_result(index: int, result: dict[str, str]) -> str:
    title = result.get("title") or "Untitled"
    url = result.get("url") or "no-url"
    content = result.get("content") or ""
    return f"{index}. {title}\nURL: {url}\nSnippet: {content}"


def build_file_search_section(query: str, settings: Settings) -> HostedToolSection:
    paths = [Path(path) for path in settings.file_search_paths]
    if not paths:
        return HostedToolSection("Local file search results:\nNo file search paths are configured.")
    terms = tokenize_query(query)
    matches: list[dict[str, str]] = []
    for root in paths:
        if not root.exists():
            continue
        for path in iter_text_files(root):
            snippet = find_file_snippet(path, terms)
            if snippet:
                matches.append({"filename": str(path), "text": snippet})
            if len(matches) >= settings.file_search_max_results:
                break
        if len(matches) >= settings.file_search_max_results:
            break
    if not matches:
        return HostedToolSection(f"Local file search results for `{query}`:\nNo matches.")
    return HostedToolSection(
        "Local file search results for `{}`:\n{}".format(
            query,
            "\n".join(f"- {match['filename']}: {match['text']}" for match in matches),
        ),
        output_item={
            "id": make_tool_id("fs"),
            "type": "file_search_call",
            "status": "completed",
            "queries": [query] if query else [],
            "results": matches,
        },
        annotations=[
            {
                "type": "file_citation",
                "filename": match["filename"],
                "text": match["text"],
            }
            for match in matches
        ],
    )


def iter_text_files(root: Path):
    if root.is_file():
        candidates = [root]
    else:
        candidates = root.rglob("*")
    for path in candidates:
        if not path.is_file() or path.suffix.lower() not in TEXT_FILE_SUFFIXES:
            continue
        yield path


def find_file_snippet(path: Path, terms: list[str]) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if not terms:
        return lines[0][:240]
    lowered_terms = [term.lower() for term in terms]
    for line in lines:
        lowered = line.lower()
        if all(term in lowered for term in lowered_terms[:3]):
            return line[:240]
    for line in lines:
        lowered = line.lower()
        if any(term in lowered for term in lowered_terms):
            return line[:240]
    return ""


def build_computer_use_section(payload: dict[str, Any]) -> HostedToolSection:
    output_count = count_input_items(payload.get("input"), "computer_call_output")
    return HostedToolSection(
        (
            "Local computer use context:\n"
            "computer_use_preview is handled by this proxy as local context. "
            f"Received computer_call_output items: {output_count}."
        ),
        output_item={
            "id": make_tool_id("cu"),
            "type": "computer_call",
            "status": "completed",
            "action": {"type": "inspect"},
            "received_call_outputs": output_count,
        },
    )


def count_input_items(input_value: Any, item_type: str) -> int:
    if isinstance(input_value, dict):
        items = [input_value]
    elif isinstance(input_value, list):
        items = input_value
    else:
        return 0
    return sum(1 for item in items if isinstance(item, dict) and item.get("type") == item_type)


def extract_query_text(input_value: Any) -> str:
    if isinstance(input_value, str):
        return normalize_search_query(input_value)
    if isinstance(input_value, dict):
        items = [input_value]
    elif isinstance(input_value, list):
        items = input_value
    else:
        return ""

    user_texts: list[str] = []
    fallback_texts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content_text = extract_text_from_content(item.get("content"))
        if not content_text and item.get("type") == "computer_call_output" and "output" in item:
            content_text = str(item.get("output", ""))
        if not content_text:
            continue

        role = item.get("role")
        if role == "user":
            user_texts.append(content_text)
        elif role is None and item.get("type") in {None, "message"}:
            fallback_texts.append(content_text)

    selected = user_texts[-1] if user_texts else (fallback_texts[-1] if fallback_texts else "")
    return normalize_search_query(selected)


def extract_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    texts: list[str] = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if isinstance(part, dict) and part.get("type") in {"input_text", "text"}:
            texts.append(str(part.get("text", "")))
    return " ".join(text.strip() for text in texts if text.strip())


def normalize_search_query(text: str) -> str:
    cleaned = strip_internal_context_blocks(str(text))
    for marker in (
        "## My request for Codex:",
        "My request for Codex:",
        "用户请求：",
        "用户请求:",
        "User request:",
    ):
        if marker in cleaned:
            cleaned = cleaned.rsplit(marker, 1)[-1]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:240]


def strip_internal_context_blocks(text: str) -> str:
    cleaned = text
    for tag in INTERNAL_CONTEXT_BLOCKS:
        pattern = re.compile(rf"<{re.escape(tag)}>.*?</{re.escape(tag)}>", re.IGNORECASE | re.DOTALL)
        cleaned = pattern.sub(" ", cleaned)
    return cleaned


def tokenize_query(query: str) -> list[str]:
    return [token for token in re.split(r"\W+", query) if len(token) >= 2][:8]
