"""配置文件 — Telegram 英语外教 Bot（示例模板）"""

# ====== Telegram ======
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# ====== NVIDIA NIM API ======
NVIDIA_API_KEY = "YOUR_NVIDIA_API_KEY"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "meta/llama-3.1-70b-instruct"
VISION_MODEL = "meta/llama-3.2-90b-vision-instruct"
AVAILABLE_MODELS_FILE = "available_models.json"
CHECK_INTERVAL = 21600
RECALL_INTERVAL = 14400
VOCAB_FILE = "cet6_words.json"
TEST_PROMPT = "请简短地用英语讲解一个医院急救室相关的词汇，并附带中文翻译。"
RECOMMENDED_MODELS = [
    "minimaxai/minimax-m2.5",
    "minimaxai/minimax-m2.7",
    "meta/llama-3.1-70b-instruct",
    "meta/llama-3.3-70b-instruct",
    "qwen/qwen2.5-coder-32b-instruct",
    "mistralai/mistral-large-3-675b-instruct-2512",
    "meta/llama-3.1-405b-instruct",
]
VERIFY_MODELS = [
    "meta/llama-3.3-70b-instruct",
    "qwen/qwen2.5-coder-32b-instruct",
    "mistralai/mistral-large-3-675b-instruct-2512",
]

# ====== 代理 ======
PROXY_URL = None

# ====== 速率限制 ======
RATE_LIMIT_PER_MINUTE = 40

# ====== 对话设置 ======
MAX_HISTORY = 20
REQUEST_TIMEOUT = 120

# ====== System Prompt ======
SYSTEM_PROMPT = (
    "你是猫娘的私人英语外教，专攻大学英语六级考试（CET-6）。"
    "你的教学风格幽默、接地气，善于用搞笑的例句帮助记忆。\n\n"
    "核心功能：\n"
    "1. 【日常对话】用中英双语进行六级作文场景对话。回复时遵循「先英文、后中文」的结构，"
    "中文翻译要自然流畅，不要逐字死译。\n"
    "2. 【生词造句】遇到生词时，造3个搞笑、夸张的英语句子并附带中文翻译，重点标注六级核心词汇。\n"
    "3. 【作文练习】引导猫娘进行写作练习，并给出修改建议。\n\n"
    "回复规则：\n"
    "- **禁止使用星号（*）作为列表符号**，请使用数字（1. 2. 3.）或 emoji（如 💡, ✅, 📌）替代。\n"
    "- **严禁生成任何形式的表格**（Markdown Table），因为 Telegram 手机端无法正常显示。\n"
    "- 每次回复保持简洁，原则上不超过 300 字。\n"
    "- 先给出完整的英文段落，再给出完整的中文对照翻译，不要中英混杂。\n"
    "- 重点词汇用 **粗体** 标注，并在最后列出 2-3 个核心词汇的详解。\n"
    "- 语法点用 「」 括起来解释，例如 「Passive Voice」。\n"
    "- 保持猫娘外教的活泼语气，多用 emoji 互动。"
)

# ====== 用户白名单 ======
ADMIN_USER_IDS = [YOUR_TELEGRAM_USER_ID]
ALLOWED_USER_IDS = []
WHITELIST_FILE = "whitelist.json"
