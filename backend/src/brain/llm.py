"""DeepSeek 流式对话大脑（config.yaml llm 段驱动）。

对齐：
- SPEC §2 BrainOutput（增量 token + is_final）——本模块的 BrainChunk 是它的流式载体
- SPEC §7 时序：LLM 首 token 超时 1.5s → 重试一次，仍失败则抛 BrainError（上层降级）
- DESIGN §5.1：TTFT（首 token 延迟）是本模块必须产出的打点，供延迟瀑布使用
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import AsyncIterator, Sequence

import httpx

from config import get_secret, load_config

DEFAULT_TIMEOUT_S = 30.0
FIRST_TOKEN_TIMEOUT_S = 1.5  # SPEC §7：LLM 首 token 1.5s


class BrainError(Exception):
    """LLM 段错误（code 对齐 SPEC §6 错误码：LLM_TIMEOUT / LLM_ERROR）"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class BrainChunk:
    """一片增量输出"""

    token: str
    index: int
    is_final: bool = False
    ttft_ms: int | None = None  # 仅首片携带（延迟打点数据源）


@dataclass
class BrainStats:
    ttft_ms: int | None = None
    tokens: int = 0
    total_ms: int = 0


class DeepSeekBrain:
    """DeepSeek 流式客户端（OpenAI 兼容协议）。"""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        first_token_timeout_s: float = FIRST_TOKEN_TIMEOUT_S,
    ) -> None:
        cfg = (load_config().get("llm") or {})
        self.model: str = model or cfg.get("model") or "deepseek-chat"
        self.base_url: str = (base_url or cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.api_key_env: str = api_key_env or cfg.get("api_key_env") or "DEEPSEEK_API_KEY"
        self.first_token_timeout_s = first_token_timeout_s
        self.last_stats = BrainStats()

    @property
    def api_key(self) -> str | None:
        return get_secret(self.api_key_env)

    async def stream_chat(
        self,
        messages: Sequence[dict],
        max_tokens: int = 512,
        retry_on_first_token_timeout: bool = True,
    ) -> AsyncIterator[BrainChunk]:
        """流式产出增量 token。首 token 超时按 SPEC §7 重试一次。

        注意：仅"首 token 未到"才重试——此时尚无 token 产出，重试不会造成重复输出。
        """
        try:
            async for chunk in self._stream_once(messages, max_tokens):
                yield chunk
        except BrainError as exc:
            if exc.code == "LLM_TIMEOUT" and exc.message.startswith("首 token") and retry_on_first_token_timeout:
                async for chunk in self._stream_once(messages, max_tokens):
                    yield chunk
                return
            raise

    async def _stream_once(self, messages: Sequence[dict], max_tokens: int) -> AsyncIterator[BrainChunk]:
        key = self.api_key
        if not key:
            raise BrainError("LLM_ERROR", f"{self.api_key_env} 未配置（写入 backend/.env 或设为环境变量）")

        payload = {
            "model": self.model,
            "messages": list(messages),
            "stream": True,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        t0 = time.perf_counter()
        stats = BrainStats()
        index = 0
        first_seen = False

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=headers, json=payload
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise BrainError("LLM_ERROR", f"HTTP {resp.status_code}: {body}")

                lines = resp.aiter_lines()
                while True:
                    try:
                        line = await asyncio.wait_for(
                            lines.__anext__(),
                            timeout=self.first_token_timeout_s if not first_seen else DEFAULT_TIMEOUT_S,
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as exc:
                        if not first_seen:
                            raise BrainError(
                                "LLM_TIMEOUT", f"首 token 超过 {self.first_token_timeout_s}s 未返回"
                            ) from exc
                        raise BrainError("LLM_ERROR", "流中断：等待下一片超时") from exc

                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        evt = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = ((evt.get("choices") or [{}])[0].get("delta") or {})
                    token = delta.get("content")
                    if not token:
                        continue

                    if not first_seen:
                        first_seen = True
                        stats.ttft_ms = int((time.perf_counter() - t0) * 1000)
                    stats.tokens += 1
                    yield BrainChunk(token=token, index=index, ttft_ms=stats.ttft_ms if index == 0 else None)
                    index += 1

        stats.total_ms = int((time.perf_counter() - t0) * 1000)
        self.last_stats = stats
        if not first_seen:
            raise BrainError("LLM_ERROR", "流结束但未收到任何 token")
