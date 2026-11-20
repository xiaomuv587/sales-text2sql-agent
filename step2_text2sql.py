# -*- coding: utf-8 -*-
"""
Text-to-SQL
链路：自然语言问题 → 模型生成 SQL → 本地 SQLite 执行 → 模型把结果说成人话。

  1) 把表结构（字段名 / 类型 / 中文含义）注入 prompt
  2) 只允许 SELECT 的安全校验
  3) SQL 执行报错时把错误回给模型自动改一次 —— Agent「观察→纠错」雏形

数据全部由脚本随机生成，品牌与门店均为虚构，不含任何真实业务信息。
"""
import os
import sys
import re
import json
import sqlite3
import random
import urllib.request
import urllib.error
from datetime import date, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ============================================================
# 【API Key 填这里】留空则读环境变量 LLM_API_KEY；传 GitHub 前必须保持空。
# ============================================================
PASTE_KEY_HERE = ""

PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-flash"),
    "bailian": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-flash"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
}
PROVIDER = "deepseek"
BASE_URL, MODEL = PROVIDERS[PROVIDER]
API_KEY = (os.getenv("LLM_API_KEY") or PASTE_KEY_HERE).strip()

# -------------------- 表结构
# 销售明细表（日期/渠道/商店/SKU/品牌/数量/金额/单据类型）
SCHEMA = """
表名：sales_detail（销售明细；一行 = 某商品的一次销售记录）
  sale_date   TEXT      销售日期，格式 YYYY-MM-DD
  channel     TEXT      销售渠道：天猫 / 京东 / 抖音 / 线下门店
  store_code  TEXT      商店代码，例如 ST01
  brand       TEXT      品牌：青禾 / 云杉 / 北岛 / 木野
  sku         TEXT      商品编码
  qty         INTEGER   销售数量；退货冲销为负数
  amount      REAL      销售金额（元），两位小数；退款为负数
  is_online   INTEGER   是否线上：1 是 / 0 否
  sales_type  TEXT      单据类型：正常销售 / 退货冲销
"""

SQL_SYSTEM = """你是 SQLite SQL 生成助手。只能基于下面这张表写查询。

""" + SCHEMA + """
规则：
1. 只输出一条 SQLite SELECT 语句；不要解释、不要 markdown 代码块、不要多余文字。
2. 只能使用上面列出的字段名，不要编造字段。
3. 「销售额 / 金额」用 SUM(amount)；退货的负数已体现在数据里，不要额外排除。
4. 「退货」用 sales_type='退货冲销' 或 qty<0。
5. 需要分组时带上 GROUP BY，结果默认按金额降序。
"""

ANSWER_SYSTEM = "你是数据分析助手。根据查询结果用一句中文回答问题，不要复述 SQL，不要编造数据。"


def chat(messages, temperature=0.0, timeout=60):
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
        return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]


# ------------- 造数据
def build_db(seed=42, n=200):
    con = sqlite3.connect(":memory:")
    con.execute("""
        CREATE TABLE sales_detail (
            sale_date TEXT, channel TEXT, store_code TEXT, brand TEXT,
            sku TEXT, qty INTEGER, amount REAL, is_online INTEGER, sales_type TEXT
        )
    """)
    random.seed(seed)
    brands = ["青禾", "云杉", "北岛", "木野"]
    channels = ["天猫", "京东", "抖音", "线下门店"]
    start = date(2026, 7, 1)
    rows = []
    for _ in range(n):
        d = start + timedelta(days=random.randint(0, 30))
        ch = random.choice(channels)
        is_return = random.random() < 0.12
        qty = -random.randint(1, 3) if is_return else random.randint(1, 8)
        amount = round(qty * random.uniform(80, 900), 2)
        rows.append((
            d.isoformat(), ch, "ST%02d" % random.randint(1, 6),
            random.choice(brands), "SKU%04d" % random.randint(1, 60),
            qty, amount, 0 if ch == "线下门店" else 1,
            "退货冲销" if is_return else "正常销售",
        ))
    con.executemany("INSERT INTO sales_detail VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return con


# ----------------------- 解析与防护
def extract_sql(text):
    """让模型输出纯 SQL。"""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    s = (m.group(1) if m else text).strip()
    m2 = re.search(r"(SELECT\b.*)", s, re.S | re.I)
    if m2:
        s = m2.group(1).strip()
    return s.rstrip(";").strip()


def safe_select(sql):
    """工程保护：只放行 SELECT，拦住删改语句。"""
    s = sql.lower()
    if not s.startswith("select"):
        return False, "不是 SELECT 语句，已拒绝执行"
    for kw in ("drop", "delete", "update", "insert", "alter", "attach", "pragma"):
        if re.search(r"\b" + kw + r"\b", s):
            return False, "含禁用关键字 %s，已拒绝执行" % kw
    return True, ""


def run_sql(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


# ------------------ 主链路
def ask(con, question, verbose=True):
    if verbose:
        print("\n" + "=" * 60)
        print("问题：", question)

    raw = chat([
        {"role": "system", "content": SQL_SYSTEM},
        {"role": "user", "content": question},
    ])
    sql = extract_sql(raw)
    if verbose:
        print("① 生成的 SQL：", sql)

    ok, why = safe_select(sql)
    if not ok:
        print("安全校验拦截：", why)
        return None

    try:
        result = run_sql(con, sql)
    except Exception as e:
        # 把错误回给模型改一次 —— Agent「观察→纠错」的最小形态
        if verbose:
            print("② 执行报错，回传给模型自动修正：", e)
        fixed = chat([
            {"role": "system", "content": SQL_SYSTEM},
            {"role": "user", "content": question},
            {"role": "assistant", "content": sql},
            {"role": "user", "content": (
                "这条 SQL 执行报错：%s\n请只输出修正后的一条 SELECT 语句，不要解释。" % e
            )},
        ])
        sql = extract_sql(fixed)
        ok, why = safe_select(sql)
        if not ok:
            print("修正后仍被拦截：", why)
            return None
        if verbose:
            print("   修正为：", sql)
        result = run_sql(con, sql)

    if verbose:
        print("② 查询结果（前 5 行）：", json.dumps(result[:5], ensure_ascii=False))

    answer = chat([
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": "问题：%s\n查询结果 JSON：%s" % (
            question, json.dumps(result[:20], ensure_ascii=False)
        )},
    ])
    if verbose:
        print("③ 最终回答：", answer)
    return {"sql": sql, "rows": result, "answer": answer}


if __name__ == "__main__":
    if not API_KEY:
        print("还没填 API Key：打开本文件顶部，把 key 粘进 PASTE_KEY_HERE 的引号里。")
        sys.exit(1)

    con = build_db()
    print("已生成 %d 行虚构销售明细数据（SQLite 内存库）。" %
          con.execute("SELECT COUNT(*) FROM sales_detail").fetchone()[0])
    print("厂商 =", PROVIDER, " 模型 =", MODEL)

    questions = [
        "各渠道的退货率",
        "青禾品牌在线上卖了多少件？",
        "每个月的销售额 ？",
        "各品牌的平均客单价？",
        "按会员等级统计销售额？",
    ]
    for q in questions:
        try:
            ask(con, q)
        except urllib.error.HTTPError as e:
            print("API 报错：", e.code, e.read().decode("utf-8", "ignore")[:400])
            break
        except Exception as e:
            print("运行出错：", type(e).__name__, e)
            break

    print("\n多试几个问题，观察模型什么时候会出错。")
