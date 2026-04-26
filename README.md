# 🐱 Telegram CET-6 英语外教 Bot

基于 **NVIDIA NIM API** 驱动的生产级 Telegram 英语外教 Bot，专为大学英语六级（CET-6）备考设计。

集成了智能对话、生词讲解、图片识别翻译、多模型动态切换、交叉校验消除幻觉、主动式记忆复盘、双语语音朗读等功能。

---

## ✨ 功能一览

### 💬 智能对话
- **中英双语教学**：先全英段落、后全中翻译，自然流畅不逐句死译
- **生词造句**：遇到生词自动生成 3 个搞笑夸张例句，重点标注六级核心词汇
- **作文练习**：引导写作并给出修改建议
- **猫娘人设**：幽默活泼的教学风格，emoji 互动

### 🤖 模型管理
- **70+ 模型动态切换**：自动扫描 NVIDIA NIM 全部可用模型，带测速排序
- **智能推荐排序**：推荐模型优先展示，其余按响应速度从快到慢
- **分页选择器**：Inline Keyboard 分页浏览，一键切换
- **切换不断对话**：换模型时保留对话历史，支持跨模型连续教学

### 📷 多模态支持
- **图片识别翻译**：发送英语截图/图片，自动翻译 + 讲解
- **视觉模型**：Llama-3.2 Vision 等视觉模型同步双语规范

### 🔍 交叉校验（Consensus Mode）
- `/verify` 命令调用 **3 个不同架构模型**（Llama / Qwen / Mistral）并行校验
- 物理消除 AI 幻觉，确保语法和释义的准确性

### 🔔 主动式记忆复盘（Active Recall）
- 集成 **CET-6 乱序词库**，每 2 小时自动推送生词讲解
- `/recall` 手动触发，支持学习进度追踪
- 进度断电保存（SQLite 持久化），重启不丢失

### 🔊 语音朗读（TTS）
- 基于 `edge-tts` + `ffmpeg`，中英双语自动识别发音
- **🔊 听单词发音**：精准朗读当前讲解的单词（生词推送 / 对话中自动提取）
- **📖 听全文朗读**：朗读完整 AI 回复
- `/speak` 命令手动朗读任意文本或上一条回复

### 🔐 安全与权限
- **白名单机制**：管理员 + 数据库白名单双重验证
- **速率限制**：改进的滑动窗口算法（40 RPM），不阻塞其他用户
- **数据库持久化**：全量使用 SQLite，对话历史、用户设置、白名单异步持久化

---

## 🏗 项目结构

```
telegram辅助bot/
├── bot.py                 # 主入口，注册命令、定时任务、启动轮询
├── handlers.py            # 所有命令和消息处理器（对话、模型切换、TTS 等）
├── nvidia_client.py       # NVIDIA NIM API 客户端（模型列表、聊天、图片识别）
├── database.py            # SQLite 异步数据库层（用户、历史、白名单、词汇进度）
├── rate_limiter.py        # 滑动窗口速率限制器
├── config.py              # 配置文件（密钥、模型、Prompt 等）⚠️ 不入库
├── config.example.py      # 配置模板（占位符，供参考）
├── cet6_words.json        # CET-6 乱序词库
├── available_models.json  # 自动生成的可用模型白名单（含测速数据）
├── english_tutor.service  # systemd 服务配置
├── requirements.txt       # Python 依赖
├── HANDOVER.md            # 项目交接文档
└── .gitignore
```

---

## 🚀 部署指南

### 1. 环境要求

- **Python** 3.12+
- **ffmpeg**（TTS 语音转码需要）
- **NVIDIA NIM API Key**（[免费申请](https://build.nvidia.com/)）
- **Telegram Bot Token**（[@BotFather](https://t.me/BotFather) 创建）

### 2. 安装依赖

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 ffmpeg（Ubuntu/Debian）
sudo apt install ffmpeg

# 安装 edge-tts（TTS 引擎）
pip install edge-tts
```

### 3. 配置

```bash
# 复制配置模板
cp config.example.py config.py

# 编辑配置，填入你的真实密钥
nano config.py
```

必须修改的配置项：

| 配置项 | 说明 |
|--------|------|
| `TELEGRAM_BOT_TOKEN` | 你的 Telegram Bot Token |
| `NVIDIA_API_KEY` | 你的 NVIDIA NIM API Key |
| `ADMIN_USER_IDS` | 管理员 Telegram User ID 列表 |

可选配置：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `PROXY_URL` | `None` | 代理地址，国内服务器设为 `"http://127.0.0.1:20171"` |
| `RATE_LIMIT_PER_MINUTE` | `40` | 每分钟最大请求数 |
| `MAX_HISTORY` | `20` | 最大对话轮数 |
| `CHECK_INTERVAL` | `21600` | 模型自动检测间隔（秒），默认 6 小时 |
| `RECALL_INTERVAL` | `7200` | 生词推送间隔（秒），默认 2 小时 |

### 4. 获取你的 User ID

首次运行后发送 `/start`，Bot 会回复你的 User ID，将其填入 `ADMIN_USER_IDS`。

### 5. 启动

**直接运行：**

```bash
python bot.py
```

**使用 systemd 托管（推荐生产环境）：**

```bash
# 复制服务文件
sudo cp english_tutor.service /etc/systemd/system/

# 按需修改 WorkingDirectory 和 ExecStart 路径
sudo nano /etc/systemd/system/english_tutor.service

# 启用并启动
sudo systemctl daemon-reload
sudo systemctl enable english_tutor.service
sudo systemctl start english_tutor.service
```

---

## 📋 命令列表

### 用户命令

| 命令 | 说明 |
|------|------|
| `/start` | 显示欢迎界面和你的 User ID |
| `/help` | 使用指南 |
| `/model` | 浏览并切换 AI 模型（带测速和分页） |
| `/current` | 查看当前使用的模型和对话历史数 |
| `/reset` | 清空对话历史 |
| `/system` | 查看当前 System Prompt |
| `/verify` | 交叉校验上一条 AI 回复（三模型共识） |
| `/recall` | 手动触发一次生词推送 |
| `/speak` | 朗读上一条回复或指定文本 |

### 管理员命令

| 命令 | 说明 |
|------|------|
| `/adduser <user_id>` | 添加用户到白名单 |
| `/removeuser <user_id>` | 从白名单移除用户 |
| `/users` | 查看当前白名单 |
| `/check_models` | 手动触发模型测速 |

### 特殊交互

- **发送文字** → 中英双语对话 / 生词讲解
- **发送图片** → 自动识别翻译 + 讲解
- **点击「🔊 听单词发音」** → 朗读当前讲解的单词
- **点击「📖 听全文朗读」** → 语音朗读完整回复

---

## 🧠 技术架构

### 技术栈

- **语言**：Python 3.12
- **框架**：`python-telegram-bot` v20+（开启 `concurrent_updates` 并发模式）
- **数据库**：`aiosqlite`（异步 SQLite）
- **AI API**：OpenAI SDK 兼容模式连接 NVIDIA NIM
- **TTS**：`edge-tts` + `ffmpeg`（MP3 → OGG/OPUS 转码）
- **服务管理**：`systemd`

### 关键设计决策

| 决策 | 原因 |
|------|------|
| 滑动窗口速率限制（40 RPM） | 修复旧版全局锁阻塞所有用户的 Bug，改为自动等待而非拒绝 |
| 双保险格式化（Prompt + 正则拦截器 `_clean_reply`） | 物理消除顽固模型的星号列表和表格 |
| NVIDIA API 直连 + Telegram 走代理 | 国内服务器 Telegram 必须走代理，但 NVIDIA API 直连更快更稳定 |
| 切换模型不清空对话历史 | 支持跨模型连续教学，用户体验更好 |
| SQLite 异步持久化 | 替代 JSON 文件存储，支持并发读写和数据一致性 |
| 模型两轮测速（Quick Pass + Deep Pass） | 首轮快速过滤，二轮极限宽容度（120s）深度测试超时模型 |

### 数据库表结构

| 表名 | 用途 |
|------|------|
| `users` | 用户当前选择的模型 |
| `history` | 对话历史记录 |
| `whitelist` | 用户白名单 |
| `vocab_progress` | 每个用户的词汇学习进度 |

---

## 🛠 维护手册

```bash
# 查看最近日志
sudo journalctl -u english_tutor.service -n 20 --no-pager

# 查看 AI 回复日志
sudo journalctl -u english_tutor.service | grep "AI reply"

# 重启服务
sudo systemctl restart english_tutor.service

# 数据库路径
/root/vscode/telegram辅助bot/bot_data.db
```

---

## 📌 待办事项

- [ ] 多模态历史增强：暂不支持图片上下文，目前已足够
- [ ] 更多词库：未来可支持雅思、托福等专项词库导入

---

## 📄 License

MIT
