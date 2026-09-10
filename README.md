# 🐱 Telegram CET-6 英语外教 Bot

> **专为大学英语六级（CET-6）备考打造的生产级智能外教系统。**  
> 基于 **DeepSeek 官方 API（DeepSeek V4 Flash 旗舰）** 极速驱动，深度融合 **SM-2 科学间隔重复算法**、**渐进式闪卡互动（Progressive Active Recall）**、**秒级单词释义缓存库**、**24小时工业级静默容灾** 与 **自然语音合成引擎**。
> *(注：原 NVIDIA NIM 版本已全量归档备份并标注 Tag: `v1.0.0-nim-final`)*

---

## 🌟 核心硬核特性

### 1. 🧠 科学抗遗忘：SM-2 间隔重复引擎（Spaced Repetition）
- **真·艾宾浩斯记忆曲线**：告别“背了前面忘后面”。采用业界公认的 SuperMemo-2（SM-2）核心算法，每个单词动态维护记忆难度系数（`ease_factor`）与复习间隔（`interval`）。
- **三档量化自评**：
  - `✅ 记住了`：根据记忆曲线成倍延长复习周期（1天 ➡️ 3天 ➡️ 7天 ➡️ 18天 ➡️ 30天）；
  - `🤔 模糊`：维持高频短周期，近期强化复习；
  - `❌ 忘了`：立即重置记忆难度并回退到第 1 阶段，重新构建神经记忆链。
- **5,651 词真题乱序词库**：预置全量大学英语六级真题核心词库，经科学洗牌，杜绝“放弃背到 abandon”的字母序疲劳。

### 2. ⚡ 渐进式主动回忆闪卡（Progressive Active Recall Flashcard）
- **新词首次学**：推送生动完整的精讲卡片，包含词义精析、接地气幽默例句、六级高频考点、纯正美音发音与自评。
- **旧词到期复习（双轨极速闪卡）**：
  - **🧠 瞬时记忆测验**：推送时先隐藏答案，抛出单词与记忆唤醒卡片，激活大脑的主动提取机制（Active Retrieval）。
  - **💬 轨道 A（自然语言作答）**：用户可在聊天框直接打字或发语音（如回答“决定”或造句）。外教 AI 秒级给予暖心点评，夸奖正确点并针对六级考点温和补充，随后附带打卡归档。
  - **👀 轨道 B（零压力就地翻牌）**：懒得打字或真忘了？随手点击 `[ 👀 查看答案与考点详解 ]`，消息原地展开全部核心考点与例句，绝无被催作业的心理负担！

### 3. 🛡️ 零疏漏保证：打卡阻断与节奏防爆（Block-and-Remind）
- **绝不漏背一个词**：如果上一生词或复习词尚未完成自评，下次推送时系统**绝不会盲目推送长篇新内容造成消息堆积**，而是弹出温和的自评提醒卡片。
- **即评即解锁**：用户一旦标记自评，阻断瞬间解除，毫秒级无缝推送当前应学单词，闭环保障全词库 100% 掌握。
- **早晚双黄金时间点**：默认对齐大脑最高效的记忆固化窗口（每日 **09:00** 与 **20:00** 定时推送）。
- **压力缓冲系统（`/pause`）**：
  - 支持一键 `😴 今天够了，明天见`（跳过今日剩余推送至明早 08:00）；
  - 支持快捷开启假期模式（暂停 3 天 / 7 天）；
  - 支持自主切换为【仅晚上推送】或【早晚均推】。

### 4. 📊 全景学习数据看板（`/stats`）
- 随时输入 `/stats`，生成精美的个人可视化学习仪表盘：
  - **词库总进度**：已背单词数 / 词库总量百分比（含直观 Emoji 进度条）；
  - **记忆库沉淀**：已进入 SM-2 周期库的单词数与牢固掌握（`interval >= 21`）词汇量；
  - **复习预警**：当前已到期急需复习的单词数；
  - **连续打卡坚持**：🔥 连续学习天数（Streak Tracker），激励每日自律。

### 5. ⚡ 极速推理与 Thinking 优化架构
- **DeepSeek V4 Flash 旗舰直连**：从旧版 NVIDIA NIM 迁移至 DeepSeek 官方直连 API，端到端生成耗时从 20~25s 骤降至 **1.0 ~ 2.5s**。
- **关闭 Thinking 实现毫秒级响应**：
  - 针对单词释义、音标输出与真题考点生成场景，深度推理（Thinking）会产生 500~1500 tokens 的多余思考耗时（增加 6~10s 延迟）。
  - 通过显式关闭 Thinking (`extra_body={"thinking": {"type": "disabled"}}`)，免除无意义思考开销，实现真正即开即用的流式闪卡体验。
- **持久化 `word_cache` 预加载库**：学习卡片一旦生成即写入本地 SQLite 高速缓存，第二次复习或翻牌直接 **0.001s 本地极速返回**，零 API 消耗、零网络等待。
- **防雪崩滑动窗口（60 RPM）**：支持高并发速率控制，全天候平稳运行。

### 6. 🔊 纯正双语神经语音（TTS Engine）
- 集成 `edge-tts` 高质量神经语音与 `ffmpeg` 音频流转码，智能剥离表情符号生成专属发音文件。
- **🔊 听单词发音**：单点针对当前单词，纯正美音精准朗读，解决“哑巴英语”。
- **📖 听全文朗读**：完整朗读 AI 外教生成的英文例句与讲解。

---

## 🏗 技术架构一览

```
telegram辅助bot/
├── bot.py                 # 服务主入口：调度注册、并发轮询、定时任务
├── handlers.py            # 核心业务层：指令系统、主动闪卡交互、作答批改、回调状态机
├── deepseek_client.py     # DeepSeek 官方客户端（兼容 OpenAI SDK，内置关闭 Thinking 参数）
├── nvidia_client.py       # 客户端兼容层：动态测速、自动容灾、双 Pass 探测
├── database.py            # SQLite 异步持久层：SM-2 排期、打卡连击、word_cache 缓存
├── rate_limiter.py        # 令牌滑动窗口限速器（Sliding-Window Algorithm）
├── config.py              # 敏感配置项（API Key、推送时间、默认模型）⚠️ 不入库
├── config.example.py      # 配置模板文件
├── cet6_words.json        # CET-6 乱序权威词库（5,651 词）
├── available_models.json  # 存活模型池与实时测速延迟表
├── verify_deepseek.py     # 自动化全链路集成测试套件
└── requirements.txt       # Python 项目依赖清单
```

### 数据库核心模型

| 表名 | 作用与核心字段 |
| :--- | :--- |
| `vocab_progress` | 记录用户当前学到的词库游标 `word_index`、最后推送时间及暂停时间戳 |
| `word_schedule` | **SM-2 调度引擎**：`interval`（间隔）、`ease_factor`（难度因子）、`next_review`（下次到期）、`review_count`（复习轮数） |
| `learning_streak` | 记录每日学习打卡记录与连续天数（`streak`） |
| `users` | 用户专属首选模型、`push_mode`（早晚/仅晚）、`pending_eval_word`（阻断锁）、`pending_quiz_word`（测验锁） |
| `history` | 异步对话上下文，支持翻页与语音重听 |
| `whitelist` | 基于 RBAC 的系统白名单权限隔离 |

---

## 📋 指令清单

### 🎓 学习与复习

| 指令 | 说明 |
| :--- | :--- |
| `/recall` | 手动触发一次生词学习或闪卡复习（受防漏阻断保护） |
| `/stats` | 打开**学习全景数据看板**（进度百分比、到期复习量、连续打卡天数） |
| `/pause` | 打开**推送频次与休息面板**（跳过今日、休假 3/7 天、切换晚间单推） |
| `/verify` | 对上一条外教讲解进行 **3 大顶尖模型交叉校验**，彻底消除幻觉 |
| `/speak` | 手动生成任意英文文本或上一条回复的语音朗读 |

### 🤖 AI 模型与系统

| 指令 | 说明 |
| :--- | :--- |
| `/model` | 调出实时分页模型面板，查看在线模型及其实测延迟，一键热切换 |
| `/current` | 查看当前绑定的模型及历史上下文轮数 |
| `/reset` | 清空对话上下文历史（保留单词学习进度与 SM-2 排期） |
| `/system` | 查看当前活跃的外教 System Prompt 教学人设 |
| `/check_models` | 手动触发一次后台 80+ 模型可用性全量巡检与测速 |

### 👑 管理员权限（Admin Only）

| 指令 | 说明 |
| :--- | :--- |
| `/adduser <id>` | 将指定 Telegram User ID 添加入白名单 |
| `/removeuser <id>` | 移除指定用户的访问权限 |
| `/users` | 查看当前管理员列表及全部白名单用户清单 |

---

## 🚀 快速上手部署

### 1. 系统要求
- **操作系统**：Linux（推荐 Ubuntu 22.04+ / Debian 12+）
- **Python 环境**：Python 3.12+
- **系统工具**：`ffmpeg`（用于语音流 OPUS 转码）
- **凭证准备**：
  - [NVIDIA NIM API Key](https://build.nvidia.com/)（免费申请，获得充沛 GPU 算力）
  - [Telegram Bot Token](https://t.me/BotFather)

### 2. 安装系统依赖与环境
```bash
# 1. 克隆代码仓库
git clone <your-repo-url>
cd telegram辅助bot

# 2. 安装系统转码工具 ffmpeg
sudo apt update && sudo apt install -y ffmpeg

# 3. 安装 Python 核心依赖
pip install -r requirements.txt
```

### 3. 配置密钥与参数
```bash
# 复制配置模板
cp config.example.py config.py

# 编辑配置
nano config.py
```

在 `config.py` 中配置核心参数：
```python
# 必须配置
TELEGRAM_BOT_TOKEN = "你的_BOT_TOKEN"
NVIDIA_API_KEY = "你的_NVAPI_KEY"
ADMIN_USER_IDS = [123456789]  # 你的 Telegram ID

# 默认主力模型（实测最佳性能梯队）
DEFAULT_MODEL = "minimaxai/minimax-m3"

# 推送时间表（默认北京时间早晚各推一次）
TIMEZONE = "Asia/Shanghai"
RECALL_PUSH_TIMES = ["09:00", "20:00"]
```

### 4. 启动与持久化运行

**生产推荐：后台 Daemon 启动**
```bash
nohup python3 bot.py > bot.log 2>&1 &
```

**或者使用 Systemd 托管**
```bash
# 复制服务文件
sudo cp english_tutor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable english_tutor
sudo systemctl start english_tutor
```

---

## 💡 常见问题与免坑指南

1. **为什么不需要死磕复杂的语法术语？**  
   大学英语六级（CET-6）考卷早在二十年前就取消了纯语法单选题。全卷得分的 90% 依赖于**词汇辨识反应速度（听力与阅读定位）**。本 Bot 的核心目标是帮你在最放松的日常状态下混熟 5,651 个真题词汇，拒绝死记硬背。
2. **如果某个模型在 NVIDIA 平台上临时下架了怎么办？**  
   底层内置的 `_chat_with_auto_failover` 会在 1 毫秒内捕获状态码并静默切换至备用活跃模型，你的学习进度和使用体验完全不受影响。
3. **我只想晚上复习，不想早上被消息打扰怎么办？**  
   直接在 Telegram 中输入 `/pause`，点击 `🌙 仅晚上推送`，系统会自动将频率调整为每天 20:00 推送 1 次。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
