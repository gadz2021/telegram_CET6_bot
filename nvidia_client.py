"""NVIDIA NIM API 客户端 — 封装模型列表和聊天功能"""

import asyncio
import logging
import time
import json
import os

import httpx
from openai import AsyncOpenAI

from config import NVIDIA_API_KEY, NVIDIA_BASE_URL, PROXY_URL, REQUEST_TIMEOUT, AVAILABLE_MODELS_FILE, DEFAULT_MODEL, RECOMMENDED_MODELS, TEST_PROMPT
from rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class NvidiaClient:
    """异步 NVIDIA NIM API 客户端，兼容 OpenAI 接口。"""

    def __init__(self, rate_limiter: RateLimiter):
        self.rate_limiter = rate_limiter

        # 构建 httpx 异步客户端（含代理和超时）
        client_kwargs: dict = {}
        if PROXY_URL:
            client_kwargs["proxy"] = PROXY_URL

        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=30.0),
            **client_kwargs,
        )
        self._client = AsyncOpenAI(
            base_url=NVIDIA_BASE_URL,
            api_key=NVIDIA_API_KEY,
            http_client=self._http_client,
        )

        # 模型列表缓存
        self._models_cache: list[dict] | None = None
        self._models_cache_time: float = 0
        self._cache_ttl: float = 600  # 10 分钟

    # ------------------------------------------------------------------
    # 模型列表
    # ------------------------------------------------------------------
    async def fetch_models(self) -> list[dict]:
        """获取本地检测过的可用模型列表及速度。"""
        now = time.time()
        if self._models_cache and (now - self._models_cache_time) < self._cache_ttl:
            return self._models_cache

        try:
            if os.path.exists(AVAILABLE_MODELS_FILE):
                with open(AVAILABLE_MODELS_FILE, "r", encoding="utf-8") as f:
                    models = json.load(f)
                if models:
                    # 确保格式兼容性
                    if isinstance(models[0], str):
                        models = [{"id": m, "speed": 0} for m in models]
                    
                    # 排序：推荐在前，其余按速度（快到慢）
                    rec_ids = RECOMMENDED_MODELS
                    rec = [m for m in models if m["id"] in rec_ids]
                    # 排序 rec 按照 RECOMMENDED_MODELS 的顺序
                    rec.sort(key=lambda x: rec_ids.index(x["id"]))
                    
                    others = [m for m in models if m["id"] not in rec_ids]
                    others.sort(key=lambda x: x.get("speed", 999))
                    
                    sorted_models = rec + others
                    self._models_cache = sorted_models
                    self._models_cache_time = time.time()
                    return sorted_models
        except Exception as e:
            logger.error("Failed to read available models: %s", e)

        # 兜底返回默认模型
        return [{"id": DEFAULT_MODEL, "speed": 0}]

    async def check_available_models(self) -> list[dict]:
        """主动测试所有模型，过滤出可用列表、测速并保存。"""
        logger.info("Started checking and speed-testing all models...")
        await self.rate_limiter.acquire()
        try:
            resp = await self._client.models.list()
            all_ids = sorted(m.id for m in resp.data)
        except Exception as e:
            logger.error("Failed to fetch full model list: %s", e)
            return []

        available_models = []
        test_messages = [{"role": "user", "content": TEST_PROMPT}]

        for i, model in enumerate(all_ids):
            logger.info("Testing model %d/%d: %s", i + 1, len(all_ids), model)
            await self.rate_limiter.acquire()
            try:
                start_t = time.time()
                await self._client.chat.completions.create(
                    model=model,
                    messages=test_messages,
                    max_tokens=100,
                    timeout=20.0
                )
                duration = time.time() - start_t
                logger.info("✅ Model %s is working. Speed: %.2fs", model, duration)
                available_models.append({"id": model, "speed": round(duration, 2)})
            except Exception as e:
                error_msg = str(e).lower()
                logger.info("❌ Model %s failed: %s", model, str(e)[:100])
                if "timeout" in error_msg or "timed out" in error_msg:
                    logger.info("⚠️ Model %s timed out in pass 1, marking for pass 2.", model)
                    available_models.append({"id": model, "speed": 999})
            
            await asyncio.sleep(2.0)

        # 第二轮：深度测速 (Deep Pass)
        pending_models = [m for m in available_models if m["speed"] == 999]
        if pending_models:
            logger.info("Starting Deep Pass for %d timed-out models...", len(pending_models))
            for i, m in enumerate(pending_models):
                model = m["id"]
                logger.info("Deep testing model %d/%d: %s", i + 1, len(pending_models), model)
                await self.rate_limiter.acquire()
                try:
                    start_t = time.time()
                    await self._client.chat.completions.create(
                        model=model,
                        messages=test_messages,
                        max_tokens=100,
                        timeout=120.0  # 极限宽容度
                    )
                    duration = time.time() - start_t
                    logger.info("✅ Model %s passed Deep Test. Speed: %.2fs", model, duration)
                    m["speed"] = round(duration, 2)
                except Exception as e:
                    logger.info("❌ Model %s failed Deep Test (timeout again): %s", model, str(e)[:100])
                
                await asyncio.sleep(2.0)

        # 保存到文件
        if available_models:
            # 排序逻辑同 fetch_models
            rec_ids = RECOMMENDED_MODELS
            rec = [m for m in available_models if m["id"] in rec_ids]
            rec.sort(key=lambda x: rec_ids.index(x["id"]))
            
            others = [m for m in available_models if m["id"] not in rec_ids]
            others.sort(key=lambda x: x["speed"])
            
            final_list = rec + others
            
            try:
                with open(AVAILABLE_MODELS_FILE, "w", encoding="utf-8") as f:
                    json.dump(final_list, f, ensure_ascii=False, indent=2)
                logger.info("Saved %d working models with speed data.", len(final_list))
                self._models_cache = final_list
                self._models_cache_time = time.time()
            except Exception as e:
                logger.error("Failed to save available models: %s", e)
        
        return final_list if available_models else []

    # ------------------------------------------------------------------
    # 聊天
    # ------------------------------------------------------------------
    async def chat(self, model: str, messages: list[dict]) -> str:
        """发送聊天请求，返回回复文本。内置重试和错误处理。"""
        logger.info("Acquiring rate limit for model: %s", model)
        await self.rate_limiter.acquire()
        logger.info("Rate limit acquired, starting chat request...")

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                start_time = time.time()
                logger.info("Sending request to NVIDIA API (model: %s)...", model)
                response = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1024,
                    timeout=REQUEST_TIMEOUT,  # 显式设置超时
                )
                duration = time.time() - start_time
                logger.info("NVIDIA API response received in %.2fs.", duration)
                # 提取回复文本 — 处理各种返回格式
                return self._extract_content(response, model)

            except Exception as e:
                error_msg = str(e)
                logger.error(
                    "Chat error (attempt %d/%d): %s",
                    attempt + 1, max_retries + 1, error_msg,
                )
                if attempt < max_retries:
                    wait = 5 if ("429" in error_msg) else 3
                    await asyncio.sleep(wait)
                    # 重试前重新获取速率许可
                    await self.rate_limiter.acquire()
                    continue

                # 所有重试都失败了，返回友好错误信息
                return self._friendly_error(error_msg, model)

        return "❌ 未知错误，请重试。"

    async def chat_with_image(self, model: str, system_prompt: str, text: str, base64_image: str) -> str:
        """发送图片+文本请求。"""
        await self.rate_limiter.acquire()
        
        try:
            logger.info("Sending image request to NVIDIA API (model: %s)...", model)
            start_time = time.time()
            
            response = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                            },
                        ],
                    },
                ],
                max_tokens=1024,
                timeout=REQUEST_TIMEOUT,
            )
            
            duration = time.time() - start_time
            logger.info("NVIDIA API image response received in %.2fs.", duration)
            return self._extract_content(response, model)
            
        except Exception as e:
            logger.error("Image chat error: %s", e)
            return self._friendly_error(str(e), model)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_content(response, model: str) -> str:
        """从各种可能的响应格式中提取文本。"""
        try:
            if response.choices and len(response.choices) > 0:
                choice = response.choices[0]
                msg = getattr(choice, "message", None)
                if msg:
                    # 标准 content 字段
                    if msg.content:
                        return msg.content.strip()
                    # 部分模型（如 DeepSeek-R1）使用 reasoning_content
                    rc = getattr(msg, "reasoning_content", None)
                    if rc:
                        return rc.strip()
        except Exception as ex:
            logger.warning("Content extraction failed: %s", ex)

        return f"⚠️ 模型 `{model}` 返回了空回复，请重试或切换模型。"

    @staticmethod
    def _friendly_error(error_msg: str, model: str) -> str:
        """把 API 异常转为用户友好的中文提示。"""
        low = error_msg.lower()
        if "404" in error_msg:
            return f"❌ 模型 `{model}` 不存在或不支持聊天，请用 /model 切换。"
        if "429" in error_msg or "rate" in low:
            return "⏳ API 限流，请稍等几秒后重试。"
        if "timeout" in low or "timed out" in low:
            return "⏳ 模型响应超时，请重试或切换到更快的模型。"
        if "400" in error_msg:
            return f"❌ 请求错误（模型可能不兼容）：\n`{error_msg[:200]}`"
        # 通用错误
        return f"❌ API 错误：\n`{error_msg[:300]}`"

    # ------------------------------------------------------------------
    async def close(self):
        """关闭底层 HTTP 客户端。"""
        await self._http_client.aclose()
