# -*- coding: utf-8 -*-
"""
config.template.py —— 配置模板（此文件提交到仓库，作为新人的起点）

使用方法：
  1. 复制本文件并重命名为 config.py（config.py 已被 .gitignore 忽略，不会入库）
  2. 设置环境变量 NEWS_AGENT_API_KEY 为你自己的大模型 API Key：
       Windows（永久生效，设完重开终端）:  setx NEWS_AGENT_API_KEY "sk-你的key"
       Windows cmd（仅当前窗口临时）:      set NEWS_AGENT_API_KEY=sk-你的key
       macOS / Linux:                      export NEWS_AGENT_API_KEY=sk-你的key
  3. 没有 Key 也没关系：把 USE_LLM_API 改成 False，即可走离线模拟模式体验全部功能
"""
import os

USE_LLM_API = True

# 密钥从环境变量读取，禁止硬编码。
# 任意 OpenAI 兼容 API 均可：DeepSeek / 智谱GLM / Kimi / 通义千问 / OpenAI
API_KEY = os.environ.get("NEWS_AGENT_API_KEY", "")

# 接口地址与模型可用环境变量覆盖，默认值对应 DeepSeek：
#   DeepSeek:  https://api.deepseek.com/v1                        model: deepseek-chat
#   智谱GLM:   https://open.bigmodel.cn/api/paas/v4               model: glm-4-flash
#   Kimi:      https://api.moonshot.cn/v1                         model: moonshot-v1-8k
#   通义千问:  https://dashscope.aliyuncs.com/compatible-mode/v1  model: qwen-plus
#   OpenAI:    https://api.openai.com/v1                          model: gpt-4o-mini
API_URL = os.environ.get("NEWS_AGENT_API_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("NEWS_AGENT_MODEL", "deepseek-chat")
