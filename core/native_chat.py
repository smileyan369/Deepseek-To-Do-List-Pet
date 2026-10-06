"""DeepSeek 官方服务端联网搜索，直接流式聊天。

模型自己决定要不要搜：给它托管式 `web_search` 工具（和 Claude Code 用的是同一种
机制），搜索在 DeepSeek 服务器上完成，返回 `server_tool_use`（搜索词）与
`web_search_tool_result`（标题/网址/页面日期）块。桌宠只需要渲染 text 增量，
其余块让「想一想…」继续转，既不抓网页也不存在验证码问题。

同时提供：
* `UrlRedactor` —— 流式去除网址的保险丝（提示词之外的最后一道防线），
  半截 URL/半截 Markdown 链接会被扣住，宁可晚一帧也不让它闪上屏幕；
* `is_transient_error` —— 决定原生路径失败后值不值得回退到免费搜索源。

实测（2026-10-05，官方 Key）：不联网的问题 0.5–1.0 秒答完；需要联网的问题
首个搜索块约 3 秒、回答约 3.7 秒出现，全程无需任何本地抓取。
"""

from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .chat import ChatApiError
from .runtime_guard import removed_runtime_hint, was_runtime_removed


SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

_URL_STARTS = ("http://", "https://", "www.")
_PARTIAL_MARKERS = ("http://", "https://", "www.", "](")
_MD_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(\s*(?:https?://|www\.)[^)]*\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_SPACES_RE = re.compile(r"[ \t]{2,}")


def strip_urls(text: str) -> str:
    """最终清理：Markdown 链接只留标题、裸网址整段删除。"""
    text = _MD_LINK_RE.sub(r"\1", text or "")
    text = _BARE_URL_RE.sub("", text)
    return _SPACES_RE.sub(" ", text)


class UrlRedactor:
    """流式剥网址：能判断为网址/链接目标的内容不会进入界面。

    文本可能被任意切块，因此把「还可能长成网址标记」的结尾（比如被切开的
    "ht"、"www."、待补右括号的 "]("）扣在缓冲区里，直到能判断为止；判定为
    网址就一路吞到空白，判定不是就原样放出。`flush()` 收起结尾残余。
    """

    def __init__(self) -> None:
        self.buffer = ""
        self.swallowing = False    # 正在吞一个已开始、未结束的网址
        self.link_target = False   # 正在吞 Markdown 链接的 "](...)" 目标

    def feed(self, text: str) -> str:
        self.buffer += text or ""
        emitted: list[str] = []
        index = 0
        while index < len(self.buffer):
            char = self.buffer[index]
            if self.swallowing:
                if char.isspace():
                    self.swallowing = False
                    emitted.append(char)
                index += 1
                continue
            if self.link_target:
                if char == ")":
                    self.link_target = False
                index += 1
                continue
            if self._url_starts_at(index):
                end = index
                while end < len(self.buffer) and not self.buffer[end].isspace():
                    end += 1
                if end == len(self.buffer):
                    # 网址可能还没结束，等下一块数据再判断。
                    break
                self.swallowing = True
                index = end
                continue
            if self.buffer.startswith("](", index):
                close = self.buffer.find(")", index + 2)
                if close == -1:
                    break
                # Drop the matching "[" of this "[label](target)" so the label
                # reads as plain text instead of a dangling bracket.
                for back in range(len(emitted) - 1, -1, -1):
                    if emitted[back] == "[":
                        del emitted[back]
                        break
                index = close + 1
                continue
            emitted.append(char)
            index += 1
        self.buffer = self.buffer[index:]
        self._hold_partial(emitted)
        return "".join(emitted)

    def flush(self) -> str:
        """收尾：把还扣着的文本放出来，未结束的网址直接丢弃。"""
        text = self.buffer
        self.buffer = ""
        self.swallowing = False
        self.link_target = False
        text = re.sub(r"\]\([^)]*$", "", text)  # 半截链接目标
        return strip_urls(text)

    def _url_starts_at(self, index: int) -> bool:
        return any(self.buffer.startswith(marker, index) for marker in _URL_STARTS)

    def _hold_partial(self, emitted: list[str]) -> None:
        """把结尾处可能长成网址标记的片段扣回缓冲区。"""
        for marker in _PARTIAL_MARKERS:
            for size in range(min(len(marker) - 1, len(emitted)), 0, -1):
                if "".join(emitted[-size:]) == marker[:size]:
                    self.buffer = "".join(emitted[-size:]) + self.buffer
                    del emitted[-size:]
                    return


def is_transient_error(exc: BaseException) -> bool:
    """只有临时性故障才值得回退到免费搜索源。

    401/403/400 这类配置或协议错误要原样报给用户，静默回退会让“Key 填错了”
    看起来像“搜索不好用”，极难排查。
    """
    text = str(exc)
    match = re.search(r"HTTP (\d{3})", text)
    if match:
        code = int(match.group(1))
        return code >= 500 or code in (408, 429)
    return True


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Anthropic 协议把 system 放在顶层，聊天记录里其余消息保持原样。"""
    system_parts: list[str] = []
    conversation: list[dict] = []
    for item in messages:
        if item.get("role") == "system":
            content = str(item.get("content") or "")
            if content:
                system_parts.append(content)
        else:
            conversation.append({"role": item.get("role", "user"), "content": item.get("content", "")})
    return "\n\n".join(system_parts), conversation


class DeepSeekNativeClient:
    """走 DeepSeek 的 Anthropic 兼容端点，让模型在服务端直接联网。"""

    MAX_CONTINUATIONS = 2

    def __init__(self, config, web_search: bool | None = None):
        self.api_key = str(getattr(config, "api_key", "") or "").strip()
        self.model = str(getattr(config, "model", "") or "").strip()
        if web_search is None:
            web_search = bool(getattr(config, "web_search", True))
        self.web_search = web_search
        parsed = urlparse(str(getattr(config, "base_url", "") or "").strip())
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://api.deepseek.com"
        self.endpoint = origin + "/anthropic/v1/messages"

    def stream(self, messages: list[dict]):
        """Yield 可见文本增量；思考与工具块只用来驱动「想一想…」。"""
        if not self.api_key:
            raise ChatApiError("未配置 DeepSeek Key")
        system, conversation = _split_system(messages)
        continuations = 0
        while True:
            payload = {
                "model": self.model,
                "max_tokens": 2048,
                "stream": True,
                "messages": conversation,
            }
            if system:
                payload["system"] = system
            if self.web_search:
                payload["tools"] = [SEARCH_TOOL]
            blocks: dict[int, dict] = {}
            stop_reason = None
            for event in self._events(payload):
                kind = event.get("type")
                if kind == "content_block_start":
                    index = int(event.get("index") or 0)
                    blocks[index] = dict(event.get("content_block") or {"type": "text"})
                elif kind == "content_block_delta":
                    index = int(event.get("index") or 0)
                    delta = event.get("delta") or {}
                    delta_type = delta.get("type")
                    # A block can be referenced by a delta before its start event
                    # shows up if a proxy truncates the stream; keep both safe.
                    block = blocks.setdefault(index, {"type": "text"})
                    if delta_type == "text_delta":
                        text = str(delta.get("text") or "")
                        if text:
                            block["text"] = str(block.get("text") or "") + text
                            yield text
                    elif delta_type == "input_json_delta":
                        block["input_json"] = str(block.get("input_json") or "") + str(delta.get("partial_json") or "")
                    elif delta_type == "thinking_delta":
                        block["thinking"] = str(block.get("thinking") or "") + str(delta.get("thinking") or "")
                elif kind == "message_delta":
                    stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
                elif kind == "message_stop":
                    break
            if stop_reason == "pause_turn" and blocks and continuations < self.MAX_CONTINUATIONS:
                # 服务端一轮没做完：把 assistant 的全部块原样接回去继续。
                continuations += 1
                conversation = conversation + [{"role": "assistant", "content": _finalize_blocks(blocks)}]
                continue
            return

    def _events(self, payload: dict):
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Accept": "text/event-stream",
            },
        )
        try:
            with urlopen(request, timeout=120) as response:
                content_type = response.headers.get("Content-Type", "") if response.headers else ""
                if "text/event-stream" not in content_type:
                    raw = json.loads(response.read().decode("utf-8"))
                    for block in raw.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "text":
                            yield {"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "text_delta", "text": str(block.get("text") or "")}}
                    yield {"type": "message_stop"}
                    return
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        event = json.loads(chunk)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("type"):
                        yield event
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except OSError:
                detail = ""
            raise ChatApiError(f"API 请求失败（HTTP {exc.code}）{': ' + detail if detail else ''}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ChatApiError(f"无法连接聊天 API：{removed_runtime_hint() if was_runtime_removed(exc) else exc}") from exc


def _finalize_blocks(blocks: dict[int, dict]) -> list[dict]:
    """Turn accumulated stream state into the message blocks a continuation needs."""
    content: list[dict] = []
    for index in sorted(blocks):
        block = dict(blocks[index])
        partial = block.pop("input_json", None)
        if partial is not None:
            try:
                block["input"] = json.loads(partial) if partial else {}
            except ValueError:
                block["input"] = {}
        if block.get("type") == "text" and not block.get("text"):
            continue
        content.append(block)
    return content
