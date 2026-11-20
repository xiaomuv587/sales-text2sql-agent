# -*- coding: utf-8 -*-
"""
第 1 步：跑通一次大模型 API 调用。

目标：屏幕上打印出模型的回复 + 本次消耗的 token。

零第三方依赖，Python 自带 urllib 即可。
运行：直接 python step1_api.py（Key 粘在下面的 PASTE_KEY_HERE 里）。
"""
import os
import sys
import json
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ============================================================
# 【API Key 填这里】把 sk- 开头那串粘进下面引号里，别动其它地方。
# 留空则读环境变量 LLM_API_KEY；传 GitHub 前务必清空这一行。
# ============================================================
PASTE_KEY_HERE = ""

PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-flash"),
    "bailian": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-flash"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
}
PROVIDER = "deepseek"  # ← 用哪家就改这里
BASE_URL, MODEL = PROVIDERS[PROVIDER]
API_KEY = (os.getenv("LLM_API_KEY") or PASTE_KEY_HERE).strip()


def chat(messages, temperature=0.0, timeout=60):
    """最裸的一次 chat/completions 调用。以后所有 Agent 框架，底层都是这个。"""
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + API_KEY,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    if not API_KEY:
        print("还没填 API Key：打开本文件顶部，把 key 粘进 PASTE_KEY_HERE 的引号里。")
        sys.exit(1)

    print("厂商 =", PROVIDER)
    print("地址 =", BASE_URL)
    print("模型 =", MODEL)
    print("\n请求中……\n")

    try:
        r = chat([
            {"role": "system", "content": "你是一个简洁的技术助手，用中文回答。"},
            {"role": "user", "content": (
                "用一句话说明：把自然语言问题转成 SQL 查询，关键难点是什么？"
                '严格以 JSON 格式返回，形如 {"answer": "..."}'
            )},
        ])
    except urllib.error.HTTPError as e:
        print("HTTP 错误", e.code)
        print(e.read().decode("utf-8", "ignore")[:600])
        print("\n排查顺序：")
        print("  1) key 是否复制完整、有没有多复制空格")
        print("  2) 模型名是否还有效（去控制台看当前可用型号，回来改 MODEL）")
        print("  3) 账户是否有余额或免费额度")
        sys.exit(1)
    except Exception as e:
        print("请求失败：", type(e).__name__, e)
        print("排查：网络是否可达、是否需要开代理。")
        sys.exit(1)

    print("模型回复：")
    print(r["choices"][0]["message"]["content"])
    print("\n本次 token 用量：", r.get("usage", {}))
    print("\n[OK] 第 1 步完成。接着打开 step2_text2sql.py。")
