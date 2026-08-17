# 抖音达人联络官

基于 CloakBrowser（反检测浏览器）+ JS 逆向 + FastAPI + Vue3 的完整解决方案，实现：

- 抖音网页端扫码登录（Cookie 持久化）
- 多关键词批量搜索带货达人
- 按粉丝数、点赞数等指标筛选
- 自动发送私信（频率控制防封号）
- 管理后台浏览达人画像，一键跳转主页

---

## 项目结构

```
dy_auth_filter/
├── config/
│   └── settings.yaml       # 所有配置（关键词、筛选条件、私信模板等）
├── crawler/
│   ├── core/
│   │   ├── browser.py      # CloakBrowser 连接管理
│   │   ├── login.py        # 登录检测 & 二维码登录
│   │   └── sign.py         # X-Bogus / msToken 签名生成
│   ├── services/
│   │   ├── search.py       # 搜索服务（多关键词批量）
│   │   ├── filter.py       # 达人筛选逻辑
│   │   └── dm.py           # 私信发送服务
│   └── main.py             # 爬虫独立运行入口
├── backend/
│   ├── main.py             # FastAPI 应用入口
│   ├── api/routes.py       # API 路由
│   ├── models/
│   │   ├── database.py     # ORM 模型（KOL / DMLog / TaskLog）
│   │   └── crud.py         # 数据库操作层
│   └── schemas/kol.py      # Pydantic Schema
├── frontend/               # Vue3 + Element Plus 管理后台
│   └── src/
│       ├── views/
│       │   ├── Dashboard.vue   # 数据概览 + 快速发任务
│       │   ├── KolList.vue     # 达人列表（分页、搜索、批量私信）
│       │   ├── KolDetail.vue   # 达人画像 + 主页跳转
│       │   ├── Tasks.vue       # 任务管理
│       │   └── Settings.vue    # 登录状态 + 配置说明
│       └── ...
├── data/                   # 运行时数据（Cookie、数据库、Profile）
├── logs/                   # 日志文件
├── requirements.txt
├── start_backend.sh        # 启动后端
├── start_frontend.sh       # 启动前端
└── run_crawler.sh          # 独立运行爬虫
```

---

## 快速开始

### 一键启动（推荐）

```bash
# macOS / Linux：自动完成所有依赖安装并启动服务
./start.sh

# 可选参数
./start.sh --skip-update   # 跳过 pip/npm 更新（加速重复启动）
./start.sh --no-frontend   # 只启动后端
./start.sh --no-browser    # 跳过 CloakBrowser 下载（已安装时使用）

# Windows PowerShell
.\start.ps1
```

脚本自动完成：Python 版本检测 → 创建虚拟环境 → 安装 pip 依赖 → 下载 CloakBrowser 二进制 → 安装前端 npm 包 → 启动后端&前端 → 自动打开浏览器。

```bash
# 环境自检（不启动服务，只检测依赖是否就绪）
./check_env.sh
```

---

### 手动分步安装

```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装 Python 依赖（含 CloakBrowser）
pip install -r requirements.txt

# 安装 CloakBrowser 二进制（首次需联网下载 ~200MB）
python -m cloakbrowser install

# 安装前端依赖
cd frontend && npm install && cd ..
```

### 修改配置

编辑 `config/settings.yaml`：

```yaml
search:
  keywords:
    - "美妆带货"      # 修改为你的目标关键词
    - "零食推荐"
  max_users_per_keyword: 50

filter:
  min_followers: 1    # 粉丝下限（万）
  min_total_likes: 10000

dm:
  template: |
    你好 {nickname}，我是某某品牌的商务负责人...
  daily_limit: 20     # 每日最多发送条数
```

### 启动服务

**推荐：一键启动（自动安装依赖）**

```bash
./start.sh              # macOS / Linux
.\start.ps1             # Windows PowerShell
```

**分开启动（调试时用）**

```bash
# 终端 1：后端
./start_backend.sh

# 终端 2：前端
./start_frontend.sh
```

**命令行直接运行爬虫（不需要管理后台）**

```bash
# 使用配置文件中的关键词
./run_crawler.sh

# 指定关键词和参数
./run_crawler.sh --keywords "美妆带货" "护肤分享" --min-followers 5 --min-likes 50000

# 只采集，不发私信
./run_crawler.sh --keywords "带货主播" --no-dm
```

---

## 使用流程

### 通过管理后台

1. 打开 `http://localhost:5173`
2. 点击右上角 **「扫码登录」**，在弹出的浏览器中完成扫码
3. 进入 **「任务管理」** 页面，输入关键词，设置筛选条件，点击「立即执行」
4. 任务后台运行，实时查看进度
5. 进入 **「达人列表」** 浏览收录的达人，点击「详情」查看画像，点击「主页」跳转抖音

### 管理后台功能

| 页面 | 功能 |
|------|------|
| 数据概览 | 统计卡片 + 快速发起任务 + 实时任务进度 |
| 达人列表 | 分页展示、按粉丝/状态筛选、批量发私信 |
| 达人详情 | 完整画像（粉丝/点赞/作品/橱窗/签名）+ 访问主页 + 单独发私信 |
| 任务管理 | 历史任务时间线、实时进度条 |
| 系统设置 | 登录状态管理、配置说明 |

---

## 关键技术说明

### CloakBrowser
- 使用 `pip install cloakbrowser`，是 Playwright 的 drop-in 替代品
- 在 C++ 层面修改 Chromium 指纹，绕过 navigator.webdriver、Canvas 指纹等检测
- 持久化 Profile 保存登录 Cookie，避免每次重新扫码
- 启用 `humanize=True` 模拟真实鼠标/键盘行为

### JS 逆向签名（X-Bogus）
- 优先在已打开的抖音页面内直接调用浏览器 JS 运行时生成签名（最准确）
- 降级方案：提取 `webmssdk.js` 到本地，通过 `execjs` 离线执行
- `msToken` 直接从登录后的 Cookie 中读取

### 防封策略
- 每日私信上限（默认 20 条）
- 随机间隔 60~180 秒
- 已发送记录持久化，避免重复
- 搜索请求随机翻页间隔 1.5~3.5 秒

---

## API 接口文档

后端启动后访问 `http://127.0.0.1:8000/docs` 查看完整 Swagger 文档。

主要接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/login/status` | 登录状态检测 |
| POST | `/api/login/qrcode` | 触发二维码登录 |
| GET | `/api/kols` | 达人列表（分页+筛选） |
| GET | `/api/kols/{id}` | 达人详情 |
| POST | `/api/tasks/run` | 触发采集任务 |
| GET | `/api/tasks/{id}` | 任务状态查询 |
| POST | `/api/dm/send` | 手动发送私信 |
| GET | `/api/stats` | 整体统计数据 |

---

## 注意事项

1. **合规使用**：本项目仅供学习研究，私信营销请遵守平台规则，控制发送频率
2. **Cookie 有效期**：抖音 Cookie 一般有效 30 天，过期后需重新扫码登录
3. **首次运行**：CloakBrowser 需要联网下载约 200MB 的 Chromium 二进制
4. **macOS 首次运行**：系统可能拦截未签名应用，执行 `xattr -cr ~/.cloakbrowser/` 解除
5. **代理（可选）**：在 `config/settings.yaml` 中配置 `cloak_browser.proxy` 使用住宅代理可提升稳定性
