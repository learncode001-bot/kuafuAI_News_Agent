"""
agent.py —— 用 LangChain ReAct 模式构建的「每日 AI 新闻助手」。
LLM 自主决定工具调用顺序，不硬编码流程。

用法（终端）:
    python runner.py "AI, 科技"     # 在线（需 OPENAI_API_KEY）
    python runner.py "AI" --offline  # 离线演示（FakeListLLM）
"""

import os
from pathlib import Path

from tools import ALL_TOOLS


# ==================================================================
# 0) config.py 接线：把在线 API 配置同步到环境变量（app.py 零改动）
# ==================================================================
def _apply_config():
    """读 config.py；USE_LLM_API=True 且有 KEY 时写入环境变量，
    这样 app.py 原有的 os.environ.get("OPENAI_API_KEY") 判断自动感知。"""
    try:
        import config as _cfg
    except Exception:
        print("[CONFIG] 未找到 config.py → 离线模拟模式")
        return
    if getattr(_cfg, "USE_LLM_API", False) and getattr(_cfg, "API_KEY", ""):
        os.environ.setdefault("OPENAI_API_KEY", _cfg.API_KEY)
        if getattr(_cfg, "API_URL", ""):
            os.environ.setdefault("OPENAI_BASE_URL", _cfg.API_URL)
        if getattr(_cfg, "MODEL", ""):
            os.environ.setdefault("LLM_MODEL", _cfg.MODEL)
        print(f"[CONFIG] 已加载 config.py → 在线 LLM-API 模式 (model={_cfg.MODEL or '默认'})")
    else:
        print("[CONFIG] config.py 未启用在线 API（USE_LLM_API=False 或无 KEY）→ 离线模拟模式")


_apply_config()


# ==================================================================
# 1) LLM 工厂
# ==================================================================
def _get_chat_llm():
    """返回支持 tool-calling 的 ChatModel（在线模式）。"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
            temperature=0.2,
            openai_api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL"),
        )
        print(f"[LLM] ChatOpenAI 就绪  model={llm.model_name}")
        return llm
    except Exception as e:
        print(f"[LLM] ChatOpenAI 失败: {e}")
        return None


def _make_fake_llm():
    """
    动态离线 Fake LLM —— 真正根据用户 topic 决定工具调用。

    逻辑：
      1. 从 prompt 的 user_input 里提取用户关心的 topic（比如 "AI, 科技, 创投"）。
      2. 根据 topic 里的关键词匹配 news_data/ 下有哪些相关文件：
         - "AI" / "人工智能" → ai_news_*.txt
         - "科技" / "tech" / "前沿" → tech_news_*.txt
         - "创投" / "创业" / "startup" → startup_news_*.txt
         - 匹配不上就不读那个文件。
      3. 动态生成 ReAct 轨迹：list_dir → 读匹配到的文件 → search_content(用 topic 关键词)
      4. 最后从 Observation 构建简报 + write_file + Final Answer。
    """
    import re as _re
    import json
    import os as _os

    NEWS_DIR = _os.path.join(_os.path.dirname(__file__), "news_data")

    # 文件路由表：关键词 → 文件名
    FILE_ROUTES = {
        "ai_news_": {"关键词": ["ai", "人工智能", "模型", "大模型", "llm", "gpt", "夸父", "alpha"],
                     "默认文件": "ai_news_20261014.txt", "分类名": "AI 头条"},
        "tech_news_": {"关键词": ["科技", "tech", "前沿", "芯片", "m5", "fsd", "鸿蒙", "量子", "vr"],
                       "默认文件": "tech_news_20261014.txt", "分类名": "科技前沿"},
        "startup_news_": {"关键词": ["创投", "创业", "startup", "机器人", "融资", "ipo", "英伟达", "blackwell", "temu"],
                          "默认文件": "startup_news_20261014.txt", "分类名": "创投与商业"},
        "politics_news_": {"关键词": ["时政", "政治", "政策", "国际", "外交", "民生", "两部门", "海峡"],
                           "默认文件": "politics_news_20261014.txt", "分类名": "时政要闻"},
        "gov_news_": {"关键词": ["考公", "公务员", "事业单位", "编制", "行测", "申论", "国考", "省考"],
                      "默认文件": "gov_news_20261014.txt", "分类名": "考公资讯"},
    }

    # ------------------------------------------------------------------
    # 从 prompt 解析用户 topic（prompt 里有 "Question: 请为我整理...偏好：XXX"）
    # ------------------------------------------------------------------
    def _extract_topic(prompt_text: str) -> str:
        m = _re.search(r"偏好[：:]\s*([^。\n]+)", prompt_text)
        if m:
            return m.group(1).strip()
        return "AI, 科技"

    # ------------------------------------------------------------------
    # 根据 topic 决定读哪些文件、搜什么关键词
    # ------------------------------------------------------------------
    def _plan_tools(topic: str):
        """
        返回 (files_to_read: list[str], search_keywords: list[str], has_any_matched: bool)
        """
        topic_lower = topic.lower()
        files_to_read = []
        matched_categories = []

        for prefix, route in FILE_ROUTES.items():
            for kw in route["关键词"]:
                if kw in topic_lower:
                    files_to_read.append(route["默认文件"])
                    matched_categories.append(route["分类名"])
                    break

        # 去重保持顺序
        seen = set()
        files_to_read = [f for f in files_to_read if not (f in seen or seen.add(f))]

        # search 关键词：取 topic 里的中文逗号分隔项 + 原始英文词
        keywords = [k.strip() for k in _re.split(r"[,，]", topic) if k.strip()]

        has_any_matched = bool(files_to_read)

        # 如果完全没匹配上任何文件类，还是要尝试一下 search_content，用 topic 里的关键词
        return files_to_read, keywords, has_any_matched, matched_categories

    # ------------------------------------------------------------------
    # 工具 ReAct 行生成器
    # ------------------------------------------------------------------
    def _react(thought, action, action_input_dict):
        j = json.dumps(action_input_dict, ensure_ascii=False)
        return f"Thought: {thought}\nAction: {action}\nAction Input: {j}"

    # ------------------------------------------------------------------
    # 从 scratchpad 解析 Observation（同之前的逻辑：只保留 read_file 完整内容）
    # ------------------------------------------------------------------
    def _extract_observations(prompt_text: str) -> list[str]:
        observations = []
        pattern = _re.compile(
            r"Observation\s*[:：]\s*(.*?)(?=\nThought:|\nFinal Answer:|\Z)",
            _re.DOTALL
        )
        for m in pattern.finditer(prompt_text):
            obs = m.group(1).strip()
            if not obs:
                continue
            first_line = obs.splitlines()[0].strip() if obs else ""
            if first_line.startswith("===") and "[错误]" not in obs and "[异常]" not in obs:
                observations.append(obs)
        return observations

    def _build_brief_from_observations(observations: list[str], topic: str):
        file_sources = []
        sections = []
        for obs in observations:
            header_match = _re.search(r"===\s*(.*?)\s*===", obs)
            header = header_match.group(1).strip() if header_match else "新闻"
            file_sources.append(header)
            lines = [l.strip() for l in obs.splitlines() if l.strip()]
            content_lines = lines[1:] if header_match else lines
            sections.append(f"## {header}\n" + "\n".join(content_lines))

        if not sections:
            brief_content = (
                f"# 每日新闻简报（{topic}）\n\n"
                f"抱歉，在本地 news_data/ 目录下没有找到与「{topic}」相关的新闻素材。\n"
                f"现有素材覆盖：AI / 科技前沿 / 创投与商业 / 时政 / 考公。\n"
                f"（离线演示模式：内容来自本地 txt 文件）"
            )
        else:
            brief_content = f"# 每日新闻简报（{topic}）\n\n" + "\n\n".join(sections)

        return brief_content, file_sources

    # ------------------------------------------------------------------
    # 动态 Fake LLM 类
    # ------------------------------------------------------------------
    class _DynamicFakeLLM:
        def __init__(self):
            self._step = 0
            self._plan = None
            self._generated_steps = []

        def reset(self):
            """单次会话开始前调用 —— 清掉上一次 Flask 请求残留的状态。"""
            self._step = 0
            self._plan = None
            self._generated_steps = []

        def invoke(self, prompt_text: str):
            response = self._next_response(prompt_text)
            class _FakeMsg:
                def __init__(self, content):
                    self.content = content
            return _FakeMsg(response)

        def _ensure_plan(self, prompt_text: str):
            """第一次调用时从 prompt 提取 topic 并规划工具调用。"""
            if self._plan is None:
                topic = _extract_topic(prompt_text)
                self._plan = _plan_tools(topic)
                files_to_read, keywords, has_any, categories = self._plan

                # 生成动态 ReAct 步骤
                steps = []
                steps.append(_react("先看看 news_data 目录里有哪些新闻文件。",
                                    "list_dir", {"path": "news_data"}))

                if has_any:
                    # 读匹配到的文件
                    for fname in files_to_read:
                        # 分类名从 FILE_ROUTES 反查
                        cat = ""
                        for p, r in FILE_ROUTES.items():
                            if fname == r["默认文件"]:
                                cat = r["分类名"]
                                break
                        steps.append(_react(
                            f"发现相关文件，读取 {cat}（{fname}）。",
                            "read_file", {"path": f"news_data/{fname}"}
                        ))
                    # 用 topic 关键词搜
                    for kw in keywords[:2]:  # 最多搜前 2 个关键词
                        steps.append(_react(
                            f"用关键词「{kw}」二次确认，有没有漏掉的。",
                            "search_content", {"keyword": kw, "directory": "news_data"}
                        ))
                else:
                    # 没匹配上任何文件类：直接用 topic 里的关键词搜
                    for kw in keywords[:3]:
                        steps.append(_react(
                            f"topic「{topic}」没有匹配到预设分类，先用关键词「{kw}」搜索看看。",
                            "search_content", {"keyword": kw, "directory": "news_data"}
                        ))

                self._generated_steps = steps

        def _next_response(self, prompt_text: str) -> str:
            self._ensure_plan(prompt_text)
            topic = _extract_topic(prompt_text)
            files_to_read, keywords, has_any, categories = self._plan
            observations = _extract_observations(prompt_text)

            # 阶段 1：工具调用
            if self._step < len(self._generated_steps):
                resp = self._generated_steps[self._step]
                self._step += 1
                return resp

            # 阶段 2：write_file
            if self._step == len(self._generated_steps):
                brief_content, sources = _build_brief_from_observations(observations, topic)
                self._step += 1
                action_input = json.dumps(
                    {"path": "daily_brief.md", "content": brief_content},
                    ensure_ascii=False
                )
                if has_any:
                    thought = (
                        f"素材齐了（匹配到 {len(categories)} 类: {', '.join(categories)}；"
                        f"实际读取 {len(observations)} 个文件），写入 daily_brief.md。"
                    )
                else:
                    if observations:
                        thought = (
                            f"虽然「{topic}」没匹配到预设分类，但 search 到了一些相关内容"
                            f"（{len(observations)} 段），整理写入 daily_brief.md。"
                        )
                    else:
                        thought = (
                            f"「{topic}」在本地 news_data/ 下没搜到相关素材，把空结果说明"
                            f"写入 daily_brief.md。"
                        )
                return f"Thought: {thought}\nAction: write_file\nAction Input: {action_input}"

            # 阶段 3：Final Answer
            if self._step == len(self._generated_steps) + 1:
                self._step += 1
                if has_any and observations:
                    answer = (
                        f"已为您整理「{topic}」新闻简报，匹配到 {len(categories)} 个分类、"
                        f"读取 {len(observations)} 个文件。简报已保存到 daily_brief.md。\n\n"
                        f"（离线演示模式：内容来自本地 news_data/ 真实文件）"
                    )
                elif observations:
                    answer = (
                        f"「{topic}」没有精准匹配的分类，但通过关键词搜索找到了"
                        f"{len(observations)} 段相关内容，已写入 daily_brief.md。\n\n"
                        f"提示：现有素材覆盖 AI / 科技 / 创投，可以试试「AI」或「科技」。"
                    )
                else:
                    answer = (
                        f"抱歉，本地 news_data/ 目录下没有找到与「{topic}」相关的素材。\n\n"
                        f"现有素材覆盖：AI / 科技前沿 / 创投与商业 / 时政 / 考公。\n"
                        f"试试搜索这些关键词，或者把新的 txt 文件放进 news_data/ 目录～"
                    )
                return f"Thought: 简报已写入，给用户总结一下。\nFinal Answer: {answer}"

            self._step += 1
            return "Final Answer: 简报已生成完毕，详见 daily_brief.md。"

    return _DynamicFakeLLM()


# ==================================================================
# 2) 路径 A-1：在线模式 —— 标准 ReAct Agent-Loop（openai SDK 原生 tool-calling）
# ==================================================================
def _build_online_react_runner():
    """
    标准 ReAct Agent-Loop（在线模式）：
      1. 任务 + 5 个工具的描述 → 传给大模型 API
      2. 大模型自己思考，自主决定调用哪个工具、调用几次
      3. 本地执行工具，把结果回传给大模型
      4. 多轮循环，大模型自己判断信息足够后停止调用，输出最终新闻简报
      5. 工具调用顺序完全由模型决定，代码不写死任何流程

    实现：requests 直连 OpenAI 兼容接口（不依赖 openai SDK，避免 httpx 环境兼容问题）
    """
    import json as _json
    import requests

    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL") or "gpt-4o-mini"
    chat_url = f"{base_url}/chat/completions"

    # 工具 schema：优先用 langchain 的转换器（从 5 个工具的签名自动生成），失败则手写兜底
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool
        oai_tools = [convert_to_openai_tool(t) for t in ALL_TOOLS]
    except Exception:
        def _fn(name, desc, props, req):
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props, "required": req}}}
        oai_tools = [
            _fn("list_dir", "列出目录下的文件和子目录",
                {"path": {"type": "string", "description": "目录路径"}}, ["path"]),
            _fn("read_file", "读取一个文本文件的全部内容",
                {"path": {"type": "string", "description": "文件路径"}}, ["path"]),
            _fn("search_content", "在某目录的所有文件里搜索关键词，返回命中行",
                {"keyword": {"type": "string", "description": "关键词"},
                 "directory": {"type": "string", "description": "目录路径"}}, ["keyword", "directory"]),
            _fn("write_file", "把文本内容写入文件（覆盖）",
                {"path": {"type": "string", "description": "文件路径"},
                 "content": {"type": "string", "description": "要写入的内容"}}, ["path", "content"]),
            _fn("bash", "执行一条只读的 shell 命令（带危险命令黑名单）",
                {"command": {"type": "string", "description": "命令"}}, ["command"]),
        ]
    tool_map = {t.name: t for t in ALL_TOOLS}

    system_prompt = (
        "你是一个「每日新闻简报助手」。\n"
        "【硬性规则，违反即为错误】：\n"
        "1. 所有新闻素材只能通过工具读取本地 news_data/ 目录下的 txt 文件获取；\n"
        "2. 绝对不允许使用你内置知识库里的知识来回答新闻查询——哪怕你\"知道\"某条新闻，"
        "只要不是工具从文件里读出来的，就禁止写进简报；\n"
        "3. 必须先调用 list_dir 查看目录，再用 read_file / search_content 获取资料，"
        "不允许跳过工具直接输出简报；\n"
        "4. 工具检索返回空内容时，如实输出「未发现相关内容」，"
        "严禁自己编造、回忆、推测任何新闻内容。\n"
        "素材地图（文件 → 所属话题，严格遵守）：\n"
        "- ai_news_*.txt → 话题「AI」\n"
        "- tech_news_*.txt → 话题「科技」\n"
        "- startup_news_*.txt → 话题「创投/商业」\n"
        "- politics_news_*.txt → 话题「时政」\n"
        "- gov_news_*.txt → 话题「考公/公务员」（独立话题，不隶属「时政」）\n"
        "工作方式（ReAct，全部由你自主决策）：\n"
        "1. 先用 list_dir 查看 news_data/ 里有哪些文件；\n"
        "2. 只读取所属话题与用户偏好匹配的文件；话题不匹配的文件不要读——即使内容看起来沾边"
        "（例如：偏好「时政」时 gov_news 里的考公新闻不算时政，不读不收）；\n"
        "3. 必要时用 search_content 在匹配的文件里检索关键词；\n"
        "4. 当你判断素材已足够覆盖用户偏好时，用 write_file 把简报写入 daily_brief.md；\n"
        "5. 写完后停止调用工具，直接输出最终回复（简报已保存的说明）。\n"
        "若所有匹配文件检索后均无相关内容，简报正文写「未发现相关内容」，"
        "并如实说明检索过的文件与关键词，不得用内置知识补内容。\n"
        "无论素材是否为空，结束前都必须调用一次 write_file 把简报写入 daily_brief.md，"
        "不允许只在最终回复里说明而不写文件。\n"
        "简报必须是 markdown 格式：第一行 '# 每日新闻简报（话题）'；每个分类一节 '## 分类名 | 日期'；"
        "条目用 '1. 标题' 单独一行，下一行起是正文。\n"
        "调用工具时参数必须是合法 JSON。何时停止由你自己判断。"
    )

    def _friendly_error(e: Exception) -> str:
        """网络错误 / key 错误 / 格式异常 → 友好提示，程序不崩溃。"""
        name = type(e).__name__
        msg = str(e)
        if "401" in msg or "invalid" in msg.lower() and "key" in msg.lower():
            tip = "API_KEY 无效或未充值（401）"
        elif "404" in msg:
            tip = "API_URL 或 MODEL 名不对"
        elif isinstance(e, requests.exceptions.ConnectionError):
            tip = "网络连不上该 API 域名"
        elif "429" in msg:
            tip = "触发限流，稍后再试或换模型"
        elif "503" in msg or "too busy" in msg.lower() or "service_unavailable" in msg.lower():
            tip = "大模型服务方繁忙（503），属临时故障，稍后重试即可，配置没有问题"
        else:
            tip = "配置或网络异常"
        return (
            "抱歉，在线 LLM 调用出错了（程序已安全兜底，未崩溃）。\n"
            f"错误类型：{name}\n可能原因：{tip}\n"
            f"错误详情：{msg[:200]}\n\n"
            "请检查 config.py：1) API_KEY 是否有效 2) API_URL 是否为该平台的 OpenAI 兼容地址 "
            "3) MODEL 名是否正确；或先把 USE_LLM_API 改为 False 使用离线模拟模式。"
        )

    def _chat(messages):
        """调一次 chat/completions，返回 message dict。
        429/5xx 临时性错误自动退避重试（DeepSeek 高峰期常见 503 Service is too busy）。"""
        import time as _time
        last_err = None
        for attempt in range(3):  # 最多 3 次：立即 → 等 3s → 等 6s
            try:
                resp = requests.post(
                    chat_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": messages, "tools": oai_tools},
                    timeout=120,
                )
            except requests.RequestException as e:
                last_err = e
                if attempt < 2:
                    print(f"[RETRY] 网络异常({e})，{3 * (attempt + 1)}s 后第 {attempt + 2} 次重试…")
                    _time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                print(f"[RETRY] API 返回 {resp.status_code}（繁忙/限流），{3 * (attempt + 1)}s 后第 {attempt + 2} 次重试…")
                _time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"API 返回 {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            try:
                return data["choices"][0]["message"]
            except (KeyError, IndexError):
                raise RuntimeError(f"模型返回格式异常: {_json.dumps(data, ensure_ascii=False)[:300]}")
        raise last_err

    def invoke(user_input: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]
        try:
            for step in range(15):  # 最多 15 轮，防死循环
                msg = _chat(messages)
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    # 模型判断信息足够 → 停止工具调用，输出最终简报
                    print(f"\n[REACT] 第 {step + 1} 轮模型停止调用工具，输出最终回复 ✅")
                    return msg.get("content") or "(模型返回为空)"

                # 回传 assistant 的 tool_calls 请求
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls,
                })
                # 本地执行每个工具，结果回传
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        params = _json.loads(fn.get("arguments") or "{}")
                    except _json.JSONDecodeError:
                        params = {}
                    tool = tool_map.get(name)
                    if tool is None:
                        result = f"[错误] 没有叫 {name!r} 的工具"
                    else:
                        try:
                            result = str(tool.invoke(params))
                        except Exception as e:
                            result = f"[工具异常] {e}"
                    print(f"[REACT {step + 1}] → {name}({params})")
                    print(f"          ← {result[:100]}...")
                    messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                     "content": result[:4000]})
            return "(达到最大循环轮数 15，模型仍未停止工具调用)"
        except Exception as e:
            print(f"[ERROR] 在线 ReAct 循环异常: {e}")
            return _friendly_error(e)

    class _OpenAIReactRunner:
        def invoke(self, user_input: str) -> str:
            return invoke(user_input)

    print(f"[AGENT] 在线 ReAct Agent-Loop 构建完成 ✅  model={model}  base_url={base_url or '(官方默认)'}")
    return _OpenAIReactRunner(), "openai_react"


# ==================================================================
# 2) 路径 A：在线模式 —— LangGraph create_react_agent
# ==================================================================
def _build_online_agent(chat_llm):
    """
    新版 LangGraph create_react_agent：
      - 原生 tool-calling
      - 输入格式 {"messages": [HumanMessage(...)]}
      - 返回值是 LangGraph Runnable，invoke 返回完整 state dict
    """
    # 兼容导入：优先 langchain.agents，其次 langchain_classic
    create_react_agent = None
    for mod in ["langchain.agents", "langchain_classic.agents"]:
        try:
            m = __import__(mod, fromlist=["create_react_agent"])
            create_react_agent = m.create_react_agent
            print(f"[IMPORT] create_react_agent <- {mod}")
            break
        except Exception:
            continue
    if create_react_agent is None:
        raise ImportError(
            "找不到 create_react_agent。请安装: pip install langchain langchain-classic langchain-openai"
        )

    # 直接传原始 chat_llm —— create_react_agent 内部会自己做 bind_tools
    agent = create_react_agent(
        model=chat_llm,
        tools=ALL_TOOLS,
    )
    print("[AGENT] 在线 Agent (LangGraph create_react_agent) 构建完成 ✅")
    return agent, "langgraph"


# ==================================================================
# 3) 路径 B：离线模式 —— 先试 AgentExecutor，不行就手动 ReAct 兜底
# ==================================================================
# 自定义 ReAct Prompt（英文标签是 ReActSingleInputOutputParser 约定，不能改）
_OFFLINE_PROMPT = """你是一个「每日 AI & 科技新闻助手」，从本地新闻文件里整理简报。

## 可用工具（完整描述）
{tools}

## 工具名字列表（仅名字，用逗号分隔）
{tool_names}

## 工作方式（ReAct 循环）
Thought: （思考接下来该做什么）
Action: （工具名，必须是 [{tool_names}] 之一）
Action Input: （给工具的 JSON 参数，严格双引号）
Observation: （工具返回结果 —— 系统自动填，你不要自己生成）
...（循环）
最后用 Final Answer 结束。

## 开始！
Question: {input}
Thought: {agent_scratchpad}"""


def _try_agent_executor_offline():
    """优先尝试 AgentExecutor 路径；任何失败返回 None。"""
    # 关键：优先 langchain_classic.agents！
    # LangChain 0.2+ 的 langchain.agents.create_react_agent 是 LangGraph 新版
    # （只接受 model=，不接受 llm= / prompt=），不兼容 AgentExecutor。
    AgentExecutor = None
    create_react_agent = None
    for mod in ["langchain_classic.agents", "langchain.agents"]:
        try:
            m = __import__(mod, fromlist=["AgentExecutor", "create_react_agent"])
            AgentExecutor = getattr(m, "AgentExecutor", None)
            create_react_agent = getattr(m, "create_react_agent", None)
            if AgentExecutor and create_react_agent:
                print(f"[IMPORT] 离线 agent 组件 <- {mod}")
                break
        except Exception:
            continue

    if AgentExecutor is None or create_react_agent is None:
        print("[FALLBACK] 没找到 AgentExecutor / create_react_agent，试试手动 ReAct...")
        return None

    from langchain_core.prompts import PromptTemplate

    fake_llm = _make_fake_llm()
    prompt = PromptTemplate.from_template(_OFFLINE_PROMPT)

    # 签名检查：如果 create_react_agent 只接受 model=，说明是 LangGraph 新版，放弃
    import inspect
    sig = inspect.signature(create_react_agent)
    if "llm" not in sig.parameters:
        print("[FALLBACK] create_react_agent 不接受 llm= 参数（LangGraph 新版），用手动 ReAct")
        return None

    try:
        agent = create_react_agent(llm=fake_llm, tools=ALL_TOOLS, prompt=prompt)
        executor = AgentExecutor(
            agent=agent, tools=ALL_TOOLS, verbose=True,
            handle_parsing_errors=True, max_iterations=15,
        )
        print("[AGENT] 离线 Agent (AgentExecutor) 构建完成 ✅")
        return executor, "agentexecutor"
    except Exception as e:
        print(f"[FALLBACK] AgentExecutor 构建失败: {e}，用手动 ReAct 兜底")
        return None


def _manual_react_runner():
    """
    零依赖的手动 ReAct 循环：
      - 只用 FakeListLLM.predict() + 正则解析
      - 工具通过 ALL_TOOLS 里的 Tool.invoke() 直接 dispatch
      - 不依赖 AgentExecutor / LangGraph / ReActOutputParser
    这是离线演示的最终兜底，任何 LangChain 版本都能跑。
    """
    import re as _re
    import json

    fake_llm = _make_fake_llm()
    tool_names = ", ".join(t.name for t in ALL_TOOLS)
    tool_descs = "\n".join(
        f"- {t.name}: {t.description}" for t in ALL_TOOLS
    )
    tool_map = {t.name: t for t in ALL_TOOLS}

    def invoke(user_input: str) -> dict:
        fake_llm.reset()  # ← 每次新会话开头重置 Fake LLM 状态（Flask 单例跨请求会残留）
        scratchpad = ""
        for step in range(15):
            # 拼 prompt 文本
            prompt = _OFFLINE_PROMPT.format(
                tools=tool_descs,
                tool_names=tool_names,
                input=user_input,
                agent_scratchpad=scratchpad or "我先想想。",
            )
            # FakeListLLM 在 langchain 0.3+ 里是 Runnable，用 .invoke()
            # 0.1/0.2 里可能是 .predict() 或可直接 callable
            raw = None
            for method_name in ("invoke", "predict", "__call__"):
                method = getattr(fake_llm, method_name, None)
                if method is None:
                    continue
                try:
                    if method_name == "invoke":
                        result = method(prompt)
                        # invoke 返回的可能是 AIMessage
                        raw = getattr(result, "content", result)
                    elif method_name == "predict":
                        raw = method(prompt)
                    else:
                        raw = method(prompt)
                    break
                except Exception:
                    continue
            if raw is None:
                raw = f"[LLM 调用失败，所有方法都试了也不行]"

            print(f"\n[STEP {step+1}] LLM 输出:\n{raw}\n")

            # 先看有没有 Final Answer
            final_match = _re.search(
                r"Final Answer\s*[:：]\s*(.+)", raw, _re.DOTALL
            )
            if final_match:
                answer = final_match.group(1).strip()
                print(f"✅ Final Answer:\n{answer}")
                return {"output": answer}

            # 解析 Action 和 Action Input
            action_match = _re.search(r"Action\s*[:：]\s*(\S+)", raw)
            # 从 "Action Input: " 后找第一个 {，然后贪婪匹配到最后一个 }
            input_match = _re.search(
                r"Action Input\s*[:：]\s*(\{.*\})", raw, _re.DOTALL
            )

            if not action_match:
                print(f"[WARN] 没解析到 Action，跳过这步")
                observation = "解析失败，请明确写出 Action: 工具名"
            else:
                action_name = action_match.group(1).strip()
                tool = tool_map.get(action_name)
                if tool is None:
                    observation = f"[错误] 没有叫 {action_name!r} 的工具"
                else:
                    # 解析 JSON 参数
                    if input_match:
                        try:
                            params = json.loads(input_match.group(1))
                        except json.JSONDecodeError:
                            params = {"path": input_match.group(1).strip('"')}
                    else:
                        params = {}

                    print(f"  → 调用 {action_name}({params})")
                    try:
                        observation = tool.invoke(params)
                    except Exception as e:
                        observation = f"[工具异常] {e}"

            print(f"  ← Observation:\n{observation}\n")
            # 追加到 scratchpad，让下一步 LLM 看到
            scratchpad += raw + "\nObservation: " + observation + "\nThought: "

        return {"output": "(ReAct 达到最大步数，未得到 Final Answer)"}

    # 返回一个伪 agent_pair，kind="manual"
    class _ManualExecutor:
        def invoke(self, inputs):
            return invoke(inputs.get("input", ""))

    print("[AGENT] 离线 Agent (手动 ReAct 兜底) 构建完成 ✅")
    return _ManualExecutor(), "manual"


def _build_offline_agent():
    """
    离线入口：用手动 ReAct 循环。
    AgentExecutor 的 ReActSingleInputOutputParser 在 langchain_classic 新版本里对 FakeListLLM
    的 JSON 解析有 bug（dict 又被当成字符串再套一层），所以离线模式直接走手动循环，
    用 json.loads 自己解析 + Tool.invoke 直接 dispatch，零依赖。
    """
    print("[AGENT] 离线模式使用手动 ReAct 循环（绕过 AgentExecutor 解析器）")
    return _manual_react_runner()


# ==================================================================
# 4) 统一入口
# ==================================================================
def build_agent(offline: bool = False):
    """
    根据环境自动选择：
      - offline=True  或 没有 OPENAI_API_KEY → 离线 AgentExecutor + FakeListLLM
      - 否则 → 在线 LangGraph create_react_agent
    """
    print(f"[AGENT] 加载 {len(ALL_TOOLS)} 个工具:")
    for t in ALL_TOOLS:
        print(f"  - {t.name}: {t.description[:60]}")

    if offline or not os.environ.get("OPENAI_API_KEY"):
        print("[AGENT] 进入离线演示模式 (FakeListLLM)")
        return _build_offline_agent()

    # 优先使用标准 ReAct Agent-Loop（只依赖 openai SDK，不依赖 langchain_openai）
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return _build_online_react_runner()
        except Exception as e:
            print(f"[AGENT] 在线 ReAct 循环构建失败: {e}，回退后续路径")

    chat_llm = _get_chat_llm()
    if chat_llm is None:
        print("[AGENT] ChatOpenAI 初始化失败，降级离线演示")
        return _build_offline_agent()

    return _build_online_agent(chat_llm)


def run(agent_pair, topic: str, max_iterations: int = 15) -> str:
    """跑 ReAct 循环，返回最终文本。"""
    agent, kind = agent_pair

    user_input = (
        f"请为我整理一份今日新闻简报，重点关注这些偏好：{topic}。"
        f"新闻数据源在 news_data/ 目录下。整理好后把简报写入 daily_brief.md。"
    )
    print(f"\n{'='*60}")
    print(f"👤 用户输入: {user_input}")
    print(f"   (mode={kind}, max_iter={max_iterations})")
    print(f"{'='*60}\n")

    if kind == "openai_react":
        # 在线标准 ReAct Agent-Loop：多轮工具调用循环已在 runner 内部完成
        final = agent.invoke(user_input)
    elif kind == "langgraph":
        from langchain_core.messages import HumanMessage
        result = agent.invoke(
            {"messages": [HumanMessage(content=user_input)]},
            config={"recursion_limit": 50},
        )
        # LangGraph create_react_agent 返回的是 messages list
        messages = result.get("messages", []) if isinstance(result, dict) else []
        # 取最后一条 AIMessage 的 content
        final = messages[-1].content if messages else str(result)
    else:  # AgentExecutor
        result = agent.invoke({"input": user_input})
        final = result.get("output", str(result)) if isinstance(result, dict) else str(result)

    print(f"\n{'='*60}")
    print(f"🤖 Agent 最终回复:")
    print(f"{'='*60}\n{final}")
    return final


# ------------------------------------------------------------------
# CLI: python agent.py --topic "AI" [--offline]
# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="AI, 科技", help="用户的新闻偏好")
    parser.add_argument("--offline", action="store_true", help="强制离线 FakeLLM 模式")
    args = parser.parse_args()

    agent_pair = build_agent(offline=args.offline)
    run(agent_pair, topic=args.topic)
