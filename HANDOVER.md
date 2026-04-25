# Telegram English Tutor Bot — 项目交接文档 (HANDOVER.md)

## 1. 项目概况
这是一个基于 **NVIDIA NIM API** 驱动的生产级 Telegram 英语外教 Bot。它专为大学英语六级（CET-6）备考设计，集成了智能对话、生词讲解、图片分析以及自动化的模型管理系统。

## 2. 核心架构与功能
### 2.1 模型管理 (NVIDIA NIM)
- **自动化探测**：每 6 小时自动扫描 130+ 模型，生成 `available_models.json` 白名单。
- **智能 UI**：`/model` 菜单展示模型耗时及**功能描述**（如：国产之光、性价比首选），并根据推荐度+速度自动排序。
- **并发处理**：支持 `concurrent_updates`，允许多个用户或同一用户多条消息并行处理，不再阻塞。

### 2.2 教学逻辑与 UI 优化
- **双语输出**：采用「先全英段落、后全中段落」的结构，确保翻译自然流畅，避免逐句死译。
- **格式约束**：
    - **严禁表格**：考虑到 Telegram 手机端显示效果，禁止生成 Markdown 表格。
    - **禁用星号列表**：强制将 `* ` 列表符替换为圆点 `• ` 或数字，确保排版整洁。
- **上下文保留**：切换 AI 模型时**不再清空**对话历史，支持跨模型连续教学。
- **多模态支持**：视觉模型（Llama-3.2 Vision）已同步最新的双语规范和词汇讲解逻辑。

### 2.3 安全与持久化
- **数据库驱动**：全量迁移至 **SQLite (`bot_data.db`)**。对话历史、用户设置、白名单均实现异步持久化。
- **白名单机制**：基于 `ADMIN_USER_IDS` 和数据库 `whitelist` 表，支持 `/adduser` 动态管理。
- **交叉验证 (Consensus Mode)**：通过 `/verify` 命令调用三个不同架构的模型（Llama, Qwen, Mistral）进行结果交叉比对，物理消除 AI 幻觉。
- **主动式记忆复盘 (Active Recall)**：集成 CET-6 乱序词库，每 4 小时自动推送生词讲解，支持 `/recall` 手动触发。
- **语音辅助 (TTS)**：集成 `edge-tts` 和 `ffmpeg`，为所有回复提供中英双语发音按钮，支持 `/speak` 手动朗读。

## 3. 技术栈
- **语言**：Python 3.12.3
- **框架**：`python-telegram-bot` (v20+, 开启并发模式)
- **数据库**：`aiosqlite` (异步 SQLite 访问)
- **API**：OpenAI SDK 兼容模式连接 NVIDIA NIM
- **服务管理**：`systemd` (服务名: `english_tutor.service`)

## 4. 关键决策记录
- **速率限制**：采用改进的滑动窗口算法（40 RPM），修复了旧版在限流时会持有全局锁阻塞所有用户的 Bug。
- **双保险格式化**：在 Prompt 约束的基础上，增加代码层面的正则拦截器 (`_clean_reply`)，物理消除顽固模型的星号列表。
- **代理配置**：代码预留了 `v2rayA` 接口，目前海外服务器直连。

## 5. 待办事项 (TODO)
- [ ] **多模态历史增强**：(暂无需求) 暂不支持图片上下文，目前已足够。
- [ ] **更多词库**：未来可支持雅思、托福等更多专项词库导入。

## 6. 维护手册
- **查看日志 (快)**：`sudo journalctl -u english_tutor.service -n 20 --no-pager`
- **查看 AI 回复日志**：`sudo journalctl -u english_tutor.service | grep "AI reply"`
- **重启服务**：`sudo systemctl restart english_tutor.service`
- **数据库路径**：`/root/vscode/telegram辅助bot/bot_data.db`

---
*文档最后更新：2026-04-25 17:36*
*当前状态：生产环境稳定运行 (数据库持久化 + 高并发优化版)*
