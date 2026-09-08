"""Telegram 命令和消息处理器"""

import re
import logging
import html
import base64
import io
import asyncio
from datetime import datetime, timedelta, time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

from config import (
    SYSTEM_PROMPT,
    MAX_HISTORY,
    ALLOWED_USER_IDS,
    DEFAULT_MODEL,
    VISION_MODEL,
    ADMIN_USER_IDS,
    WHITELIST_FILE,
    VERIFY_MODELS,
    VOCAB_FILE,
    RECALL_PUSH_TIMES,
)
from nvidia_client import NvidiaClient
import database
import edge_tts
import subprocess
import os
import json
import tempfile

logger = logging.getLogger(__name__)

_FILLER_WORDS = frozenset({
    "the", "and", "but", "for", "not", "you", "all", "can", "had", "her",
    "was", "one", "our", "out", "are", "has", "have", "this", "that", "with",
    "from", "they", "been", "said", "each", "which", "their", "will", "other",
    "about", "many", "then", "them", "these", "some", "would", "make", "like",
    "into", "time", "very", "when", "come", "could", "than", "more", "what",
    "does", "also", "just", "know", "take", "people", "your", "how", "well",
    "tell", "give", "good", "back", "only", "going", "think", "help", "being",
    "really", "still", "should", "where", "much", "need", "want", "because",
})


def _extract_keyword(text: str) -> str | None:
    words = re.findall(r'[a-zA-Z]{3,}', text)
    for w in words:
        low = w.lower()
        if low not in _FILLER_WORDS:
            return w
    return None


# ======================================================================
# TTS 语音生成助手
# ======================================================================
async def _generate_tts(text: str) -> str:
    """生成语音文件并返回路径 (OGG 格式)"""
    clean_text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    clean_text = re.sub(r'[\u2600-\u27bf]', '', clean_text)
    clean_text = clean_text.replace("*", "").replace("#", "").replace("`", "")

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_file:
        mp3_path = mp3_file.name

    has_chinese = any('\u4e00' <= char <= '\u9fff' for char in clean_text)
    voice = "zh-CN-XiaoxiaoNeural" if has_chinese else "en-US-AvaNeural"

    try:
        communicate = edge_tts.Communicate(clean_text, voice)
        await communicate.save(mp3_path)

        ogg_path = mp3_path.replace(".mp3", ".ogg")
        subprocess.run([
            "ffmpeg", "-y", "-i", mp3_path,
            "-c:a", "libopus", "-b:a", "64k",
            ogg_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(mp3_path):
            os.remove(mp3_path)
        return ogg_path
    except Exception as e:
        logger.error("TTS or FFmpeg failed: %s", e)
        return mp3_path if os.path.exists(mp3_path) else ""


def _clean_reply(text: str) -> str:
    """清理回复中的不合规符号，把列表星号或减号换成圆点。"""
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        new_line = re.sub(r"^(\s*)[\*\-]\s+", r"\1• ", line)
        cleaned_lines.append(new_line)
    return "\n".join(cleaned_lines)


MODELS_PER_PAGE = 8
_cached_models: list[dict] = []          # 模型列表快照 (id, speed)


async def _check_user(user_id: int) -> bool:
    """Check if user is admin or in database whitelist."""
    if user_id in ADMIN_USER_IDS:
        return True

    whitelist = await database.get_whitelist()
    if user_id in whitelist:
        return True

    if user_id in ALLOWED_USER_IDS:
        return True

    if not ADMIN_USER_IDS and not ALLOWED_USER_IDS and not whitelist:
        return True
    return False


def _is_admin(user_id: int) -> bool:
    if not ADMIN_USER_IDS:
        return True
    return user_id in ADMIN_USER_IDS


async def _get_model(uid: int, nvidia: NvidiaClient | None = None) -> str:
    default = await nvidia.get_default_model() if nvidia else DEFAULT_MODEL
    model = await database.get_user_model(uid, default)
    if nvidia and not await nvidia.is_model_available(model):
        new_model = await nvidia.get_default_model()
        logger.warning("User %d model %s is no longer in available list. Auto-switching to %s", uid, model, new_model)
        await database.set_user_model(uid, new_model)
        return new_model
    return model


async def _chat_with_auto_failover(nvidia: NvidiaClient, uid: int, messages: list[dict]) -> str:
    """智能调用聊天：遇 404/410/限流/超时自动切换备用模型，保障24小时不掉线"""
    model = await _get_model(uid, nvidia)
    reply = await nvidia.chat(model, messages)

    low = reply.lower()
    is_failed = (
        reply.strip().startswith("❌") or reply.strip().startswith("⏳") or
        "404" in reply or "410" in reply or "429" in reply or "限流" in reply or
        "不存在" in reply or "不支持聊天" in reply or "已下线" in reply or
        "end of life" in low or "no longer available" in low or "gone" in low or
        "api 错误" in low or "error code" in low
    )

    if is_failed:
        backup_candidates = [
            "minimaxai/minimax-m3",
            "google/gemma-4-31b-it",
            "moonshotai/kimi-k3",
            "openai/gpt-oss-20b",
        ]
        fallback = None
        for cand in backup_candidates:
            if cand != model and await nvidia.is_model_available(cand):
                fallback = cand
                break

        if not fallback:
            fallback = await nvidia.get_default_model()
            if fallback == model:
                models = await nvidia.fetch_models()
                for m in models:
                    if nvidia.is_chat_model(m["id"]) and m["id"] != model:
                        fallback = m["id"]
                        break

        if fallback and fallback != model:
            logger.warning("Model %s failed (%s) for user %d, auto-failing over to %s", model, reply[:60], uid, fallback)
            await database.set_user_model(uid, fallback)
            reply = await nvidia.chat(fallback, messages)
            if not reply.strip().startswith("❌") and not reply.strip().startswith("⏳"):
                reply += f"\n\n*(💡 提示：原模型请求受限，外教已自动为你无缝切换至稳定模型 `{fallback}`)*"

    return reply


async def _get_history(uid: int) -> list[dict]:
    return await database.get_history(uid, MAX_HISTORY * 2)


def _build_model_kb(models: list[dict], page: int) -> InlineKeyboardMarkup:
    total = max(1, -(-len(models) // MODELS_PER_PAGE))
    page = max(0, min(page, total - 1))
    start = page * MODELS_PER_PAGE
    end = min(start + MODELS_PER_PAGE, len(models))

    rows: list[list[InlineKeyboardButton]] = []
    for i in range(start, end):
        m = models[i]
        mid = m["id"]
        short_name = mid.split("/")[-1] if "/" in mid else mid
        rows.append([InlineKeyboardButton(short_name, callback_data=f"ms:{i}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"mp:{page - 1}"))
    nav.append(InlineKeyboardButton(f"📄 {page + 1}/{total}", callback_data="noop"))
    if page < total - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"mp:{page + 1}"))
    rows.append(nav)

    return InlineKeyboardMarkup(rows)


# ======================================================================
# /start
# ======================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await _check_user(user.id):
        await update.message.reply_text(
            f"⛔ 你没有权限使用此 Bot。\n"
            f"🆔 你的 User ID: `{user.id}`\n"
            f"请联系管理员添加白名单。",
            parse_mode="Markdown",
        )
        return

    is_admin = _is_admin(user.id)
    admin_badge = " 👑管理员" if is_admin else ""

    text = (
        f"👋 你好 猫娘！{admin_badge}\n\n"
        f"我是你的私人英语外教 Bot，由 NVIDIA NIM API 驱动。\n"
        f"支持 70+ 模型切换、艾宾浩斯遗忘曲线智能复习、图片识别翻译。\n\n"
        f"💬 *学习与互动*\n"
        f"• 发任何文字 → 中英双语对话\n"
        f"• 发一个生词 → 3 个搞笑造句助记\n"
        f"• 发一张英语图片 → 翻译 + 语法词汇讲解\n"
        f"• 每次推送附带自评按钮，自动计算下次最佳复习时间\n\n"
        f"🛠 *常用命令*\n"
        f"/stats — 查看个人学习看板 (进度/连续打卡/复习库)\n"
        f"/recall — 立即开始学习 (优先推送待复习词汇)\n"
        f"/pause — 调整推送频次或暂停 (避免打扰)\n"
        f"/model — 浏览并切换 AI 模型（带实时测速）\n"
        f"/current — 查看当前模型\n"
        f"/reset — 清空对话历史\n"
        f"/verify — 交叉校验上一条 AI 回复（消除幻觉）\n"
        f"/help — 查看详细指南\n"
    )
    if is_admin:
        text += (
            f"\n👑 *管理员命令*\n"
            f"/adduser `<user_id>` — 添加白名单\n"
            f"/removeuser `<user_id>` — 移除白名单\n"
            f"/users — 查看当前白名单\n"
            f"/check\\_models — 手动触发全量模型测速\n"
        )
    nvidia: NvidiaClient = context.bot_data.get("nvidia")
    current_model = await _get_model(user.id, nvidia)
    await database.set_user_model(user.id, current_model)

    text += (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"🆔 你的 User ID: `{user.id}`\n"
        f"🤖 当前模型: `{current_model}`\n"
        f"⏰ 每日推送时间: {', '.join(RECALL_PUSH_TIMES)}"
    )

    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(text, parse_mode=None)


# ======================================================================
# /help
# ======================================================================
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return
    await update.message.reply_text(
        "🎓 *英语外教 Bot 使用指南*\n\n"
        "💬 *聊天模式*\n"
        "• 发任意中文/英文消息，我会用中英双语和你对话\n"
        "• 发一个英语单词，我会造 3 个搞笑句子帮你深度记忆\n\n"
        "📷 *图片模式*\n"
        "• 发一张英语图片/截图，我会自动识别、翻译和拆解考点\n\n"
        "🧠 *科学记忆（SM-2 遗忘曲线）*\n"
        "• 每次推送单词后，点击下方的【记住了/模糊/忘了】完成打卡\n"
        "• 系统会根据你的反馈自动推算最佳复习间隔，到期主动提醒\n\n"
        "🛠 *全部命令*\n"
        "/stats — 学习看板（打卡天数、复习池、词库总进度）\n"
        "/recall — 手动触发一次单词学习/复习\n"
        "/pause — 推送压力调节（跳过今天、暂停N天、改仅晚上推）\n"
        "/speak `<文字>` — 手动语音朗读（不带参数则朗读上条AI回复）\n"
        "/model — 浏览并切换 AI 模型\n"
        "/current — 查看当前使用的模型\n"
        "/reset — 清空对话记录\n"
        "/system — 查看当前 System Prompt\n"
        "/verify — 交叉校验上一条 AI 回复\n",
        parse_mode="Markdown",
    )


# ======================================================================
# /current
# ======================================================================
async def cmd_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return
    uid = update.effective_user.id
    nvidia: NvidiaClient = context.bot_data.get("nvidia")
    model = await _get_model(uid, nvidia)
    history = await _get_history(uid)
    await update.message.reply_text(
        f"🤖 当前模型: `{model}`\n"
        f"💬 对话历史: {len(history)} 条消息",
        parse_mode="Markdown",
    )


# ======================================================================
# /reset
# ======================================================================
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return
    uid = update.effective_user.id
    await database.clear_history(uid)
    await update.message.reply_text("🗑 对话历史已清空！")


# ======================================================================
# /system
# ======================================================================
async def cmd_system(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return
    await update.message.reply_text(
        f"📋 *当前 System Prompt:*\n\n{SYSTEM_PROMPT}",
        parse_mode="Markdown",
    )


# ======================================================================
# /verify — 交叉校验
# ======================================================================
async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return

    uid = update.effective_user.id
    history = await database.get_history(uid, 5)
    if not history:
        await update.message.reply_text("❌ 当前没有对话历史，无法校验。")
        return

    last_assistant_msg = None
    for msg in reversed(history):
        if msg["role"] == "assistant":
            last_assistant_msg = msg["content"]
            break

    if not last_assistant_msg:
        await update.message.reply_text("❌ 未找到 AI 的回复记录，无法校验。")
        return

    msg = await update.message.reply_text("🔍 正在调用多个不同架构的模型进行交叉校验，请稍候...")
    await update.message.chat.send_action(ChatAction.TYPING)

    nvidia: NvidiaClient = context.bot_data["nvidia"]

    verify_prompt = (
        "你是一位精通中英双语的资深英语专家。请你对下面这段 AI 给出的英语教学回复进行交叉校验。\n\n"
        "【待校验回复】：\n"
        f"{last_assistant_msg}\n\n"
        "【任务】：\n"
        "1. 检查是否存在事实性错误（尤其是英语语法、单词释义）。\n"
        "2. 检查是否存在 AI 幻觉（瞎编乱造）。\n"
        "3. 给出简短、客观的评价（中文）。\n\n"
        "如果回复完全正确，请回复：✅ 经校验，该回复准确无误。\n"
        "如果有误，请指出错误所在。"
    )

    current_model = await _get_model(uid, nvidia)
    verify_models = await nvidia.get_verify_models(exclude=current_model)

    tasks = []
    for model_id in verify_models:
        messages = [{"role": "user", "content": verify_prompt}]
        tasks.append(nvidia.chat(model_id, messages))

    responses = await asyncio.gather(*tasks)

    results = []
    for i, res in enumerate(responses):
        model_name = verify_models[i].split("/")[-1]
        results.append(f"🤖 *{model_name}*:\n{res}")

    final_text = "⚖️ *多模型交叉校验结果 (Consensus Mode)*\n\n" + "\n\n---\n\n".join(results)

    await msg.delete()
    try:
        await update.message.reply_text(final_text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(final_text, parse_mode=None)


# ======================================================================
# /stats — 学习数据看板
# ======================================================================
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _check_user(uid):
        return

    total_vocab = 5651
    if os.path.exists(VOCAB_FILE):
        try:
            with open(VOCAB_FILE, "r", encoding="utf-8") as f:
                vocab = json.load(f)
                total_vocab = len(vocab)
        except Exception:
            pass

    stats = await database.get_stats(uid, total_vocab=total_vocab)
    model = await _get_model(uid)

    mode_map = {
        "both": "☀️ 早晚均推 (09:00 & 20:00)",
        "evening_only": "🌙 仅晚上推 (20:00)"
    }
    mode_str = mode_map.get(stats["push_mode"], stats["push_mode"])
    pause_str = f"⏸️ 暂停至 {stats['pause_until'][:16]}" if stats["pause_until"] else "✅ 正常推送中"
    percent = round((stats["word_index"] / stats["total_vocab"]) * 100, 1) if stats["total_vocab"] else 0

    text = (
        "📊 *你的 CET-6 学习数据看板*\n\n"
        f"🔥 *连续学习*：`{stats['streak']}` 天\n"
        f"📖 *新词进度*：`{stats['word_index']} / {stats['total_vocab']}` 词 ({percent}%)\n\n"
        "🧠 *记忆库状态 (SM-2 遗忘曲线)*\n"
        f"• 已加入复习池：`{stats['in_schedule']}` 词\n"
        f"• 稳固掌握 (复习≥3次)：`{stats['mastered']}` 词\n"
        f"• 今日待复习：`{stats['due_count']}` 词\n\n"
        "⚙️ *当前设置*\n"
        f"• 每日推送：`{mode_str}`\n"
        f"• 状态：`{pause_str}`\n"
        f"• 当前模型：`{model}`\n\n"
        "💡 *提示*：每次推送词汇后，点击下方的自评按钮可自动打卡并安排最佳复习时间！"
    )

    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(text, parse_mode=None)


# ======================================================================
# 核心业务：发送学习/复习词汇
# ======================================================================
async def _send_recall_content(context: ContextTypes.DEFAULT_TYPE, uid: int, chat_id: int, is_scheduled: bool = False):
    """统一的词汇推送逻辑：优先检查到期复习词，若无则推新词。"""
    if not os.path.exists(VOCAB_FILE):
        await context.bot.send_message(chat_id=chat_id, text="❌ 词库文件不存在。")
        return

    with open(VOCAB_FILE, "r", encoding="utf-8") as f:
        vocab = json.load(f)

    nvidia: NvidiaClient = context.bot_data["nvidia"]

    # 0. 方案B（打卡阻断法）：检查是否有上一生词尚未自评
    pending_word = await database.get_pending_eval_word(uid)
    if pending_word:
        logger.info("User %d has pending evaluation word '%s', blocking next push.", uid, pending_word)
        remind_buttons = [
            [
                InlineKeyboardButton("🔊 听单词发音", callback_data=f"tts_word:{pending_word}"),
            ],
            [
                InlineKeyboardButton("✅ 记住了", callback_data=f"rgr:{pending_word}:good"),
                InlineKeyboardButton("🤔 模糊", callback_data=f"rgr:{pending_word}:fuzzy"),
                InlineKeyboardButton("❌ 忘了", callback_data=f"rgr:{pending_word}:forgot"),
            ],
        ]
        if is_scheduled:
            remind_buttons.append([
                InlineKeyboardButton("😴 今天够了，明天见", callback_data="pause_opt:today")
            ])

        remind_text = (
            f"🔔 *Active Recall 自评打卡提醒*\n\n"
            f"上一生词 *`{pending_word}`* 还没有完成自评噢～\n\n"
            f"请先在下方标记你的掌握程度，完成后将**立即为你解锁下一个单词**："
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=remind_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(remind_buttons)
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=remind_text,
                parse_mode=None,
                reply_markup=InlineKeyboardMarkup(remind_buttons)
            )
        return

    # 1. 优先检查是否有到期待复习的单词 (SM-2 交互式闪卡主动回忆)
    due_item = await database.get_due_review_word(uid)

    if due_item:
        word = due_item["word"]
        review_count = due_item["review_count"]

        # 方案1：闪卡测验卡片 (Progressive Active Recall)
        quiz_text = (
            f"🔁 *Active Recall: 单词复习 (第 {review_count + 1} 轮)*\n\n"
            f"💡 考考你的瞬时记忆：*`{word}`* 还记得是什么意思吗？\n\n"
            f"💬 *可以直接打字回复我测试，也可以点击下方按钮查看答案与详解👇*"
        )
        buttons = [
            [
                InlineKeyboardButton("🔊 听单词发音", callback_data=f"tts_word:{word}"),
                InlineKeyboardButton("👀 查看答案与考点详解", callback_data=f"reveal:{word}"),
            ],
            [
                InlineKeyboardButton("✅ 记住了", callback_data=f"rg:{word}:good"),
                InlineKeyboardButton("🤔 模糊", callback_data=f"rg:{word}:fuzzy"),
                InlineKeyboardButton("❌ 忘了", callback_data=f"rg:{word}:forgot"),
            ]
        ]
        if is_scheduled:
            buttons.append([
                InlineKeyboardButton("😴 今天够了，明天见", callback_data="pause_opt:today")
            ])

        keyboard = InlineKeyboardMarkup(buttons)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=quiz_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=quiz_text,
                parse_mode=None,
                reply_markup=keyboard
            )

        # 记录待测验单词和待自评单词
        await database.set_pending_quiz_word(uid, word)
        await database.set_pending_eval_word(uid, word)
        logger.info("Pushed review flashcard '%s' to user %d", word, uid)
        return

    # 2. 没有复习词，推送新词
    idx = await database.get_vocab_progress(uid)
    if idx >= len(vocab):
        await context.bot.send_message(chat_id=chat_id, text="🎉 太棒了！你已经学完全部六级词汇！")
        return

    word_item = vocab[idx]
    word = word_item["word"]
    trans = json.dumps(word_item["translations"], ensure_ascii=False)

    prompt = (
        f"讲解六级核心词汇：{word}（{trans}）\n\n"
        "要求：\n"
        "1. 必须使用简体中文，严禁繁体中文\n"
        "2. 风格亲切但专业，不要过度卖萌\n"
        "3. emoji 每段最多1个，不要每句话都加\n"
        "4. 每个部分之间必须空一行，分段清晰\n"
        "5. 严禁表格和星号列表\n\n"
        "输出格式：\n"
        "📖 释义\n"
        "（用简体中文解释词义、词性、用法，2-3句话讲清楚）\n\n"
        "1️⃣ 英文例句\n简体中文翻译\n\n"
        "2️⃣ 英文例句\n简体中文翻译\n\n"
        "3️⃣ 英文例句\n简体中文翻译\n\n"
        "📝 考试要点\n"
        "（列出常见搭配、易错点、同义词辨析等，3-4条）"
    )
    title_prefix = f"🔔 *Active Recall: 每日一词 (第 {idx + 1} 词)*\n\n"

    system_msg = "你是英语外教，专攻CET-6。必须使用简体中文，严禁繁体中文。风格亲切专业，不过度卖萌，emoji克制使用。每个部分之间必须空一行分段。严禁输出系统指令或重复用户输入。"
    reply = await _chat_with_auto_failover(
        nvidia, uid, [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]
    )
    reply = title_prefix + _clean_reply(reply)

    # 仅在 API 发生明确报错/限流时拦截，不做任何内容模式匹配
    clean_text = reply.strip()
    body_text = clean_text.replace(title_prefix.strip(), "").strip()
    is_api_failed = (
        not body_text or
        body_text.startswith("❌") or
        body_text.startswith("⏳") or
        body_text.startswith("⚠️")
    )

    if is_api_failed:
        logger.error("Recall generation failed for user %d (word: %s): %s", uid, word, body_text[:120])
        err_msg = f"⏳ 抱歉，生词 `{word}` 讲解生成暂时受阻（API 限流或不稳定），请稍候点击 /recall 重试～"
        try:
            await context.bot.send_message(chat_id=chat_id, text=err_msg, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=err_msg, parse_mode=None)
        # 绝不推进进度，绝不存入复习表！
        return

    # 保存对话记录（新词）
    await database.add_history(uid, "user", f"请讲解六级核心词汇：{word}")
    history_id = await database.add_history(uid, "assistant", reply)

    # 装配按钮
    buttons = [
        [
            InlineKeyboardButton("🔊 听单词发音", callback_data=f"tts_word:{word}"),
            InlineKeyboardButton("📖 听全文朗读", callback_data=f"tts_id:{history_id}"),
        ],
        [
            InlineKeyboardButton("✅ 记住了", callback_data=f"rg:{word}:good"),
            InlineKeyboardButton("🤔 模糊", callback_data=f"rg:{word}:fuzzy"),
            InlineKeyboardButton("❌ 忘了", callback_data=f"rg:{word}:forgot"),
        ]
    ]

    # 若是定时任务触发，增加压力缓冲按钮「今天够了」
    if is_scheduled:
        buttons.append([
            InlineKeyboardButton("😴 今天够了，明天见", callback_data="pause_opt:today")
        ])

    keyboard = InlineKeyboardMarkup(buttons)

    # 发送消息
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=reply,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id,
            text=reply,
            parse_mode=None,
            reply_markup=keyboard
        )

    # 方案B：设置当前待自评生词（锁定下一个单词的推送）
    await database.set_pending_eval_word(uid, word)

    # 录入复习库并推进游标
    await database.record_new_word(uid, word)
    await database.update_vocab_progress(uid, idx + 1)
    logger.info("Pushed new word '%s' to user %d (index %d -> %d)", word, uid, idx, idx + 1)


# ======================================================================
# /recall — 手动触发推送
# ======================================================================
async def cmd_recall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _check_user(uid):
        return

    msg = await update.message.reply_text("📖 正在为你准备单词讲解与复习...", parse_mode="Markdown")
    await update.message.chat.send_action(ChatAction.TYPING)

    await _send_recall_content(context, uid=uid, chat_id=update.effective_chat.id, is_scheduled=False)
    try:
        await msg.delete()
    except Exception:
        pass


# ======================================================================
# /pause — 暂停与推送设置
# ======================================================================
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await _check_user(uid):
        return

    is_paused = await database.is_paused(uid)
    pause_until = await database.get_pause_until(uid)
    push_mode = await database.get_user_push_mode(uid)

    mode_desc = "☀️ 早晚均推 (09:00 & 20:00)" if push_mode == "both" else "🌙 仅晚上推 (20:00)"
    status_desc = f"⏸️ 暂停至 {pause_until[:16]}" if is_paused else "✅ 正常推送中"

    text = (
        "⏸️ *推送频次与休息设置*\n\n"
        f"• 当前状态：`{status_desc}`\n"
        f"• 当前频次：`{mode_desc}`\n\n"
        "点击下方选项自由调整："
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("😴 今天跳过 (至明早8点)", callback_data="pause_opt:today"),
            InlineKeyboardButton("⏸️ 暂停 3 天", callback_data="pause_opt:3d"),
        ],
        [
            InlineKeyboardButton("⏸️ 暂停 7 天", callback_data="pause_opt:7d"),
            InlineKeyboardButton("▶️ 立即恢复推送", callback_data="pause_opt:resume"),
        ],
        [
            InlineKeyboardButton(
                "🌙 仅晚上推送 (当前生效)" if push_mode == "evening_only" else "🌙 改为仅晚上推 (20:00)",
                callback_data="pause_opt:mode_evening"
            ),
        ],
        [
            InlineKeyboardButton(
                "☀️ 早晚均推 (当前生效)" if push_mode == "both" else "☀️ 改为早晚均推 (09:00 & 20:00)",
                callback_data="pause_opt:mode_both"
            ),
        ]
    ])

    try:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await update.message.reply_text(text, parse_mode=None, reply_markup=keyboard)


# ======================================================================
# 回调：暂停与推送设置选择
# ======================================================================
async def callback_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id

    if not await _check_user(uid):
        await query.answer("无权限", show_alert=True)
        return

    opt = data.replace("pause_opt:", "")

    if opt == "today":
        # 暂停至明天早上 08:00
        now = datetime.now()
        target = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        await database.set_pause_until(uid, target)
        msg_text = f"😴 已为你跳过今日剩余推送，将于明天 08:00 恢复！好好休息～🐾"
    elif opt == "3d":
        await database.set_pause(uid, 3)
        msg_text = "⏸️ 已暂停推送 3 天，假期愉快！🐾"
    elif opt == "7d":
        await database.set_pause(uid, 7)
        msg_text = "⏸️ 已暂停推送 7 天，期待下次回来继续进步！🐾"
    elif opt == "resume":
        await database.cancel_pause(uid)
        msg_text = "▶️ 已恢复推送！外教随时为你服务～🐾"
    elif opt == "mode_evening":
        await database.set_user_push_mode(uid, "evening_only")
        msg_text = "🌙 已切换为【仅晚上推送】模式，每天 20:00 推送 1 次！"
    elif opt == "mode_both":
        await database.set_user_push_mode(uid, "both")
        msg_text = "☀️ 已切换为【早晚均推】模式，每天 09:00 和 20:00 各推送 1 次！"
    else:
        msg_text = "未知选项"

    await query.answer(msg_text, show_alert=True)

    # 更新原消息文本
    is_paused = await database.is_paused(uid)
    pause_until = await database.get_pause_until(uid)
    push_mode = await database.get_user_push_mode(uid)

    mode_desc = "☀️ 早晚均推 (09:00 & 20:00)" if push_mode == "both" else "🌙 仅晚上推 (20:00)"
    status_desc = f"⏸️ 暂停至 {pause_until[:16]}" if is_paused else "✅ 正常推送中"

    new_text = (
        "⏸️ *推送频次与休息设置*\n\n"
        f"• 当前状态：`{status_desc}`\n"
        f"• 当前频次：`{mode_desc}`\n\n"
        f"✨ *更新*：{msg_text}\n\n"
        "你可以随时点击下方选项更改："
    )

    new_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("😴 今天跳过 (至明早8点)", callback_data="pause_opt:today"),
            InlineKeyboardButton("⏸️ 暂停 3 天", callback_data="pause_opt:3d"),
        ],
        [
            InlineKeyboardButton("⏸️ 暂停 7 天", callback_data="pause_opt:7d"),
            InlineKeyboardButton("▶️ 立即恢复推送", callback_data="pause_opt:resume"),
        ],
        [
            InlineKeyboardButton(
                "🌙 仅晚上推送 (当前生效)" if push_mode == "evening_only" else "🌙 改为仅晚上推 (20:00)",
                callback_data="pause_opt:mode_evening"
            ),
        ],
        [
            InlineKeyboardButton(
                "☀️ 早晚均推 (当前生效)" if push_mode == "both" else "☀️ 改为早晚均推 (09:00 & 20:00)",
                callback_data="pause_opt:mode_both"
            ),
        ]
    ])

    try:
        await query.edit_message_text(new_text, parse_mode="Markdown", reply_markup=new_kb)
    except Exception:
        pass


# ======================================================================
# 回调：SM-2 遗忘曲线自评打卡
# ======================================================================
async def callback_review_grade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id

    if not await _check_user(uid):
        await query.answer("无权限", show_alert=True)
        return

    # 格式: rg:{word}:{grade} 或 rgr:{word}:{grade}
    parts = data.split(":")
    if len(parts) != 3:
        return
    prefix, word, grade = parts
    is_from_reminder = (prefix == "rgr")

    res = await database.update_word_review(uid, word, grade)
    interval = res["interval"]
    streak = res["streak"]

    # 方案B：清空用户的待自评单词状态
    pending = await database.get_pending_eval_word(uid)
    if pending and pending.lower() == word.lower():
        await database.set_pending_eval_word(uid, None)

    # 清空可能存在的待测验状态
    quiz_p = await database.get_pending_quiz_word(uid)
    if quiz_p and quiz_p.lower() == word.lower():
        await database.set_pending_quiz_word(uid, None)

    grade_desc = {
        "good": "记住了",
        "fuzzy": "有点模糊",
        "forgot": "完全忘了"
    }.get(grade, "")

    if is_from_reminder:
        alert_text = f"已补评【{grade_desc}】！\n正在为你解锁下一个单词～🐾"
    else:
        alert_text = f"已打卡【{grade_desc}】！\n下次复习间隔：{interval} 天后 🐾\n连续学习：{streak} 天"
    await query.answer(alert_text, show_alert=True)

    # 修改当前消息的按键，锁定自评结果，防止重复点击
    if query.message and query.message.reply_markup:
        old_kb = query.message.reply_markup.inline_keyboard
        new_kb_rows = []
        for row in old_kb:
            # 如果是自评那一行 (包含 rg: 或 rgr:)，替换为状态展示
            if any(btn.callback_data and (btn.callback_data.startswith("rg:") or btn.callback_data.startswith("rgr:")) for btn in row):
                new_kb_rows.append([
                    InlineKeyboardButton(f"✅ 已记录 ({interval}天后复习 | 🔥连续{streak}天)", callback_data="noop")
                ])
            else:
                new_kb_rows.append(row)

        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_kb_rows))
        except Exception:
            pass

    # 如果是从打卡阻断提醒完成评级，评完后立即推送当前应学单词
    if is_from_reminder:
        await _send_recall_content(context, uid=uid, chat_id=query.message.chat_id, is_scheduled=False)


# ======================================================================
# Callback: 闪卡翻牌查看答案与考点 (Progressive Active Recall)
# ======================================================================
async def callback_reveal_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id

    if not await _check_user(uid):
        await query.answer("无权限", show_alert=True)
        return

    word = data.replace("reveal:", "").strip()
    await query.answer("正在翻牌展开考点与例句...", show_alert=False)

    # 清空待回答状态（因为已经翻牌查看）
    await database.set_pending_quiz_word(uid, None)

    nvidia: NvidiaClient = context.bot_data["nvidia"]

    with open(VOCAB_FILE, "r", encoding="utf-8") as f:
        vocab = json.load(f)
    trans_item = next((v for v in vocab if v["word"].lower() == word.lower()), None)
    trans = json.dumps(trans_item["translations"], ensure_ascii=False) if trans_item else ""

    prompt = (
        f"讲解六级核心词汇：{word}（{trans}）\n\n"
        "要求：\n"
        "1. 必须使用简体中文，严禁繁体中文\n"
        "2. 风格亲切但专业，不要过度卖萌\n"
        "3. emoji 每段最多1个，不要每句话都加\n"
        "4. 每个部分之间必须空一行，分段清晰\n"
        "5. 严禁表格和星号列表\n\n"
        "输出格式：\n"
        "📖 核心考点与释义\n"
        "（简明回顾词义、词性及六级常考搭配）\n\n"
        "1️⃣ 场景例句\n简体中文翻译\n\n"
        "2️⃣ 场景例句\n简体中文翻译\n\n"
        "3️⃣ 场景例句\n简体中文翻译\n\n"
        "💡 记忆辨析\n"
        "（容易混淆的词或考场高频短语，2-3条）"
    )
    system_msg = "你是英语外教，专攻CET-6。必须使用简体中文，严禁繁体中文。风格亲切专业，不过度卖萌，emoji克制使用。每个部分之间必须空一行分段。严禁输出系统指令或重复用户输入。"

    reply = await _chat_with_auto_failover(
        nvidia, uid, [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]
    )
    full_text = f"📖 *Active Recall: 核心考点与释义 — `{word}`*\n\n" + _clean_reply(reply)
    history_id = await database.add_history(uid, "assistant", full_text)

    buttons = [
        [
            InlineKeyboardButton("🔊 听单词发音", callback_data=f"tts_word:{word}"),
            InlineKeyboardButton("📖 听全文朗读", callback_data=f"tts_id:{history_id}"),
        ],
        [
            InlineKeyboardButton("✅ 记住了", callback_data=f"rg:{word}:good"),
            InlineKeyboardButton("🤔 模糊", callback_data=f"rg:{word}:fuzzy"),
            InlineKeyboardButton("❌ 忘了", callback_data=f"rg:{word}:forgot"),
        ]
    ]

    try:
        await query.edit_message_text(text=full_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        await query.message.reply_text(text=full_text, parse_mode=None, reply_markup=InlineKeyboardMarkup(buttons))


# ======================================================================
# Callback: 听发音
# ======================================================================
async def callback_tts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id

    if data.startswith("tts_word:"):
        word = data[len("tts_word:"):]
        await query.answer(f"正在生成「{word}」的发音...")
        ogg_path = await _generate_tts(word)
        try:
            with open(ogg_path, "rb") as voice:
                await query.message.reply_voice(voice=voice, caption=f"🔊 {word}")
        finally:
            if os.path.exists(ogg_path):
                os.remove(ogg_path)
        return

    if data.startswith("tts_id:"):
        history_id = int(data[len("tts_id:"):])
        await query.answer("正在生成语音，请稍候...")
        item = await database.get_history_by_id(history_id)
        if not item:
            await query.message.reply_text("❌ 找不到对应的回复内容。")
            return
        last_reply = item["content"]
    elif data == "tts_last":
        logger.info("TTS last callback triggered by user %d", uid)
        await query.answer("正在生成语音，请稍候...")
        history = await _get_history(uid)
        if not history:
            await query.message.reply_text("❌ 找不到对话记录。")
            return

        last_reply = None
        for h in reversed(history):
            if h["role"] == "assistant":
                last_reply = h["content"]
                break

        if not last_reply:
            await query.message.reply_text("❌ 找不到 AI 的回复内容。")
            return
    else:
        return

    ogg_path = await _generate_tts(last_reply)
    try:
        with open(ogg_path, "rb") as voice:
            await query.message.reply_voice(voice=voice, caption="猫娘的发音示范 🐾")
    finally:
        if os.path.exists(ogg_path):
            os.remove(ogg_path)


# ======================================================================
# /speak — 手动语音朗读
# ======================================================================
async def cmd_speak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return

    uid = update.effective_user.id
    text = " ".join(context.args)

    if not text:
        history = await _get_history(uid)
        for h in reversed(history):
            if h["role"] == "assistant":
                text = h["content"]
                break

    if not text:
        await update.message.reply_text("❓ 请输入要朗读的文字，或确保之前有过对话历史。")
        return

    msg = await update.message.reply_text("🔊 正在为您语音朗读...")
    await update.message.chat.send_action(ChatAction.RECORD_VOICE)

    ogg_path = await _generate_tts(text)
    try:
        with open(ogg_path, "rb") as voice:
            await update.message.reply_voice(voice=voice, caption="猫娘的发音示范 🐾")
        await msg.delete()
    except Exception as e:
        logger.error("Speak command failed: %s", e)
        await update.message.reply_text(f"❌ 语音生成失败：{e}")
    finally:
        if os.path.exists(ogg_path):
            os.remove(ogg_path)


# ======================================================================
# 定时任务：主动复盘推送 (每日定时执行)
# ======================================================================
async def active_recall_job(context: ContextTypes.DEFAULT_TYPE):
    slot = context.job.data.get("slot") if (context.job and context.job.data) else None
    logger.info("Running scheduled active recall job (slot: %s)...", slot)

    uids = await database.get_all_active_users()

    for uid in uids:
        try:
            # 检查用户是否已暂停推送
            if await database.is_paused(uid):
                logger.info("User %d has paused pushes, skipping.", uid)
                continue

            # 检查推送频次设置：若用户设为仅晚上，且当前是早上，跳过
            push_mode = await database.get_user_push_mode(uid)
            if slot == "morning" and push_mode == "evening_only":
                logger.info("User %d push_mode is evening_only, skipping morning push.", uid)
                continue

            await _send_recall_content(context, uid=uid, chat_id=uid, is_scheduled=True)
            await asyncio.sleep(1.5)

        except Exception as e:
            logger.error("Failed to send recall to user %d: %s", uid, e)


# ======================================================================
# /adduser — 添加白名单（管理员）
# ======================================================================
async def cmd_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        await update.message.reply_text("⛔ 只有管理员可以执行此命令。")
        return

    if not context.args:
        await update.message.reply_text("❓ 用法：/adduser `<user_id>`\n例如：/adduser 123456789", parse_mode="Markdown")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID 必须是数字。")
        return

    await database.add_to_whitelist(target_id)
    await update.message.reply_text(f"✅ 已将 `{target_id}` 添加到白名单。", parse_mode="Markdown")


# ======================================================================
# /removeuser — 移除白名单（管理员）
# ======================================================================
async def cmd_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        await update.message.reply_text("⛔ 只有管理员可以执行此命令。")
        return

    if not context.args:
        await update.message.reply_text("❓ 用法：/removeuser `<user_id>`", parse_mode="Markdown")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID 必须是数字。")
        return

    await database.remove_from_whitelist(target_id)
    await update.message.reply_text(f"✅ 已将 `{target_id}` 从白名单移除。", parse_mode="Markdown")


# ======================================================================
# /users — 查看白名单（管理员）
# ======================================================================
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        await update.message.reply_text("⛔ 只有管理员可以执行此命令。")
        return

    admin_list = ", ".join(str(x) for x in ADMIN_USER_IDS) if ADMIN_USER_IDS else "(未配置，所有人均为管理员)"
    whitelist_set = await database.get_whitelist()
    whitelist = ", ".join(str(x) for x in whitelist_set) if whitelist_set else "(空)"

    await update.message.reply_text(
        f"👑 *管理员*:\n{admin_list}\n\n"
        f"📋 *白名单用户*:\n{whitelist}",
        parse_mode="Markdown",
    )


# ======================================================================
# /check_models — 手动测速
# ======================================================================
async def cmd_check_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return

    await update.message.reply_text("⏳ 开始后台检测全部模型可用性，由于 API 频率限制，这大约需要 3-4 分钟，请稍候...")

    nvidia: NvidiaClient = context.bot_data["nvidia"]

    async def _run_check():
        try:
            available_models = await nvidia.check_available_models()
            await update.message.reply_text(
                f"✅ 检测完成！\n\n"
                f"共发现 {len(available_models)} 个可用模型。\n"
                f"已保存最新列表，现在可以使用 /model 查看。"
            )
        except Exception as e:
            logger.error("Check models error: %s", e)
            await update.message.reply_text(f"❌ 检测过程出错: {e}")

    context.application.create_task(_run_check())


# ======================================================================
# /model — 浏览模型列表
# ======================================================================
async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return

    nvidia: NvidiaClient = context.bot_data["nvidia"]
    await update.message.reply_text("⏳ 正在获取模型列表…")

    try:
        models = await nvidia.fetch_models()
    except Exception as e:
        await update.message.reply_text(f"❌ 获取模型列表失败: {e}")
        return

    global _cached_models
    _cached_models = models

    await update.message.reply_text(
        f"📋 共 {len(models)} 个可用模型，请选择：",
        reply_markup=_build_model_kb(models, 0),
    )


# ======================================================================
# 回调：翻页 / 选择模型
# ======================================================================
async def callback_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not await _check_user(query.from_user.id):
        return

    data = query.data
    if data == "noop":
        return

    if data.startswith("mp:"):
        page = int(data.split(":")[1])
        if not _cached_models:
            await query.edit_message_text("❌ 模型列表已过期，请重新 /model")
            return
        await query.edit_message_text(
            f"📋 共 {len(_cached_models)} 个可用模型，请选择：",
            reply_markup=_build_model_kb(_cached_models, page),
        )

    elif data.startswith("ms:"):
        idx = int(data.split(":")[1])
        if not _cached_models or idx >= len(_cached_models):
            await query.edit_message_text("❌ 索引无效，请重新 /model")
            return

        model_id = _cached_models[idx]["id"]
        uid = query.from_user.id
        await database.set_user_model(uid, model_id)

        await query.edit_message_text(
            f"✅ 已切换到：`{model_id}`\n\n"
            f"对话历史已保留，继续聊天吧！",
            parse_mode="Markdown",
        )


# ======================================================================
# 普通消息 → AI 对话
# ======================================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("Received message from user %s (%d)", user.first_name, user.id)
    if not await _check_user(user.id):
        logger.warning("User %d not allowed", user.id)
        return
    if not update.message or not update.message.text:
        return

    uid = user.id
    text = update.message.text.strip()
    if not text:
        return

    # 方案1：检查用户是否有正在进行闪卡测验的单词 (Progressive Active Recall 自然语言作答)
    quiz_word = await database.get_pending_quiz_word(uid)
    if quiz_word:
        await database.set_pending_quiz_word(uid, None)

        with open(VOCAB_FILE, "r", encoding="utf-8") as f:
            vocab = json.load(f)
        trans_item = next((v for v in vocab if v["word"].lower() == quiz_word.lower()), None)
        trans = json.dumps(trans_item["translations"], ensure_ascii=False) if trans_item else ""

        await update.message.chat.send_action(ChatAction.TYPING)
        eval_prompt = (
            f"学生正在复习大学英语六级核心词汇：{quiz_word}（参考释义：{trans}）。\n"
            f"学生给出的回答是：\"{text}\"\n\n"
            "任务：\n"
            "1. 请亲切点评学生的回答（答对了热情夸奖肯定；有偏差或答错则给出温和纠正与地道释义）。\n"
            "2. 简短补充 1 个与该词相关的六级高频用法或地道例句。\n"
            "3. 保持简短精炼（150字以内），严禁使用表格，严禁使用星号（*）做列表。"
        )
        system_msg = "你是专业的大学英语六级（CET-6）英语外教，善于鼓励学生，教学风格幽默亲切，使用简体中文。"
        eval_reply = await _chat_with_auto_failover(
            nvidia, uid, [{"role": "system", "content": system_msg}, {"role": "user", "content": eval_prompt}]
        )
        eval_reply = f"🎓 *外教闪测点评 — `{quiz_word}`*\n\n" + _clean_reply(eval_reply)
        await database.add_history(uid, "user", f"[闪测回答: {quiz_word}] {text}")
        history_id = await database.add_history(uid, "assistant", eval_reply)

        buttons = [
            [
                InlineKeyboardButton("🔊 听单词发音", callback_data=f"tts_word:{quiz_word}"),
                InlineKeyboardButton("📖 听全文朗读", callback_data=f"tts_id:{history_id}"),
            ],
            [
                InlineKeyboardButton("✅ 记住了", callback_data=f"rg:{quiz_word}:good"),
                InlineKeyboardButton("🤔 模糊", callback_data=f"rg:{quiz_word}:fuzzy"),
                InlineKeyboardButton("❌ 忘了", callback_data=f"rg:{quiz_word}:forgot"),
            ]
        ]
        try:
            await update.message.reply_text(eval_reply, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            await update.message.reply_text(eval_reply, parse_mode=None, reply_markup=InlineKeyboardMarkup(buttons))
        return

    nvidia: NvidiaClient = context.bot_data["nvidia"]
    model = await _get_model(uid)
    history = await _get_history(uid)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": text}]

    await update.message.chat.send_action(ChatAction.TYPING)

    reply = await _chat_with_auto_failover(nvidia, uid, messages)
    reply = _clean_reply(reply)
    logger.info("AI reply for user %d: %s", uid, reply[:100] + "...")

    await database.add_history(uid, "user", text)
    history_id = await database.add_history(uid, "assistant", reply)

    keyword = _extract_keyword(text)
    buttons = []
    if keyword:
        buttons.append(InlineKeyboardButton("🔊 听单词发音", callback_data=f"tts_word:{keyword}"))
    buttons.append(InlineKeyboardButton("📖 听全文朗读", callback_data=f"tts_id:{history_id}"))
    keyboard = InlineKeyboardMarkup([buttons])

    try:
        await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        try:
            await update.message.reply_text(reply, parse_mode=None, reply_markup=keyboard)
        except Exception as e:
            logger.error("Failed to send reply: %s", e)
            await update.message.reply_text("❌ 发送回复失败，请重试。")


# ======================================================================
# 图片消息 → 翻译与讲解
# ======================================================================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await _check_user(user.id):
        return

    uid = user.id  # 修复 bug：定义 uid
    msg = await update.message.reply_text("📸 收到图片，正在识别其中的英语内容...")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        photo_file = await update.message.photo[-1].get_file()
        image_bytearray = await photo_file.download_as_bytearray()
        base64_image = base64.b64encode(image_bytearray).decode('utf-8')

        nvidia: NvidiaClient = context.bot_data["nvidia"]
        raw_text = update.message.caption or "请翻译并讲解这张图片中的英语内容，重点标注六级词汇和语法点。"

        user_text = (
            f"用户留言：{raw_text}\n\n"
            "任务：请作为猫娘外教，翻译并讲解这张图片中的英语内容。\n"
            "要求：\n"
            "1. 使用中英双语，先给完整的英文讲解，再给中文翻译。\n"
            "2. 重点标注图片中出现的 **六级核心词汇**。\n"
            "3. 解释图片涉及的「语法点」。\n"
            "4. 严禁生成表格，禁止使用星号（*）做列表。\n"
            "5. 最后列出 2-3 个最重要的单词详解。"
        )

        reply = await nvidia.chat_with_image(
            model=VISION_MODEL,
            system_prompt=SYSTEM_PROMPT,
            text=user_text,
            base64_image=base64_image
        )
        reply = _clean_reply(reply)

        await database.add_history(uid, "user", f"[图片分析] {raw_text}")
        history_id = await database.add_history(uid, "assistant", reply)

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📖 听全文朗读", callback_data=f"tts_id:{history_id}")
        ]])

        await msg.delete()
        try:
            await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            await update.message.reply_text(reply, parse_mode=None, reply_markup=keyboard)

    except Exception as e:
        logger.error("Handle photo error: %s", e)
        await msg.edit_text(f"❌ 图片处理失败: {e}")
