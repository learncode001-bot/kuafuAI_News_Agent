# 每日 AI 新闻助手 Agent

我选择的是选项A，基于 Python + LangChain 实现 ReAct 模式的智能 Agent。
用户填写用户名，订阅感兴趣的新闻话题并设置推送时间，服务会在指定时刻自动生成新闻简报并推送到网页前端，无需手动刷新页面。
新闻素材来源于本地 `news_data/` 目录下文本文件。本项目未使用爬虫，本地文件数据源稳定、可复现，规避反爬限制。
# 环境要求
    Python 3.10+
# 获取源码：
Git克隆仓库（前提是已安装Git工具）
git clone https://github.com/learncode001-bot/kuafuAI_News_Agent.git
cd kuafuAI_News_Agent
如果本机没有安装Git，可以点击仓库页面  Code → Download ZIP  下载压缩包解压，跳过上面git相关命令。
# 安装依赖：
pip install -r requirements.txt
下载缓慢可换清华源：
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# 复制配置模版生成本地配置：
copy config.template.py config.py    # Windows
cp config.template.py config.py      # macOS / Linux
# 运行模式二选一
情况一：无大模型 API Key —— 离线模拟模式
打开 config.py，将 USE_LLM_API = True改成 USE_LLM_API = False，走离线模式，不用联网也能完整流程。
情况二：有大模型API Key —— 在线LLM模式
密钥 不写入代码文件，设成环境变量，防泄露：
setx NEWS_AGENT_API_KEY "sk-你的key"   # Windows，设完要【关掉重开终端】才生效
export NEWS_AGENT_API_KEY=sk-你的key   # macOS / Linux
默认对接的是 DeepSeek接口。想用别的平台（智谱、Kimi、通义、OpenAI 都行），修改 config.py 里的 API_URL 和 MODEL 两个值就可以。
# 启动项目
    python app.py
浏览器打开 http://localhost:5000，页面顶部会显示当前是【在线LLM-API模式】还是【离线模拟模式】。
# 试用流程
1、右边输入用户名 
2、订阅里填话题（比如 `AI, 时政`）和一个 1分钟后的时间 
3、页面不要关闭，到达设定时间左边会自动弹出简报。
# 两种运行模式说明
1、在线LLM‑API模式（主模式）
实现标准ReAct循环：将任务与5个工具描述提交给大模型，工具调用选择、调用次数、终止时机全部由大模型自主决策，业务代码没有写死工具执行顺序，单次任务最多15轮调用，防止死循环。
实现取舍：项目没有使用OpenAI官方SDK，直接通过requests请求OpenAI兼容接口。
开发环境存在依赖包污染，SDK初始化会报错；使用requests直连减少依赖，兼容性更强，方便切换各类大模型服务。
2、离线模拟模式（兜底演示模式）
适用于无网络、没有API‑Key的演示场景。
内部实现FakeListLLM，根据用户订阅话题匹配本地素材，动态生成完整ReAct执行轨迹；读取的新闻内容依旧来自本地txt素材，并非硬编码写死的静态文本。
异常容错处理
针对API调用各类异常做了容错：密钥无效(401)、网络超时、服务端繁忙(503)等错误会在页面展示原因，不会直接造成程序崩溃。
瞬时服务故障：API请求自动退避重试，最多重试3次
定时推送失败：会在5分钟后执行一次自动补推
# 密钥安全设计
1、 API密钥仅读取环境变量  NEWS_AGENT_API_KEY ，不在任何业务文件保存明文密钥
2、 config.py  加入  .gitignore ，不会提交至版本仓库，仓库仅提供模板文件  config.template.py 
3.、已自查Git提交历史，确认没有密钥明文提交记录项目结构
# 项目结构
app.py               Flask 入口：页面路由、订阅管理、后台定时推送线程
agent.py             ReAct Agent核心：在线循环 + 离线模拟
tools.py              5个业务工具实现
config.template.py     配置模板，复制成 config.py 使用
runner.py            命令行单次运行入口
news_data/           本地新闻素材（AI/科技/创投/时政/考公 5 个 txt）
templates/index.html  前端页面
briefs/<用户名>/      运行后生成，各用户的简报归档
# 存储说明（未使用数据库）
本项目不依赖外部数据库，全部业务数据使用本地文件持久化，因此不提供建表SQL脚本：
用户订阅信息： subscriptions.json 
用户简报归档： briefs/<用户名>/ ，按用户名做数据隔离
调度运行日志： scheduler.log  
# 五个工具（tools.py）
list_dir(path) ：列出目录内容
read_file(path) ：读取文件内容
search_content(keyword, dir) ：按关键词检索文件
write_file(path, content) ：写入文件
bash(command) ：执行系统shell命令，内置危险命令黑名单，拦截删除、格式化等高风险操作
# 如何保证工具调用顺序由大模型决定
1. ReAct循环本身不硬编码任何工具调用逻辑，全部依靠模型返回tool_calls做本地执行
2. System Prompt内置素材地图，明确告知模型news_data下各个文件对应的话题，避免跨话题读取素材
3. 强制约束规则：仅允许通过工具读取本地素材，禁止大模型使用内部知识编造新闻；检索无结果必须如实输出未找到内容；任务结束前需要将简报写入目标文件。开发过程中踩过的坑（真实记录）
# 开发踩坑记录：
1. LangChain版本兼容：不同版本接口变动较大，新版移除FakeListLLM.predict()，改用invoke()接口；手写离线ReAct循环规避版本解析差异。
2. 重复推送问题：Flask debug热重载会生成父子双进程，后台调度线程被重复启动，造成重复推送。通过 WERKZEUG_RUN_MAIN 环境变量判断，仅在服务子进程启动调度线程。
3. 简报内容串扰：产物文件 daily_brief.md 存在运行残留，旧任务残留内容被归档至新任务；解决方案：每次运行前清除产物文件，增加运行互斥锁防止并发冲突。
4. 大模型服务503过载：定时推送遇到服务商高峰过载，直接丢失当日简报；增加API退避重试，调度失败支持5分钟自动补推。
