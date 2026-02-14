# PD FastAPI Starter
## 快速开始

### 1) 安装 uv 并创建虚拟环境

```bash
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建虚拟环境
uv venv
```

### 2) 安装依赖

```bash
uv sync
# 如使用 EmailStr 字段，请确保安装 email-validator
uv pip install email-validator
```

### 3) 配置环境变量（推荐）

1) 直接提供 `DATABASE_URL`

```
DATABASE_URL=mysql+pymysql://user:password@127.0.0.1:3306/cd_ai_db?charset=utf8mb4
```

### 4) 初始化/同步数据库表结构

```bash
# 一次性创建基础表（或补齐缺失索引/列）
python database_setup.py
```

### 5) 运行应用

```bash
# 快速运行（开发环境）
uv run main.py

# 热重载
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uvicorn main:app --host 0.0.0.0 --port 8000
   ```

## Endpoints

- GET /healthz
- POST /api/v1/auth/login
