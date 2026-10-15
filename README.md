# 每日 AI 新闻助手（ReAct Agent）

基于 **Python + LangChain** 的 ReAct 模式新闻简报 Agent：用户按偏好订阅话题，Agent 每天定时自动整理简报并推送到页面。**工具调用顺序完全由 LLM 决定**——调哪个工具、调用几次、什么时候停，代码不写死任何线性流程。

支持 **在线 LLM-API**（DeepSeek 等 OpenAI 兼容接口）与 **离线模拟**（无需联网/密钥）双模式。

---

## 快速开始（5 分钟跑起来）

> 环境要求：Python 3.10+，Windows / macOS / Linux 均可

**1. 克隆并进入项目**

```bash
git clone https://github.com/learncode001-bot/kuafuAI_News_Agent.git
cd kuafuAI_News_Agent
```

**2. 一键安装依赖**

```bash
pip install -r requirements.txt
```

**3. 创建本机配置文件**

```bash
# Windows (cmd)
copy config.template.py config.py
# macOS / Linux
cp config.template.py config.py
```

**4. 二选一：有 Key 走在线，没 Key 走离线**

- **没有 API Key（最快体验）**：打开 `config.py`，把 `USE_LLM_API = True` 改成 `False` 即可，跳到第 5 步。
- **有 API Key（在线模式）**：把密钥设为环境变量（密钥不走配置文件，防止泄露）：

```bash
# Windows（永久生效，设完重开终端）
setx NEWS_AGENT_API_KEY "sk-你的key"
# Windows cmd（仅当前窗口临时）
set NEWS_AGENT_API_KEY=sk-你的key
# macOS / Linux
export NEWS_AGENT_API_KEY=sk-你的key
```

默认对接 DeepSeek（`https://api.deepseek.com/v1` + `deepseek-chat`）。用其它平台时，在 `config.py` 里改 `API_URL` / `MODEL` 即可，对照表见下文。

**5. 启动服务**

```bash
python app.py
```

浏览器打开 `http://localhost:5000`，看到页面顶部徽标显示【在线LLM-API模式】或【离线模拟模式】即成功。

**6. 体验完整流程**

1. 页面右侧输入你的**用户名**（这就是身份，订阅/推送/历史都跟它走）
2. 「订阅推送」里填关键字（如 `AI`）+ 推送时间（建议填 2 分钟后），点保存
3. 保持页面开着，到点后左侧主阅读区**自动弹出**新简报，无需任何操作
4. 等不及也可以点「立即生成」或订阅条目的「▶ 立即推」手动触发

---

## 两种运行模式

| | 离线模拟模式 | 在线 LLM-API 模式 |
|---|---|---|
| 开关 | `config.py` 里 `USE_LLM_API = False` | `USE_LLM_API = True` + 环境变量密钥 |
| 依赖 | 无需联网、无需 Key | 任意 OpenAI 兼容 API Key |
| 原理 | `FakeListLLM` 按用户话题动态规划 ReAct 轨迹，从本地文件真实取材 | 标准 ReAct Agent-Loop：任务+5 个工具描述发给大模型，模型自主决策调用，本地执行工具后结果回传，多轮循环直到模型自己判断停止 |

两种模式共用同一套 5 个工具，业务逻辑完全一致。

### 各平台 API 配置对照（在线模式）

| 平台 | API_URL | MODEL 示例 |
|---|---|---|
| DeepSeek（默认） | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |

> 异常兜底：网络错误、Key 无效（401）、模型名错误、返回格式异常都会被捕获并在页面友好提示，程序不会崩溃。

### 密钥安全

- 密钥**只走环境变量** `NEWS_AGENT_API_KEY`，任何文件都不保存明文密钥
- `config.py` 是本机私有文件，已加入 `.gitignore`，**请勿提交，也不要删除 .gitignore 中该条目**
- 仓库中的 `config.template.py` 是模板，复制改名即为 `config.py`

---

## 项目结构

```
├── app.py               # Flask 入口：页面路由、订阅管理、后台定时推送线程
├── agent.py             # ReAct Agent：在线 Agent-Loop（主）+ LangGraph（兜底）+ 离线手动 ReAct
├── tools.py             # 5 个工具：list_dir / read_file / search_content / write_file / bash
├── config.template.py   # 配置模板（入库）；复制为 config.py 后使用（config.py 不入库）
├── runner.py            # 终端单次运行入口
├── requirements.txt     # 依赖清单，一键起环境
├── news_data/           # 新闻素材库（本地 txt，不做联网爬虫）
├── templates/index.html # 前端页面（左侧新闻阅读区 + 右侧功能面板）
└── briefs/<用户名>/     # 运行时生成：每个用户的简报归档（不入库）
```

## 数据存储说明（无数据库）

本项目**不使用数据库**，无需建表 SQL / migration 脚本。全部数据以文件形式存储：

| 数据 | 位置 | 说明 |
|---|---|---|
| 新闻素材 | `news_data/*.txt` | 入库，Agent 的唯一新闻来源 |
| 用户订阅 | `subscriptions.json` | 运行时生成，用户名/话题/每日推送时间 |
| 简报归档 | `briefs/<用户名>/*.md` | 运行时生成，按用户隔离 |
| 推送日志 | `scheduler.log` | 运行时生成 |

## 工具集（5 个，签名与题目一致）

| 工具 | 签名 | 说明 |
|---|---|---|
| list_dir | `list_dir(path)` | 列出目录内容 |
| read_file | `read_file(path)` | 读取文件（UTF-8，失败降级 GBK） |
| search_content | `search_content(keyword, dir)` | 目录下按关键词搜索文件内容 |
| write_file | `write_file(path, content)` | 写入文件（自动创建父目录） |
| bash | `bash(command)` | 执行 shell 命令（内置危险命令黑名单） |

## 核心设计：工具调用顺序由 LLM 决定

在线模式是标准 **function-calling ReAct 循环**（[agent.py](agent.py) `_build_online_react_runner`）：

1. 把用户任务 + 5 个工具的 JSON Schema 发给大模型
2. 模型自主返回 `tool_calls`（调哪个、参数是什么）
3. 本地执行工具，结果以 `role=tool` 消息回传
4. 多轮循环，模型自己判断素材足够后停止调用、输出简报
5. 最多 15 轮防死循环；全流程终端打印每轮决策，可观测

此外通过 system_prompt 向模型声明**素材地图**（哪个文件属于哪个话题）与**硬性规则**（只允许用工具读本地文件、禁止用内置知识编造新闻、检索为空时如实输出「未发现相关内容」），保证简报真实可追溯。

## 终端单次运行（可选）

```bash
python runner.py "AI, 科技"      # 按 config.py 自动选择模式
python runner.py "AI" --offline  # 强制离线模拟
```
