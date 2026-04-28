# 代码推送教程

本文档详细说明如何将本地修改的代码推送到 **GitHub** 和 **远程服务器**。

---

## 推送前准备

### 1. 检查修改了哪些文件

```bash
git status
```

会显示类似这样的输出：

```
Changes not staged for commit:
  (modified):   handlers.py
  (modified):   config.py
```

### 2. 添加要推送的文件

**方式一**：添加所有修改的文件
```bash
git add .
```

**方式二**：只添加指定文件（比如只改了 handlers.py）
```bash
git add handlers.py
```

### 3. 提交修改

```bash
git commit -m "提交说明"
```

例如：
```bash
git commit -m "Fix: update TTS button text"
```

---

## 推送到 GitHub

```bash
git push origin main
```

如果弹出登录框，按提示输入 GitHub 用户名和 Token（或密码）。

---

## 推送到远程服务器

### 方式一：一键推送（推荐）

项目根目录下已有一键部署脚本：

```bash
./deploy.sh
```

或手动执行：

```bash
bash deploy.sh
```

### 方式二：手动分步执行

如果你不想用脚本，可以手动执行以下步骤：

#### Step 1: 复制文件到服务器

```bash
sshpass -p '服务器密码' scp -o ConnectTimeout=15 -o StrictHostKeyChecking=no 你的文件 root@服务器IP:/root/tg_bot/
```

例如推送 handlers.py：

```bash
sshpass -p 'Zzh20060505!' scp -o ConnectTimeout=15 -o StrictHostKeyChecking=no handlers.py root@115.190.249.227:/root/tg_bot/
```

**推送多个文件**（用空格隔开）：

```bash
sshpass -p 'Zzh20060505!' scp -o ConnectTimeout=15 -o StrictHostKeyChecking=no handlers.py bot.py config.py root@115.190.249.227:/root/tg_bot/
```

#### Step 2: 重启服务

```bash
sshpass -p 'Zzh20060505!' ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no root@115.190.249.227 'systemctl restart english_tutor.service'
```

#### Step 3: 验证服务状态

```bash
sshpass -p 'Zzh20060505!' ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no root@115.190.249.227 'systemctl is-active english_tutor.service'
```

输出 `active` 表示运行正常。

---

## 完整示例

假设你修改了 `handlers.py` 和 `config.py`，完整推送流程：

```bash
# 1. 查看状态
git status

# 2. 添加文件
git add handlers.py config.py

# 3. 提交
git commit -m "Feature: add new TTS button"

# 4. 推送到 GitHub
git push origin main

# 5. 复制到服务器（多个文件）
sshpass -p 'Zzh20060505!' scp -o ConnectTimeout=15 -o StrictHostKeyChecking=no handlers.py config.py root@115.190.249.227:/root/tg_bot/

# 6. 重启服务
sshpass -p 'Zzh20060505!' ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no root@115.190.249.227 'systemctl restart english_tutor.service'

# 7. 验证
sshpass -p 'Zzh20060505!' ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no root@115.190.249.227 'systemctl is-active english_tutor.service'
```

---

## 常见问题

### Q: `sshpass` 提示找不到命令

**解决**：安装 sshpass

```bash
# Ubuntu/Debian
sudo apt install sshpass

# macOS
brew install hudochenkov/sshpass/sshpass
```

### Q: GitHub 推送需要 Token

**解决**：GitHub 已不支持密码推送，需要使用 Personal Access Token。

1. 打开 https://github.com/settings/tokens
2. 点击 "Generate new token (classic)"
3. 勾选 `repo` 权限
4. 生成后复制 Token，用 Token 代替密码登录

### Q: 服务器密码有特殊字符

**解决**：用单引号包住密码

```bash
sshpass -p '你的密码(含特殊字符)' ssh ...
```

### Q: 不想每次输入密码

**解决**：配置 SSH 密钥（推荐）

```bash
# 本地生成密钥
ssh-keygen -t ed25519

# 复制公钥到服务器
ssh-copy-id root@115.190.249.227
```

之后就可以不带密码直接连接：

```bash
scp handlers.py root@115.190.249.227:/root/tg_bot/
ssh root@115.190.249.227 'systemctl restart english_tutor.service'
```

---

## 服务器信息汇总

| 项目 | 值 |
|------|-----|
| 服务器 IP | `115.190.249.227` |
| SSH 端口 | `22` |
| 用户名 | `root` |
| 密码 | `Zzh20060505!` |
| 代码目录 | `/root/tg_bot/` |
| 服务名 | `english_tutor.service` |

---

## 日志查看

推送后如果出问题，查看服务器日志：

```bash
# 查看最近 20 行日志
sshpass -p 'Zzh20060505!' ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no root@115.190.249.227 'journalctl -u english_tutor.service -n 20 --no-pager'

# 实时查看日志
sshpass -p 'Zzh20060505!' ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no root@115.190.249.227 'journalctl -u english_tutor.service -f'
```
