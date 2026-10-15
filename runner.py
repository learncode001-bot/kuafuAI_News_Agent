"""
runner.py —— 第一步交付物的「终端跑通入口」。
直接调用 agent.build_agent + agent.run。
顶层 try/except 确保任何异常都能打印完整 traceback，方便排查。
"""

import sys
import traceback


def main():
    # 延迟导入：这样如果依赖没装，错误能被 main() 里的 try/except 捕获
    from agent import build_agent, run

    args = sys.argv[1:]
    offline = "--offline" in args
    topic_args = [a for a in args if a != "--offline"]

    agent_pair = build_agent(offline=offline)

    if topic_args:
        topic = " ".join(topic_args)
        run(agent_pair, topic=topic)
    else:
        interactive(agent_pair)


def interactive(agent_pair):
    print("=" * 60)
    print("🤖 每日 AI 新闻助手 (ReAct Agent) —— 交互模式")
    print("输入 quit / exit 退出")
    print("=" * 60)

    while True:
        try:
            topic = input("\n你的新闻偏好（例如 'AI, 科技, 创投'）> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 👋")
            break
        if topic.lower() in ("quit", "exit", "q") or not topic:
            print("再见 👋")
            break
        run(agent_pair, topic=topic)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n❌ 运行出错了！{type(e).__name__}: {e}", file=sys.stderr)
        print("-" * 60, file=sys.stderr)
        traceback.print_exc()
        print("-" * 60, file=sys.stderr)
        print("\n💡 常见排查：", file=sys.stderr)
        print("  1) 先装依赖: pip install langchain langchain-classic langchain-core langchain-community", file=sys.stderr)
        print("  2) 再试: python runner.py \"AI\" --offline", file=sys.stderr)
        sys.exit(1)
