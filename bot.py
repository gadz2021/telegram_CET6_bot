#!/usr/bin/env python3
"""Telegram 英语外教 Bot — 主入口

使用 NVIDIA NIM API 驱动，支持 60+ 模型动态切换。
"""

import logging
import os
import sys

# ------------------------------------------------------------------
# 代理设置（必须在所有网络库之前设置）
# ------------------------------------------------------------------
from config import PROXY_URL

if PROXY_URL:
    os.environ.setdefault("http_proxy", PROXY_URL)
    os.environ.setdefault("https_proxy", PROXY_URL)
    os.environ.setdefault("HTTP_PROXY", PROXY_URL)
    os.environ.setdefault("HTTPS_PROXY", PROXY_URL)

# ------------------------------------------------------------------

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import TELEGRAM_BOT_TOKEN, RATE_LIMIT_PER_MINUTE, CHECK_INTERVAL, RECALL_INTERVAL
from rate_limiter import RateLimiter
from nvidia_client import NvidiaClient
from handlers import (
    cmd_start,
    cmd_help,
    cmd_current,
    cmd_reset,
    cmd_system,
    cmd_model,
    cmd_check_models,
    cmd_adduser,
    cmd_removeuser,
    cmd_users,
    cmd_verify,
    cmd_recall,
    cmd_speak,
    active_recall_job,
    callback_model,
    callback_tts,
    handle_message,
    handle_photo,
)

# ------------------------------------------------------------------
# 日志
# ------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def on_startup(application):
    """Bot 启动时的初始化任务"""
    from database import init_db, migrate_json_whitelist
    from config import WHITELIST_FILE
    
    logger.info("Initializing database...")
    await init_db()
    await migrate_json_whitelist(WHITELIST_FILE)
    logger.info("Database ready.")


def main():
    logger.info("Starting Telegram English Tutor Bot...")
    if PROXY_URL:
        logger.info("Using proxy: %s", PROXY_URL)

    # 初始化速率限制器和 NVIDIA 客户端
    rate_limiter = RateLimiter(max_requests=RATE_LIMIT_PER_MINUTE)
    nvidia = NvidiaClient(rate_limiter)

    # 构建 Telegram Application
    builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN)
    builder.post_init(on_startup)
    builder.concurrent_updates(True)

    # PTB v20+ 通过 httpx 使用环境变量中的代理，无需额外配置
    application = builder.build()

    # 把 nvidia 客户端存入 bot_data，供 handler 使用
    application.bot_data["nvidia"] = nvidia

    # 注册命令处理器
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("model", cmd_model))
    application.add_handler(CommandHandler("current", cmd_current))
    application.add_handler(CommandHandler("reset", cmd_reset))
    application.add_handler(CommandHandler("system", cmd_system))
    application.add_handler(CommandHandler("check_models", cmd_check_models))
    application.add_handler(CommandHandler("adduser", cmd_adduser))
    application.add_handler(CommandHandler("removeuser", cmd_removeuser))
    application.add_handler(CommandHandler("users", cmd_users))
    application.add_handler(CommandHandler("verify", cmd_verify))
    application.add_handler(CommandHandler("recall", cmd_recall))
    application.add_handler(CommandHandler("speak", cmd_speak))

    # 配置后台定时任务：每 24 小时 (86400秒) 执行一次检测
    async def run_daily_check(context):
        logger.info("Running daily background model check...")
        try:
            await nvidia.check_available_models()
        except Exception as e:
            logger.error("Daily background check failed: %s", e)

    if application.job_queue:
        application.job_queue.run_repeating(run_daily_check, interval=CHECK_INTERVAL, first=10)
        application.job_queue.run_repeating(active_recall_job, interval=RECALL_INTERVAL, first=30)
        logger.info("Job queue enabled: model check every %ds, recall every %ds", CHECK_INTERVAL, RECALL_INTERVAL)
    else:
        logger.warning("Job queue is None! Install APScheduler: pip install APScheduler")

    # 注册回调处理器（模型选择的 inline keyboard）
    application.add_handler(CallbackQueryHandler(callback_model, pattern="^(ms:|mp:|noop)"))
    application.add_handler(CallbackQueryHandler(callback_tts, pattern="^tts_"))

    # 注册图片处理器
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # 注册普通文本消息处理器（放在最后，作为 fallback）
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # 启动轮询
    logger.info("Bot is running! Press Ctrl+C to stop.")
    application.run_polling(
        drop_pending_updates=True,  # 忽略离线期间的消息
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
