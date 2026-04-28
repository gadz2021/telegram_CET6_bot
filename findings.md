# 项目发现：Telegram 英语外教 Bot

## 技术基础设施
- **API**: NVIDIA NIM (兼容 OpenAI 接口)
- **数据库**: SQLite (bot_data.db)，使用 `aiosqlite` 异步库
- **TTS (语音)**: `edge-tts` (使用 zh-CN-XiaoxiaoNeural / en-US-AvaNeural)
- **服务管理**: systemd (服务名: `english_tutor.service`)

## 维护与操作手册
- **快速查看日志**: `sudo journalctl -u english_tutor.service -n 20 --no-pager`
- **查看 AI 回语**: `sudo journalctl -u english_tutor.service | grep "AI reply"`
- **重启服务**: `sudo systemctl restart english_tutor.service`
- **验证服务状态**: `sudo systemctl is-active english_tutor.service`
- **一键部署脚本**: `./deploy.sh` (本地项目根目录)

## 部署与环境细节
- **服务器 IP**: 115.190.249.227
- **SSH 密码**: `Zzh20060505!`
- **代码路径**: /root/tg_bot/
- **双机冲突 (Conflict)**: 如果本地和远程同时运行 `bot.py`，会导致 `Conflict` 错误。部署前必须 `pkill -f bot.py`。
- **定时任务**: `APScheduler` 的 `first` 参数应设为 `RECALL_INTERVAL` 以避免重启立即推送。

## 模型表现与行为
- **Llama 3.1/3.3**: 英语水平极佳，但除非严格过滤，否则容易输出 Markdown 表格和星号列表。
- **DeepSeek**: 推理能力强，中文表达自然。
- **延迟**: 部分模型（如 minimax-m2.5）在 NVIDIA API 侧偶尔会出现高延迟（90秒+）。

## UI/UX 模式
- **双语格式**: “英文段落 -> 中文段落”是首选布局。
- **历史追踪**: 所有 AI 回复都必须存入历史记录，以启用“听全文”按钮。
- **TTS 过滤**: 在生成语音前必须剔除表情符号和常见 Markdown 符号。
