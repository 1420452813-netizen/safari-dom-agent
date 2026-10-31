"""
纯文本判题器。

DOM 方案已经把「题干 + 选项文字」从页面里读出来了，因此判题不再需要视觉模型，
直接用文本模型判断即可 —— 比截图送 VLM 更准、更快、更省 token。

支持：
  - 智谱 glm-4-flash（免费，TEXT_PROVIDER="zhipu"）
  - 豆包 / 火山方舟 或任意 OpenAI 兼容文本端点

关于准确率（实测结论，务必保留）：
  glm-4-flash 若被要求「只输出 JSON」会直接猜字母，实测把「商品的二因素」误判为
  「使用价值和交换价值」。改成「先简要分析考点、排除干扰项，最后再输出 JSON」后判对。
  因此 JUDGE_PROMPT 默认带思考链（CoT）。
  另提供双模型会审（ENSEMBLE）：两个模型答案不一致时交给仲裁模型定夺。
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import List, Optional

import config

# 注意：模板里的 {"answer":"A"} 含花括号，组装时必须用 replace，不能用 str.format
JUDGE_PROMPT = """你是本课程的高水平助教。请先简要分析考点、逐个排除干扰项（2-4 句话），再给出唯一正确答案。

输出格式：先写分析，最后一行固定输出 JSON：{"answer":"A"}
（answer 为大写字母 A/B/C/D；不要输出 Markdown 代码围栏）

题目：
{question}

选项：
{options}
"""
# 允许在 config.py 用 JUDGE_PROMPT 覆盖为极速版（config 优先；未定义则回退到上面的 CoT 默认）
JUDGE_PROMPT = getattr(config, "JUDGE_PROMPT", JUDGE_PROMPT)

# 多选题提示词：输出字母数组
MULTI_PROMPT = """你是本课程的高水平助教。这是一道多选题（可能不止一个正确选项）。请先简要分析考点、逐个排除干扰项（2-4 句话），再给出所有正确选项。

输出格式：先写分析，最后一行固定输出 JSON：{"answer":["A","B"]}
（answer 为大写字母数组，如仅一个正确也用数组格式；不要输出 Markdown 代码围栏）

题目：
{question}

选项：
{options}
"""

# 填空题/简答题提示词：输出纯文本答案
FILL_PROMPT = """你是本课程的高水平助教。这是一道填空题/简答题，请直接给出最准确、简洁的标准答案文本。

输出格式：先写简要说明（可选 1-2 句），最后一行固定输出 JSON：{"answer":"这里填答案文本"}
（answer 为纯文本，不要包含选项字母前缀；不要输出 Markdown 代码围栏）

题目：
{question}
"""


def _extract_letter(text: str) -> Optional[str]:
    """从模型输出里稳健提取选项字母（多级容错）。"""
    if not text:
        return None
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
        ans = str(data.get("answer", "")).strip().upper()
        if ans:
            return ans[0]
    except Exception:
        pass
    m = re.search(r'"answer"\s*:\s*"([A-Da-d])"', clean)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([A-D])\b", clean)
    if m:
        return m.group(1).upper()
    return None


def _clean_and_parse_json(text: str):
    """统一剥离 Markdown 围栏并尝试解析 JSON（TC-SPEC-2026-V3）。"""
    if not text:
        return None, ""
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean), clean
    except Exception:
        return None, clean


def _norm_option(o: str) -> str:
    """全面清洗选项前缀，支持中英文括号、中括号及 A-Z（TC-SPEC-2026-V3）。"""
    return re.sub(r"^\s*[\[\(（]?[A-Za-z][\]\)）\.、．:：\-]\s*", "", str(o or "")).strip()


def _extract_letters(text: str, max_opts: int = 10):
    """从模型输出稳健解析多选答案数组（如 ['A','B','C']），支持动态选项长度（TC-SPEC-2026-V3）。

    杜绝原硬编码 [:4] 在多选超过 4 项时截断丢选项的问题。
    """
    data, clean = _clean_and_parse_json(text)
    if isinstance(data, dict):
        v = data.get("answer", "")
        if isinstance(v, str):
            found = re.findall(r"[A-Za-z]", v)
            return list(dict.fromkeys(c.upper() for c in found))[:max_opts] or None
        if isinstance(v, list):
            return [str(x).strip().upper()[0] for x in v if str(x).strip()][:max_opts] or None
    if isinstance(data, list):
        return [str(x).strip().upper()[0] for x in data if str(x).strip()][:max_opts] or None
    m = re.search(r"\[([^\]]*)\]", clean)
    if m:
        found = re.findall(r"[A-Za-z]", m.group(1))
        if found:
            return list(dict.fromkeys(c.upper() for c in found))[:max_opts]
    found = re.findall(r"[A-Za-z]", clean)
    return list(dict.fromkeys(c.upper() for c in found))[:max_opts] if found else None


def _extract_text(text: str) -> str:
    """填空/简答：取 answer 文本，失败则取末行非 JSON 文本。"""
    if not text:
        return ""
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
        ans = str(data.get("answer", "")).strip()
        if ans:
            return ans
    except Exception:
        pass
    m = re.search(r'"answer"\s*:\s*"([^"]*)"', clean)
    if m:
        return m.group(1).strip()
    lines = [
        l.strip() for l in clean.splitlines()
        if l.strip() and not l.strip().startswith("{") and "answer" not in l.lower()
    ]
    return lines[-1] if lines else clean[:200]


class TextSolver:
    """纯文本判题。judge() 返回答案字母，如 'C'。"""

    def __init__(self, provider: Optional[str] = None):
        p = (provider or config.TEXT_PROVIDER).lower()
        self.name = "text-" + p
        self.provider = p

        if p in ("zhipu", "glm", "zhipuai"):
            try:
                from zhipuai import ZhipuAI
            except ImportError as exc:
                raise ImportError("缺少依赖 zhipuai，请先 pip install -r requirements.txt") from exc
            if not config.ZHIPU_API_KEY or str(config.ZHIPU_API_KEY).startswith("在此填写"):
                raise ValueError("请先在启动器的「设置」中填写 API Key，或设置环境变量 ZHIPU_API_KEY")
            self.client = ZhipuAI(api_key=config.ZHIPU_API_KEY)
            self.model = config.ZHIPU_TEXT_MODEL
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ImportError("缺少依赖 openai，请先 pip install -r requirements.txt") from exc
            if p in ("doubao", "ark", "volc", "volcengine"):
                key = config.DOUBAO_API_KEY
                base = config.DOUBAO_BASE_URL
                model = config.DOUBAO_TEXT_MODEL
            else:
                key = config.GENERIC_API_KEY
                base = config.GENERIC_BASE_URL
                model = config.GENERIC_TEXT_MODEL
            if not key or str(key).startswith("在此填写"):
                raise ValueError(f"请先在启动器的「设置」中填写 API Key，或设置对应环境变量后再运行")
            self.client = OpenAI(api_key=key, base_url=base)
            self.model = model

        # 会审/仲裁用的备选模型（同源、不同型号）
        self.second_model = getattr(config, "ZHIPU_TEXT_MODEL_2", "") if self.provider.startswith(("zhipu", "glm")) else ""
        self.arbiter_model = getattr(config, "ZHIPU_ARBITER_MODEL", "") if self.provider.startswith(("zhipu", "glm")) else ""
        self.ensemble = bool(getattr(config, "ENSEMBLE", False)) and bool(self.second_model)

        # token 用量累计（judge_many 并发判题，需加锁累加）
        # 每次 _chat 返回的 resp.usage 含 prompt/completion/total_tokens
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._usage_lock = threading.Lock()

    def _chat(self, prompt: str, model: Optional[str] = None, max_tokens: Optional[int] = None) -> str:
        last: Optional[Exception] = None
        use_model = model or self.model
        # 默认用 TEXT_MAX_TOKENS（单选/多选/判断的极短分析+JSON）；填空题可传 FILL_MAX_TOKENS 放宽
        use_max = max_tokens if max_tokens is not None else config.TEXT_MAX_TOKENS
        for attempt in range(1, config.MAX_RETRY + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=use_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=config.TEMPERATURE,
                    max_tokens=use_max,
                )
                # 累计 token 用量（resp.usage 在 chat.completions 响应中始终返回）
                u = getattr(resp, "usage", None)
                if u is not None:
                    with self._usage_lock:
                        self.usage["prompt_tokens"] += int(getattr(u, "prompt_tokens", 0) or 0)
                        self.usage["completion_tokens"] += int(getattr(u, "completion_tokens", 0) or 0)
                        self.usage["total_tokens"] += int(getattr(u, "total_tokens", 0) or 0)
                return resp.choices[0].message.content or ""
            except Exception as exc:
                last = exc
                print(f"  [{self.name}/{use_model}] 第 {attempt} 次调用失败: {exc}")
                if attempt < config.MAX_RETRY:
                    time.sleep(config.RETRY_DELAY)
        raise RuntimeError(f"{self.name} 判题失败: {last}")

    def judge(self, question: str, options: List[str], qtype: str = "single"):
        """
        按题型判题：
          - single / boolean：返回单个字母（如 'C' / 'A'）
          - multiple：返回字母数组（如 ['A','B']）
          - fill：返回答案文本字符串
        开启 ENSEMBLE 时：主模型 → 备选模型，一致则采纳；不一致交仲裁模型定夺。
        """
        opts = "\n".join(f"{chr(65 + i)}. {_norm_option(o)}" for i, o in enumerate(options))
        q = question or ""

        if qtype == "multiple":
            prompt = MULTI_PROMPT.replace("{question}", q).replace("{options}", opts)
            ans = _extract_letters(self._chat(prompt))
            if not self.ensemble or not ans:
                return ans
            ans2 = _extract_letters(self._chat(prompt, self.second_model))
            if ans and ans2 and set(ans) == set(ans2):
                return ans
            if not self.arbiter_model:
                print(f"  ⚠ 会审不一致（{self.model}={ans} / {self.second_model}={ans2}），未配置仲裁模型，采纳主模型")
                return ans or ans2
            arb = _extract_letters(self._chat(prompt, self.arbiter_model))
            print(f"  ⚖ 会审不一致（{self.model}={ans} / {self.second_model}={ans2}）→ 仲裁={arb}")
            return arb or ans or ans2

        if qtype == "fill":
            prompt = FILL_PROMPT.replace("{question}", q)
            return _extract_text(self._chat(prompt, max_tokens=config.FILL_MAX_TOKENS))

        # single / boolean
        prompt = JUDGE_PROMPT.replace("{question}", q).replace("{options}", opts)
        ans = _extract_letter(self._chat(prompt))
        if not self.ensemble:
            return ans
        ans2 = _extract_letter(self._chat(prompt, self.second_model))
        if ans and ans2 and ans == ans2:
            return ans
        if not self.arbiter_model:
            print(f"  ⚠ 会审不一致（{self.model}={ans} / {self.second_model}={ans2}），未配置仲裁模型，采纳主模型")
            return ans or ans2
        arb = _extract_letter(self._chat(prompt, self.arbiter_model))
        print(f"  ⚖ 会审不一致（{self.model}={ans} / {self.second_model}={ans2}）→ 仲裁={arb}")
        return arb or ans or ans2

    def judge_many(self, questions: List[dict]) -> List[dict]:
        """
        批量判题（并发提速）。questions 为 [{type, question, options}, ...]。
        返回 [{type, answer}, ...]，answer 形态随题型（字母 / 字母数组 / 文本）。
        并发粒度=题（每题内部仍串行，兼容 ENSEMBLE 多次模型调用）；顺序与输入对齐。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _one(i: int, q: dict) -> dict:
            qtype = q.get("type", "single")
            try:
                if qtype == "fill":
                    ans = self.judge(q.get("question", ""), [], "fill")
                    print(f"  第{i+1}题 [填空] → {(ans or '')[:40]}")
                    return {"type": "fill", "answer": ans or ""}
                if qtype == "multiple":
                    ans = self.judge(q.get("question", ""), q.get("options", []), "multiple")
                    print(f"  第{i+1}题 [多选] → {ans}")
                    return {"type": "multiple", "answer": ans or []}
                ans = self.judge(q.get("question", ""), q.get("options", []), qtype)
                label = "判断" if qtype == "boolean" else "单选"
                print(f"  第{i+1}题 [{label}] → {ans}")
                return {"type": qtype, "answer": ans}
            except Exception as exc:
                print(f"  第{i+1}题 判题失败: {exc}")
                return {"type": qtype, "answer": None}

        n = len(questions)
        out = [None] * n
        with ThreadPoolExecutor(max_workers=min(config.JUDGE_CONCURRENCY, max(1, n))) as ex:
            futs = {ex.submit(_one, i, q): i for i, q in enumerate(questions)}
            for fut in as_completed(futs):
                i = futs[fut]
                out[i] = fut.result()
        return out
