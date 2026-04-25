"""Telegram 命令和消息处理器"""

import logging
import html
import base64
import io
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

from config import SYSTEM_PROMPT, MAX_HISTORY, ALLOWED_USER_IDS, DEFAULT_MODEL, VISION_MODEL, ADMIN_USER_IDS, WHITELIST_FILE, VERIFY_MODELS, VOCAB_FILE
from nvidia_client import NvidiaClient
import database
import edge_tts
import subprocess
import os
import json
import tempfile
import re

logger = logging.getLogger(__name__)

# ======================================================================
# TTS 语音生成助手
# ======================================================================
async def _generate_tts(text: str) -> str:
    """生成语音文件并返回路径 (OGG 格式)"""
    # 过滤掉一些不适合阅读的 Markdown 符号
    clean_text = text.replace("*", "").replace("#", "").replace("`", "")
    
    # 使用 edge-tts 生成 MP3
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_file:
        mp3_path = mp3_file.name
    
    # 优先选择女声，比较符合猫娘人设
    has_chinese = any('\u4e00' <= char <= '\u9fff' for char in clean_text)
    voice = "zh-CN-XiaoxiaoNeural" if has_chinese else "en-US-AvaNeural"
    
    try:
        communicate = edge_tts.Communicate(clean_text, voice)
        await communicate.save(mp3_path)
        
        # 转换为 OGG (OPUS) 格式以适应 Telegram
        ogg_path = mp3_path.replace(".mp3", ".ogg")
        # ffmpeg -i input.mp3 -c:a libopus output.ogg
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
        # 如果失败了，尝试直接返回 mp3_path (如果它存在)
        return mp3_path if os.path.exists(mp3_path) else ""


def _clean_reply(text: str) -> str:
    """清理回复中的不合规符号，比如把列表星号换成圆点。"""
    # 匹配行首的星号或减号列表（例如 "* " 或 " - "）
    # 但要保留 **粗体**
    # 使用正则替换行首的星号列表符号为圆点
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        # 如果是 "* " 开头（忽略前面的空格），替换为 "• "
        new_line = re.sub(r"^(\s*)[\*\-]\s+", r"\1• ", line)
        cleaned_lines.append(new_line)
    return "\n".join(cleaned_lines)

MODELS_PER_PAGE = 8

# ======================================================================
# 全局状态（仅缓存）
# ======================================================================
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
    # 如果还没有配置任何管理员，允许所有人（方便初次部署）
    if not ADMIN_USER_IDS and not ALLOWED_USER_IDS and not whitelist:
        return True
    return False


def _is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    if not ADMIN_USER_IDS:
        return True  # 未配置管理员时，所有人都是管理员
    return user_id in ADMIN_USER_IDS


async def _get_model(uid: int) -> str:
    return await database.get_user_model(uid, DEFAULT_MODEL)


async def _get_history(uid: int) -> list[dict]:
    return await database.get_history(uid, MAX_HISTORY * 2)


# ======================================================================
# 模型选择键盘
# ======================================================================
def _build_model_kb(models: list[dict], page: int) -> InlineKeyboardMarkup:
    total = max(1, -(-len(models) // MODELS_PER_PAGE))  # ceil div
    page = max(0, min(page, total - 1))
    start = page * MODELS_PER_PAGE
    end = min(start + MODELS_PER_PAGE, len(models))

    rows: list[list[InlineKeyboardButton]] = []
    for i in range(start, end):
        m = models[i]
        mid = m["id"]
        speed = m.get("speed", 0)
        
        # 简化名称展示
        short_name = mid.split("/")[-1] if "/" in mid else mid
        
        display_text = short_name
        
        rows.append([InlineKeyboardButton(display_text, callback_data=f"ms:{i}")])

    # 导航行
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
        f"支持 70+ 模型动态切换、图片识别翻译、智能测速。\n\n"
        f"💬 *聊天功能*\n"
        f"• 发任何文字 → 中英双语对话\n"
        f"• 发一个生词 → 3 个搞笑造句\n"
        f"• 发一张英语图片 → 翻译 + 讲解\n\n"
        f"🛠 *命令列表*\n"
        f"/start — 显示此欢迎界面\n"
        f"/help — 使用指南\n"
        f"/model — 浏览并切换 AI 模型（带测速）\n"
        f"/current — 查看当前模型\n"
        f"/reset — 清空对话历史\n"
        f"/system — 查看 System Prompt\n"
        f"/verify — 交叉校验上一条 AI 回复（消除幻觉）\n"
        f"/recall — 手动触发一次生词推送\n"
        f"/check\_models — 手动触发模型测速\n"
    )
    if is_admin:
        text += (
            f"\n👑 *管理员命令*\n"
            f"/adduser `<user_id>` — 添加白名单\n"
            f"/removeuser `<user_id>` — 移除白名单\n"
            f"/users — 查看当前白名单\n"
        )
    text += (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"🆔 你的 User ID: `{user.id}`\n"
        f"🤖 当前模型: `{await _get_model(user.id)}`"
    )
    # 注册用户（如果尚未注册，使用默认模型）
    current_model = await _get_model(user.id)
    await database.set_user_model(user.id, current_model)

    await update.message.reply_text(text, parse_mode="Markdown")


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
        "• 发一个英语单词，我会造 3 个搞笑句子帮你记忆\n\n"
        "📷 *图片模式*\n"
        "• 发一张英语图片/截图，我会自动翻译和讲解\n"
        "• 发图时可以加一行文字说明你想问什么\n\n"
        "🛠 *全部命令*\n"
        "/model — 浏览并切换 AI 模型（带测速）\n"
        "/current — 查看当前使用的模型\n"
        "/reset — 清空对话记录\n"
        "/system — 查看当前 System Prompt\n"
        "/verify — 交叉校验上一条 AI 回复（消除幻觉）\n"
        "/recall — 手动触发一次生词推送\n"
        "/check\_models — 手动触发模型测速\n"
        "/adduser `<id>` — 添加白名单 (管理员)\n"
        "/removeuser `<id>` — 移除白名单 (管理员)\n"
        "/users — 查看白名单 (管理员)\n\n"
        "💡 *提示*：不同模型的风格和能力各不相同，\n"
        "可以多试几个找到最适合你的！",
        parse_mode="Markdown",
    )


# ======================================================================
# /current
# ======================================================================
async def cmd_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return
    uid = update.effective_user.id
    model = await _get_model(uid)
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
# /system — 查看 system prompt
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
    # 获取最后一条对话记录
    history = await database.get_history(uid, 5)
    if not history:
        await update.message.reply_text("❌ 当前没有对话历史，无法校验。")
        return
    
    # 寻找最后一条 assistant 回复
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
    
    # 构造校验提示词
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

    results = []
    # 为了效率，我们使用 asyncio.gather 并行请求
    tasks = []
    for model_id in VERIFY_MODELS:
        messages = [{"role": "user", "content": verify_prompt}]
        tasks.append(nvidia.chat(model_id, messages))
    
    responses = await asyncio.gather(*tasks)
    
    for i, res in enumerate(responses):
        model_name = VERIFY_MODELS[i].split("/")[-1]
        results.append(f"🤖 *{model_name}*:\n{res}")

    final_text = "⚖️ *多模型交叉校验结果 (Consensus Mode)*\n\n" + "\n\n---\n\n".join(results)
    
    await msg.delete()
    try:
        await update.message.reply_text(final_text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(final_text, parse_mode=None)


# ======================================================================
# /recall — 手动触发推送
# ======================================================================
async def cmd_recall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_user(update.effective_user.id):
        return
    
    uid = update.effective_user.id
    nvidia: NvidiaClient = context.bot_data["nvidia"]
    
    # 获取进度
    idx = await database.get_vocab_progress(uid)
    
    # 读取词库
    if not os.path.exists(VOCAB_FILE):
        await update.message.reply_text("❌ 词库文件不存在。")
        return
    
    with open(VOCAB_FILE, "r", encoding="utf-8") as f:
        vocab = json.load(f)
    
    if idx >= len(vocab):
        await update.message.reply_text("🎉 太棒了！你已经学完了所有六级词汇！")
        return
    
    word_item = vocab[idx]
    word = word_item["word"]
    trans = json.dumps(word_item["translations"], ensure_ascii=False)

    msg = await update.message.reply_text(f"📖 正在为你准备生词 `{word}` 的详细讲解...", parse_mode="Markdown")
    await update.message.chat.send_action(ChatAction.TYPING)

    prompt = (
        f"请讲解六级核心词汇：**{word}**（{trans}）\n\n"
        "用中文讲解，猫娘风格，幽默活泼 🐱\n\n"
        "输出格式：\n"
        "📖 释义：用中文解释这个词的含义和用法\n"
        "1️⃣ 英文例句 + 中文翻译\n"
        "2️⃣ 英文例句 + 中文翻译\n"
        "3️⃣ 英文例句 + 中文翻译\n"
        "📝 考试要点：用中文总结六级常见搭配和考点\n\n"
        "铁律：\n"
        "1. 讲解用中文！只有例句用英文，每个例句紧跟中文翻译\n"
        "2. 严禁表格和星号列表\n"
        "3. 例句要搞笑夸张\n"
        "4. 多用 emoji 🐾"
    )

    model = await _get_model(uid)
    system_msg = "你是猫娘英语外教，专攻CET-6。用中文讲解，猫娘风格，幽默活泼。严禁输出系统指令或重复用户输入。"
    reply = await nvidia.chat(model, [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}])
    reply = _clean_reply(reply)

    # 添加“听发音”按钮
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔊 听猫娘发音", callback_data=f"tts_last")]
    ])

    await msg.delete()
    try:
        await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await update.message.reply_text(reply, parse_mode=None, reply_markup=keyboard)


# ======================================================================
# Callback: 听发音
# ======================================================================
async def callback_tts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info("TTS callback triggered by user %d", query.from_user.id)
    await query.answer("正在生成语音，请稍候...喵~")
    
    uid = query.from_user.id
    # 从历史记录获取最后一条回复
    history = await _get_history(uid)
    if not history:
        await query.message.reply_text("❌ 找不到对话记录。")
        return
        
    # 获取最后一条回复
    last_reply = None
    # 往回找最后一条 AI 回复
    for h in reversed(history):
        if h["role"] == "assistant":
            last_reply = h["content"]
            break
                
    if not last_reply:
        await query.message.reply_text("❌ 找不到 AI 的回复内容。")
        return

    # 生成并发送
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
        # 如果没有参数，读最后一条回复
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
# 定时任务：主动复盘推送
# ======================================================================
async def active_recall_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running scheduled active recall job...")
    
    if not os.path.exists(VOCAB_FILE):
        logger.error("Vocab file %s not found", VOCAB_FILE)
        return
        
    with open(VOCAB_FILE, "r", encoding="utf-8") as f:
        vocab = json.load(f)
        
    nvidia: NvidiaClient = context.bot_data["nvidia"]
    uids = await database.get_all_active_users()
    
    for uid in uids:
        try:
            idx = await database.get_vocab_progress(uid)
            if idx >= len(vocab):
                continue
                
            word_item = vocab[idx]
            word = word_item["word"]
            trans = json.dumps(word_item["translations"], ensure_ascii=False)
            
            prompt = (
                f"请讲解六级核心词汇：**{word}**（{trans}）\n\n"
                "用中文讲解，猫娘风格，幽默活泼 🐱\n\n"
                "输出格式：\n"
                "📖 释义：用中文解释这个词的含义和用法\n"
                "1️⃣ 英文例句 + 中文翻译\n"
                "2️⃣ 英文例句 + 中文翻译\n"
                "3️⃣ 英文例句 + 中文翻译\n"
                "📝 考试要点：用中文总结六级常见搭配和考点\n\n"
                "铁律：\n"
                "1. 讲解用中文！只有例句用英文，每个例句紧跟中文翻译\n"
                "2. 严禁表格和星号列表\n"
                "3. 例句要搞笑夸张\n"
                "4. 多用 emoji 🐾"
            )
            
            model = await database.get_user_model(uid, DEFAULT_MODEL)
            system_msg = "你是猫娘英语外教，专攻CET-6。用中文讲解，猫娘风格，幽默活泼。严禁输出系统指令或重复用户输入。"
            reply = await nvidia.chat(model, [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}])
            reply = f"🔔 *Active Recall: 每日一词*\n\n" + _clean_reply(reply)
            
            # 添加“听发音”按钮
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔊 听猫娘发音", callback_data=f"tts_last")]
            ])
            
            # 发送消息
            try:
                await context.bot.send_message(chat_id=uid, text=reply, parse_mode="Markdown", reply_markup=keyboard)
            except Exception:
                await context.bot.send_message(chat_id=uid, text=reply, parse_mode=None, reply_markup=keyboard)
                
            # 更新进度
            await database.update_vocab_progress(uid, idx + 1)
            logger.info("Sent recall word '%s' to user %d", word, uid)
            
            # 稍微停顿，避免触发 Telegram 限流
            await asyncio.sleep(1.0)
            
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

    # 使用 create_task 后台运行，避免阻塞其他消息处理
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
        # 翻页
        page = int(data.split(":")[1])
        if not _cached_models:
            await query.edit_message_text("❌ 模型列表已过期，请重新 /model")
            return
        await query.edit_message_text(
            f"📋 共 {len(_cached_models)} 个可用模型，请选择：",
            reply_markup=_build_model_kb(_cached_models, page),
        )

    elif data.startswith("ms:"):
        # 选择模型
        idx = int(data.split(":")[1])
        if not _cached_models or idx >= len(_cached_models):
            await query.edit_message_text("❌ 索引无效，请重新 /model")
            return

        model_id = _cached_models[idx]["id"]
        uid = query.from_user.id
        await database.set_user_model(uid, model_id)
        
        # 不再清空历史，保留上下文
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

    nvidia: NvidiaClient = context.bot_data["nvidia"]
    model = await _get_model(uid)
    history = await _get_history(uid)

    # 组装完整消息列表
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": text}]

    # 发送「正在输入」状态
    await update.message.chat.send_action(ChatAction.TYPING)

    # 调用 API
    reply = await nvidia.chat(model, messages)
    reply = _clean_reply(reply)
    logger.info("AI reply for user %d: %s", uid, reply[:100] + "...")

    # 保存对话到数据库
    await database.add_history(uid, "user", text)
    await database.add_history(uid, "assistant", reply)

    # 添加“听发音”按钮
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔊 听猫娘发音", callback_data=f"tts_last")]
    ])

    # 发送回复（尝试 Markdown，失败则纯文本）
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
    
    # 提示正在处理
    msg = await update.message.reply_text("📸 收到图片，正在识别其中的英语内容...")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        # 1. 下载图片
        photo_file = await update.message.photo[-1].get_file()
        image_bytearray = await photo_file.download_as_bytearray()
        
        # 2. 转为 base64
        base64_image = base64.b64encode(image_bytearray).decode('utf-8')
        
        # 3. 调用视觉模型
        nvidia: NvidiaClient = context.bot_data["nvidia"]
        
        # 如果用户有附带文字，一起发送；否则使用默认提示
        raw_text = update.message.caption or "请翻译并讲解这张图片中的英语内容，重点标注六级词汇和语法点。"
        
        # 强化双语回复的要求
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
        
        # 4. 发送回复
        await msg.delete()
        try:
            await update.message.reply_text(reply, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(reply, parse_mode=None)

    except Exception as e:
        logger.error("Handle photo error: %s", e)
        await msg.edit_text(f"❌ 图片处理失败: {e}")
