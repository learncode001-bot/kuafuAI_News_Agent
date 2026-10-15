# 每日 AI 新闻助手 Agent

我的课程作业（也是面试作品），用 Python + LangChain 写的 ReAct 模式 Agent。
在页面上填个用户名、订几个感兴趣的话题、设个时间，它每天到点自动整理一份新闻简报推到页面上，不用手动刷新。

新闻来源是本地 `news_data/` 目录下的 txt 文件。题目说新闻从哪来自己定，我没做爬虫，直接用本地文件——好处是稳定、可复现，也不会爬到一半被反爬。

## 跑起来（亲测 5 分钟左右）

需要 Python 3.10+。

```bash
git clone https://github.com/learncode001-bot/kuafuAI_News_Agent.git
cd kuafuAI_News_Agent
pip install -r requirements.txt
```

pip 太慢就换清华源：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

然后复制一份配置文件：

```bash
copy config.template.py config.py    # Windows
cp config.template.py config.py      # macOS / Linux
```

接下来分两种情况：

**情况一：手头没有大模型 API Key** —— 打开 `config.py`，把 `USE_LLM_API = True` 改成 `False`，走离线模式，不用联网也能跑完整流程。

**情况二：有 Key** —— Key 不设进文件，设成环境变量（防泄露）：

```bash
setx NEWS_AGENT_API_KEY "sk-你的key"   # Windows，设完要【关掉重开终端】才生效
export NEWS_AGENT_API_KEY=sk-你的key   # macOS / Linux
```

默认接的是 DeepSeek。想用别的平台（智谱、Kimi、通义、OpenAI 都行），改 `config.py` 里的 `API_URL` 和 `MODEL` 两个值就可以。

最后启动：

```bash
python app.py
```

浏览器打开 `http://localhost:5000`，页面顶部会显示当前是【在线LLM-API模式】还是【离线模拟模式】。

试用流程：右边输入用户名 → 订阅里填话题（比如 `AI, 时政`）和一个 2 分钟后的时间 → 页面别关，到点左边会自动弹出简报。等不及就点「立即生成」或订阅条目上的「▶ 立即推」。

## 在线和离线两种模式

**在线模式**是主角：标准的 ReAct 循环——把任务和 5 个工具的描述发给大模型，模型自己决定调哪个工具、调几次、什么时候停，我只负责在本地执行工具并把结果回传，最多 15 轮防止死循环。调哪个工具的顺序没有一行代码写死。

这里有个实现上的取舍：我没用 openai 官方 SDK，而是用 requests 直接请求 OpenAI 兼容接口。原因是我电脑环境里的依赖被别的包污染过，SDK 一直报错，requests 直连反而最稳，还少一层依赖。

**离线模式**是备胎：演示、答辩现场没网或者没 Key 的时候用。我自己写了个 FakeListLLM，根据用户话题匹配本地素材文件、动态拼出 ReAct 轨迹，内容同样是从本地 txt 真实读出来的，不是写死的文案。

调 API 可能遇到的异常都做了兜底：Key 不对（401）、网络超时、对方服务繁忙（503）等，页面会显示具体原因而不是程序崩溃。503 这种临时故障会自动重试 3 次；定时推送如果失败了，5 分钟后还会自动补推一次。

## 密钥怎么处理的

- Key 只走环境变量 `NEWS_AGENT_API_KEY`，任何文件里都不存明文
- `config.py` 在 `.gitignore` 里，不会提交进仓库（仓库里的是模板 `config.template.py`）
- 我自查过整个 git 历史，确认 Key 从来没被提交过

## 项目结构

```
app.py               Flask 入口：页面路由、订阅管理、后台定时推送线程
agent.py             ReAct Agent：在线循环 + 离线模拟，核心都在这
tools.py             5 个工具
config.template.py   配置模板，复制成 config.py 用
runner.py            命令行单次运行入口
news_data/           本地新闻素材（AI/科技/创投/时政/考公 5 个 txt）
templates/index.html 前端页面
briefs/<用户名>/      运行后生成，各用户的简报归档
```

## 关于数据库：没有用

全部数据都是文件，所以仓库里没有建表 SQL（也不需要）：

- 订阅存在 `subscriptions.json`
- 每个用户的简报存在 `briefs/<用户名>/` 下，按用户名隔离
- 推送记录写在 `scheduler.log`

## 5 个工具

题目要求的 5 个，在 `tools.py` 里：

- `list_dir(path)` 列目录
- `read_file(path)` 读文件
- `search_content(keyword, dir)` 按关键词搜文件内容
- `write_file(path, content)` 写文件
- `bash(command)` 执行 shell 命令（加了危险命令黑名单，rm -rf 这类直接拒绝）

## 怎么保证「工具顺序是 LLM 决定的」

除了 ReAct 循环本身，我在 system prompt 里给模型交代了两样东西：

1. **素材地图**：告诉它 news_data 下每个文件对应什么话题（ai_news 是 AI、politics_news 是时政……），免得它把「时政」读到「考公」里去
2. **几条硬规矩**：新闻只能用工具读本地文件、不许用自己脑子里的知识编、搜不到就老实输出「未发现相关内容」、结束前必须把简报写进文件

可以试试订一个素材库里没有的话题（比如「足球」），它会老老实实告诉你没找到，不会编新闻——这是我特意留的演示点。

## 开发过程中踩过的坑（真实记录）

这部分留下来给以后的自己，面试被问「遇到过什么难点」也基本在这：

- **LangChain 版本兼容**：不同版本 API 变动大，FakeListLLM 的 `.predict()` 在新版被删了，最后改用 `.invoke()`，离线的 ReAct 循环也是手写的，绕开了解析器差异
- **重复推送**：Flask 开了 debug 模式后，重载机制会起父子两个进程，调度线程被启动了两份，同一订阅到点推两遍。最后用 `WERKZEUG_RUN_MAIN` 判断，只在真正跑服务的子进程里启动线程
- **简报串内容**：`daily_brief.md` 是共享产物文件，上一次运行残留的内容被归档给了下一个订阅（订「生物」收到「历史」简报，人都懵了）。现在每次运行前先删旧文件，再加锁防并发
- **DeepSeek 高峰期 503**：到点推送正好撞上对方服务过载，当天简报就丢了。后来加了两层保险：API 调用自动退避重试，调度器失败 5 分钟后自动补推

## 命令行玩法（可选）

```bash
python runner.py "AI, 科技"      # 按 config.py 的配置自动选模式
python runner.py "AI" --offline  # 强制离线
```
