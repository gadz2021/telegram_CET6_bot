"""速率限制器 — 滑动窗口算法，限制每分钟请求数"""

import asyncio
import time
from collections import deque


class RateLimiter:
    """滑动窗口速率限制器。在达到限制时自动等待。"""

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """获取一个请求许可。如果超过限额，自动等待直到有空位。"""
        while True:
            wait_time = 0
            async with self._lock:
                now = time.monotonic()
                # 清除窗口外的旧时间戳
                while self.timestamps and self.timestamps[0] <= now - self.window_seconds:
                    self.timestamps.popleft()

                if len(self.timestamps) < self.max_requests:
                    self.timestamps.append(now)
                    return
                
                # 需要等待：算出最早的时间戳何时过期
                wait_time = self.timestamps[0] + self.window_seconds - now
            
            if wait_time > 0:
                await asyncio.sleep(wait_time)
