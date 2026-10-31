# tests/benchmark_eval.py
# -----------------------------------------------------------------------------
# 论文表 1 真实实验数据采集脚本（Ours 列：DOM 语义剪枝 + 并发 LLM）
#
# 测量维度（每轮 trial）：
#   t_read   感知与读取耗时  —— dom_reader.read_questions(prefer_export=False)
#   t_solve  LLM 推理总耗时  —— TextSolver().judge_many(questions)（并发）
#   t_apply  动作派发与回读  —— dom_reader.apply_answers(...) + 回读校验
#   t_total  端到端总耗时     —— t_read + t_solve + t_apply
#   apply_rate  DOM 状态回填成功率 —— apply_answers 返回 applied[].ok 的比例
#   tokens   智谱 API 返回的 usage.prompt/completion/total（单题平均 = 总量 / n）
#
# 前置条件（真实环境）：
#   1) Safari 已打开并停留在 lms.eurasia.edu 受测页面（DOMRunner 通道已授权）
#   2) 已配置 ZHIPU_API_KEY（~/.tronclass/config.json 或环境变量）
#
# 用法：  .venv/bin/python tests/benchmark_eval.py
# -----------------------------------------------------------------------------
import os
import sys
import json
import time
import statistics

# 让 tests/ 下也能 import 到项目根的 dom_reader / text_solver
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dom_reader
from text_solver import TextSolver

TRIALS = 5


def _key_ready():
    """友好预检：是否具备可用的 ZHIPU API Key（不打印明文）。"""
    if os.environ.get("ZHIPU_API_KEY"):
        return True
    p = os.path.expanduser("~/.tronclass/config.json")
    if os.path.exists(p):
        try:
            d = json.load(open(p))
            return any("KEY" in str(k).upper() for k in d)
        except Exception:
            return False
    return False


def run_single_trial():
    # 1. 测量 DOM 感知读取耗时
    t0 = time.perf_counter()
    data = dom_reader.read_questions(prefer_export=False)
    questions = data.get("questions", [])
    t_read = time.perf_counter() - t0

    # 2. 测量并发推理耗时（同时采集智谱 usage Token 计数）
    solver = TextSolver()
    t1 = time.perf_counter()
    answers = solver.judge_many(questions)
    t_solve = time.perf_counter() - t1
    usage = dict(solver.usage)  # {prompt_tokens, completion_tokens, total_tokens}

    # 3. 测量 DOM 动作派发与回读校验耗时
    t2 = time.perf_counter()
    res = dom_reader.apply_answers(answers, prefer_export=False)
    _verify = dom_reader.read_questions(prefer_export=False)  # 回读校验
    t_apply = time.perf_counter() - t2

    applied_list = res.get("applied", [])
    success_count = sum(1 for item in applied_list if item.get("ok"))
    success_rate = (success_count / len(questions) * 100.0) if questions else 0.0

    n = len(questions)
    return {
        "n_questions": n,
        "t_read": t_read,
        "t_solve": t_solve,
        "t_apply": t_apply,
        "t_total": t_read + t_solve + t_apply,
        "apply_rate": success_rate,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


if __name__ == "__main__":
    if not _key_ready():
        print("⚠️ 未检测到 ZHIPU_API_KEY（config.json / 环境变量均无）。")
        print("   请先在 ~/.tronclass/config.json 配置 key，或 export ZHIPU_API_KEY=...，")
        print("   并确保 Safari 停留在受测页面，再运行本脚本以采集真实 usage 与耗时。")
        sys.exit(2)

    print(f"开始基准评测：共 {TRIALS} 次试验，需 Safari 停留在受测页面…")
    results = []
    for i in range(TRIALS):
        r = run_single_trial()
        results.append(r)
        print(f"  trial {i + 1}: n={r['n_questions']} "
              f"t_total={r['t_total']:.2f}s apply_rate={r['apply_rate']:.1f}% "
              f"tokens={r['total_tokens']}")

    nq = results[0]["n_questions"]
    print(f"\n题目数 n = {nq}（{TRIALS} 次试验 → 均值 ± 标准差）\n")

    for key in ["t_read", "t_solve", "t_apply", "t_total", "apply_rate"]:
        vals = [r[key] for r in results]
        mean = statistics.mean(vals)
        stdev = statistics.stdev(vals) if len(vals) > 1 else 0.0
        unit = "%" if key == "apply_rate" else "s"
        print(f"{key:12s}: {mean:.2f} ± {stdev:.2f} {unit}")

    def _agg(key):
        vals = [r[key] for r in results]
        return (statistics.mean(vals),
                statistics.stdev(vals) if len(vals) > 1 else 0.0)

    p_mean, p_std = _agg("prompt_tokens")
    c_mean, c_std = _agg("completion_tokens")
    t_mean, t_std = _agg("total_tokens")
    print("\nToken 统计（单题平均 = 总量 / 题数 n）:")
    print(f"  prompt_tokens    : {p_mean:.0f} ± {p_std:.0f}   (单题 {p_mean / nq:.1f})" if nq
          else f"  prompt_tokens    : {p_mean:.0f} ± {p_std:.0f}")
    print(f"  completion_tokens: {c_mean:.0f} ± {c_std:.0f}   (单题 {c_mean / nq:.1f})" if nq
          else f"  completion_tokens: {c_mean:.0f} ± {c_std:.0f}")
    print(f"  total_tokens     : {t_mean:.0f} ± {t_std:.0f}   (单题 {t_mean / nq:.1f})" if nq
          else f"  total_tokens     : {t_mean:.0f} ± {t_std:.0f}")
