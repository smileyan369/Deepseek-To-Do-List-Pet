"""Official DeepSeek account balance.

`GET {origin}/user/balance` is DeepSeek's only documented account endpoint; the
token-usage history the platform console shows has no API at all (the community
route reads an undocumented platform endpoint with a browser session token, which
this app deliberately does not use). Amounts arrive as decimal strings, so they
are parsed into Decimal instead of float, and the bubble's type scale grows with
the balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .chat import ChatApiError, ChatConfig
from .runtime_guard import removed_runtime_hint, was_runtime_removed


BALANCE_PATH = "/user/balance"
DEFAULT_ORIGIN = "https://api.deepseek.com"
MIN_FONT_PX = 15
MAX_FONT_PX = 40
CURRENCY_SYMBOLS = {"CNY": "¥", "USD": "$"}


@dataclass(frozen=True)
class Balance:
    """One currency's balance snapshot; `total` already includes `granted`."""
    currency: str
    total: Decimal
    granted: Decimal
    topped_up: Decimal
    available: bool

    @property
    def symbol(self) -> str:
        return CURRENCY_SYMBOLS.get(self.currency, "")

    def format_total(self) -> str:
        return f"{self.symbol}{self.total:,.2f}"

    def font_px(self) -> int:
        return balance_font_px(self.total)


def balance_endpoint(base_url: str) -> str:
    """The balance API sits at the API origin; both /user/balance and
    /v1/user/balance answer, so a configured /v1 suffix is dropped."""
    parsed = urlparse((base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return DEFAULT_ORIGIN + BALANCE_PATH
    return f"{parsed.scheme}://{parsed.netloc}{BALANCE_PATH}"


def balance_font_px(total: Decimal) -> int:
    """Bigger balance, bigger type on a log scale: ¥1≈17px, ¥100≈31px,
    ¥1000≈39px, clamped to 15–40px so the bubble stays the pet's size."""
    value = max(float(total), 0.0)
    scaled = MIN_FONT_PX + 8.0 * math.log10(value + 1.0)
    return int(round(min(float(MAX_FONT_PX), max(float(MIN_FONT_PX), scaled))))


def _amount(value: object) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        return Decimal("0")


def parse_balance(payload: dict) -> Balance:
    """Read the documented shape; a missing entry degrades to a zero balance."""
    infos = payload.get("balance_infos") or []
    chosen = next((item for item in infos
                   if str(item.get("currency", "")).upper() == "CNY"), None)
    if chosen is None:
        chosen = infos[0] if infos else {}
    return Balance(
        currency=str(chosen.get("currency", "") or "CNY").upper(),
        total=_amount(chosen.get("total_balance", 0)),
        granted=_amount(chosen.get("granted_balance", 0)),
        topped_up=_amount(chosen.get("topped_up_balance", 0)),
        available=bool(payload.get("is_available", True)),
    )


def _error_message(exc: HTTPError) -> str:
    if exc.code == 401:
        return "API Key 无效或已失效，请在“配置聊天 API”中更新。"
    if exc.code == 404:
        return "当前接口地址不提供余额查询，余额仅支持 DeepSeek 官方接口。"
    if exc.code == 429:
        return "余额查询过于频繁，请稍后再试。"
    if 500 <= exc.code < 600:
        return f"DeepSeek 服务暂时不可用（HTTP {exc.code}）。"
    try:
        body = exc.fp.read() if getattr(exc, "fp", None) else b""
    except (OSError, ValueError):
        body = b""
    detail = body.decode("utf-8", errors="replace")[:200]
    return f"余额查询失败（HTTP {exc.code}）" + (f"：{detail}" if detail else "")


class DeepSeekBalanceClient:
    """Read-only balance lookup against the official API key."""

    def fetch(self, config: ChatConfig, timeout: int = 15) -> Balance:
        key = config.api_key.strip()
        if not key:
            raise ChatApiError("尚未配置 API Key，无法查询余额。")
        request = Request(balance_endpoint(config.base_url), headers={
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
        })
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ChatApiError(_error_message(exc)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            detail = removed_runtime_hint() if was_runtime_removed(exc) else str(exc)
            raise ChatApiError(f"无法连接余额接口：{detail}") from exc
        except ValueError as exc:
            raise ChatApiError("余额接口返回了无法解析的内容。") from exc
        return parse_balance(payload)
