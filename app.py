"""
app.py —— Flask Web 入口
功能：
  - 首页：填用户名 + 偏好 → 立即生成 / 保存订阅
  - 订阅管理：增删订阅，每天到点后台自动推送
  - 历史简报：briefs/<username>/YYYYMMDD_HHMM_<topic>.md
  - 定时调度：后台线程每分钟检查，到点自动跑 Agent + 归档
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, render_template, request, jsonify, session, abort

import sys
sys.path.insert(0, str(Path(__file__).parent))

from agent import build_agent, run

app = Flask(__name__)
app.secret_key = "kuafu-ai-news-agent-dev-key"

BASE_DIR = Path(__file__).parent
BRIEFS_DIR = BASE_DIR / "briefs"
SUBS_FILE = BASE_DIR / "subscriptions.json"
SCHED_LOG = BASE_DIR / "scheduler.log"
BRIEFS_DIR.mkdir(exist_ok=True)

# 全局状态
AGENT_PAIR = None
AGENT_MODE = "unknown"
AGENT_LOCK = threading.Lock()

# 定时推送状态
SCHED_LAST_RUN = {}   # {username+topics: "2026-10-14"}  —— 当天已跑过的 key
SCHED_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Agent 初始化
# ---------------------------------------------------------------------------
def _init_agent():
    global AGENT_PAIR, AGENT_MODE
    if AGENT_PAIR is not None:
        return AGENT_PAIR, AGENT_MODE
    with AGENT_LOCK:
        if AGENT_PAIR is not None:
            return AGENT_PAIR, AGENT_MODE
        api_key = os.environ.get("OPENAI_API_KEY")
        offline = not api_key
        try:
            AGENT_PAIR = build_agent(offline=offline)
            AGENT_MODE = "offline" if offline else "online"
        except Exception as e:
            print(f"[FLASK] Agent 初始化失败: {e}，降级离线")
            AGENT_PAIR = build_agent(offline=True)
            AGENT_MODE = "offline"
    return AGENT_PAIR, AGENT_MODE


# ---------------------------------------------------------------------------
# 文件 / 订阅 工具
# ---------------------------------------------------------------------------
def _user_dir(username: str, create: bool = False) -> Path:
    """用户简报目录。默认只返回路径不创建——查询接口不应产生副作用，
    只有真正要写文件时（_archive_brief）才传 create=True。"""
    safe = username.strip().replace("/", "_").replace("\\", "_") or "访客"
    d = BRIEFS_DIR / safe
    if create:
        d.mkdir(exist_ok=True)
    return d


def _archive_brief(username: str, topic: str, brief_text: str) -> str:
    """归档一份简报，返回文件名。"""
    ud = _user_dir(username, create=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    # 话题可能含 Windows 文件名非法字符（如 C++、AI? ），统一替换掉
    safe_topic = re.sub(r'[<>:"/\\|?*]', "_", topic).replace(",", "_").replace(" ", "_")[:20] or "brief"
    name = f"{ts}_{safe_topic}.md"
    (ud / name).write_text(brief_text or f"# {topic}\n\n（空简报）", encoding="utf-8")
    return name


def _list_briefs(username: str):
    ud = _user_dir(username)
    items = []
    for p in ud.glob("*.md"):
        st = p.stat()
        items.append({
            "name": p.name,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    items.sort(key=lambda x: x["name"], reverse=True)
    return items


def _load_subs():
    if not SUBS_FILE.exists():
        return []
    try:
        return json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_subs(subs):
    SUBS_FILE.write_text(json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8")


# 同一时刻只允许一个 Agent 运行（保护共享产物文件 daily_brief.md，
# 防止手动生成与定时推送并发时互相覆盖/读到对方的半成品）
RUN_LOCK = threading.Lock()


def _run_and_archive(username: str, topic: str):
    """跑 Agent + 归档，返回 (final_text, brief_text, archive_name, mode)。

    daily_brief.md 是 Agent 的共享产物文件：运行前先删除，保证归档内容
    一定来自本次运行；若本次模型未写文件（如检索为空时跳过了 write_file），
    用模型最终回复兜底，绝不把上一次运行的残留内容归档给本次订阅。
    """
    agent_pair, mode = _init_agent()
    brief_path = BASE_DIR / "daily_brief.md"
    with RUN_LOCK:
        try:
            brief_path.unlink(missing_ok=True)  # 清掉上一轮残留
        except Exception:
            pass

        final_text = run(agent_pair, topic=topic)

        # 在线调用失败（friendly 兜底文本）：按失败处理——不归档、不假装成功，
        # 抛给调用方：页面显示错误提示、调度器记 ❌ 日志
        if final_text.startswith("抱歉，在线 LLM 调用出错"):
            raise RuntimeError(final_text)

        brief_text = ""
        if brief_path.exists():
            try:
                brief_text = brief_path.read_text(encoding="utf-8")
            except Exception:
                pass
        if not brief_text.strip():
            # 模型本次未写文件（例如素材为空时只回了话没调 write_file）
            brief_text = final_text or f"# 每日新闻简报（{topic}）\n\n未发现相关内容。"

        archive_name = _archive_brief(username, topic, brief_text)
    return final_text, brief_text, archive_name, mode


# ---------------------------------------------------------------------------
# 后台定时推送线程
# ---------------------------------------------------------------------------
def _scheduler_loop():
    """每 5 秒检查：到点的订阅 → 跑 Agent → 归档。"""
    print("[SCHED] 定时推送线程已启动")
    while True:
        try:
            now = datetime.now()
            current_hhmm = now.strftime("%H:%M")
            today = now.strftime("%Y-%m-%d")

            subs = _load_subs()
            for sub in subs:
                if not sub.get("enabled", True):
                    continue
                push_time = sub.get("time", "09:00")
                if current_hhmm != push_time:
                    continue

                # 去重 key 含时间+创建时间：同一条订阅一天只推一次；
                # 删除后重新保存（created_at 变化）或改时间都算新订阅，不会被旧标记挡住
                key = f"{sub['username']}|{sub['topics']}|{push_time}|{sub.get('created_at', '')}"
                with SCHED_LOCK:
                    if SCHED_LAST_RUN.get(key) == today:
                        continue
                    SCHED_LAST_RUN[key] = today

                log_line = f"⏰ [{now.strftime('%Y-%m-%d %H:%M:%S')}] 自动推送: {sub['username']} @ {push_time} → {sub['topics']}"
                print(log_line)
                try:
                    final, brief, archive, mode = _run_and_archive(sub["username"], sub["topics"])
                    log_line += f"  ✅ 完成，归档 {archive}  (mode={mode})"
                    print(log_line)
                except Exception as e:
                    log_line += f"  ❌ 失败: {e}"
                    print(log_line)
                    import traceback
                    traceback.print_exc()

                # 写日志文件（追加）
                try:
                    with open(SCHED_LOG, "a", encoding="utf-8") as f:
                        f.write(log_line + "\n")
                except Exception:
                    pass
        except Exception as e:
            print(f"[SCHED] loop error: {e}")

        time.sleep(5)  # 每 5 秒检查一次，到点误差不超过 5 秒


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    username = session.get("username", "访客")
    briefs = _list_briefs(username)
    # 用户端页面：只显示当前用户自己的订阅
    subs = [s for s in _load_subs() if s["username"] == username]
    return render_template(
        "index.html",
        username=username,
        briefs=briefs,
        subscriptions=subs,
    )


@app.route("/generate", methods=["POST"])
def generate():
    """手动立即生成一份。"""
    username = request.form.get("username", "").strip() or "访客"
    topic = request.form.get("topic", "").strip() or "AI, 科技"
    session["username"] = username
    session["last_topic"] = topic

    try:
        final_text, brief_text, archive_name, mode = _run_and_archive(username, topic)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"Agent 运行出错: {e}"}), 500

    briefs = _list_briefs(username)
    return jsonify({
        "ok": True,
        "mode": mode,
        "final_text": final_text,
        "brief_md": brief_text,
        "archive_name": archive_name,
        "briefs": briefs,
    })


# ---------- 订阅管理 ----------

@app.route("/subscriptions", methods=["GET"])
def get_subs():
    """支持 ?username= 过滤：用户端只拉自己的订阅，并附下次推送时间。"""
    username = request.args.get("username", "").strip()
    subs = _load_subs()
    if username:
        subs = [s for s in subs if s["username"] == username]
    now = datetime.now()
    for s in subs:
        try:
            push_dt = datetime.strptime(s.get("time", "09:00"), "%H:%M").replace(
                year=now.year, month=now.month, day=now.day)
            if push_dt <= now:
                push_dt += timedelta(days=1)
            s["next_push"] = push_dt.strftime("%m-%d %H:%M")
        except ValueError:
            s["next_push"] = "?"
    return jsonify({"subscriptions": subs})


@app.route("/subscriptions", methods=["POST"])
def add_sub():
    """新增或更新一条订阅。"""
    data = request.get_json(force=True)
    username = (data.get("username") or session.get("username") or "").strip() or "访客"
    topics = (data.get("topics") or "").strip()
    push_time = (data.get("time") or "09:00").strip()

    if not topics:
        return jsonify({"ok": False, "error": "订阅偏好不能为空"}), 400
    # 时间格式校验 HH:MM
    try:
        datetime.strptime(push_time, "%H:%M")
    except ValueError:
        return jsonify({"ok": False, "error": "推送时间格式应为 HH:MM"}), 400

    subs = _load_subs()
    key = {"username": username, "topics": topics}
    # 去重：同用户名+同偏好只保留一条
    new_subs = [s for s in subs if not (s["username"] == username and s["topics"] == topics)]
    new_subs.append({
        "username": username,
        "topics": topics,
        "time": push_time,
        "enabled": True,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_subs(new_subs)
    return jsonify({"ok": True, "subscriptions": new_subs})


@app.route("/subscriptions", methods=["DELETE"])
def del_sub():
    """删除一条订阅。参数：username + topics。"""
    username = request.args.get("username", "").strip()
    topics = request.args.get("topics", "").strip()
    if not username or not topics:
        return jsonify({"ok": False, "error": "缺少 username 或 topics"}), 400
    subs = _load_subs()
    new_subs = [s for s in subs if not (s["username"] == username and s["topics"] == topics)]
    _save_subs(new_subs)
    return jsonify({"ok": True, "subscriptions": new_subs})


@app.route("/subscriptions/push-now", methods=["POST"])
def push_now():
    """对某订阅立即推一次（不等定时）。"""
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip() or "访客"
    topics = (data.get("topics") or "").strip()
    if not topics:
        return jsonify({"ok": False, "error": "topics 不能为空"}), 400
    try:
        final_text, brief_text, archive_name, mode = _run_and_archive(username, topics)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500
    briefs = _list_briefs(username)
    return jsonify({
        "ok": True,
        "mode": mode,
        "final_text": final_text,
        "brief_md": brief_text,
        "archive_name": archive_name,
        "briefs": briefs,
    })


# ---------- 历史查看 ----------

@app.route("/briefs")
def list_briefs():
    username = request.args.get("username", session.get("username", "访客")).strip() or "访客"
    return jsonify({"username": username, "briefs": _list_briefs(username)})


@app.route("/briefs/latest")
def latest_brief():
    """前端轮询用：返回某用户最新一份简报（含全文）。"""
    username = request.args.get("username", session.get("username", "访客")).strip() or "访客"
    briefs = _list_briefs(username)
    if not briefs:
        return jsonify({"has_new": False})
    newest = briefs[0]
    ud = _user_dir(username)
    target = (ud / newest["name"]).resolve()
    content = ""
    if str(target).startswith(str(ud.resolve())) and target.exists():
        content = target.read_text(encoding="utf-8", errors="replace")
    return jsonify({
        "has_new": True,
        "name": newest["name"],
        "mtime": newest["mtime"],
        "content": content,
    })


@app.route("/briefs/<name>")
def view_brief(name):
    username = request.args.get("username", session.get("username", "访客")).strip() or "访客"
    ud = _user_dir(username)
    target = (ud / name).resolve()
    if not str(target).startswith(str(ud.resolve())) or not target.exists():
        abort(404)
    return jsonify({
        "name": name,
        "username": username,
        "content": target.read_text(encoding="utf-8", errors="replace"),
    })


# ---------- 健康检查 ----------

@app.route("/health")
def health():
    ap, mode = _init_agent()
    subs = _load_subs()
    next_push = None
    now = datetime.now()
    for s in subs:
        try:
            hh, mm = map(int, s.get("time", "09:00").split(":"))
            dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dt <= now:
                dt = dt.replace(day=now.day + 1)
            if next_push is None or dt < next_push:
                next_push = dt
        except Exception:
            pass
    return jsonify({
        "status": "ok",
        "mode": mode,
        "subscriptions_count": len(subs),
        "next_push": next_push.strftime("%Y-%m-%d %H:%M:%S") if next_push else None,
    })


@app.route("/scheduler/status")
def scheduler_status():
    """定时线程状态（用于前端轮询）。"""
    subs = _load_subs()
    logs = []
    if SCHED_LOG.exists():
        try:
            raw = SCHED_LOG.read_text(encoding="utf-8")
            logs = [l for l in raw.strip().split("\n") if l][-20:]  # 最近 20 条
        except Exception:
            pass
    # 每个订阅的下次推送时间
    now = datetime.now()
    subs_info = []
    for s in subs:
        try:
            hh, mm = map(int, s.get("time", "09:00").split(":"))
            dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dt <= now:
                dt = dt.replace(day=now.day + 1)
            subs_info.append({
                "username": s["username"],
                "topics": s["topics"],
                "time": s["time"],
                "next_push": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "enabled": s.get("enabled", True),
            })
        except Exception:
            pass
    return jsonify({
        "running": True,
        "subscriptions": subs_info,
        "recent_logs": list(reversed(logs)),
        "briefs_dir": str(BRIEFS_DIR),
    })


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # debug 重载机制会同时存在父(文件监控)/子(真正服务)两个进程；
    # 只在子进程里启动调度线程，否则同一订阅到点会被推两次
    # （WERKZEUG_RUN_MAIN=true 只存在于子进程；若以后关闭 reloader 需去掉此判断）
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=_scheduler_loop, daemon=True).start()
        print("⏰ 定时推送线程已就绪（每 5 秒检查一次）")
    print("\n🌐 Flask 启动中... 浏览器打开 http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=True)
