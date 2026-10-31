"""
多提供方视觉求解模块。

支持：
  - 智谱 ZhipuSolver：glm-4v-flash（免费多模态，推荐）
  - 豆包 DoubaoSolver：火山方舟 Ark（OpenAI 兼容）
  - 通用 GenericSolver：任意 OpenAI 兼容视觉端点
        （硅基流动 / 魔搭 ModelScope / Google Gemini / OpenRouter / 本地 Ollama …）

公共能力：图片预处理、Base64 编码、JSON 正则容错提取、结果规整、失败重试。
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from typing import Optional

from PIL import Image

import config

SYSTEM_PROMPT = """你是一个高精度的在线测试评估助手。你将看到一道题目（可能是编程 Java Web 或通用学科题）的屏幕截图。

严格按以下规则执行，并且只输出 JSON：
1. 判定题型，取值之一：
   - "single"   单选题
   - "multiple" 多选题
   - "fill_in"  填空题
   - "boolean"  判断题
2. 给出答案：
   - 选择题/判断题：输出选项的大写字母列表，如 ["A"] 或 ["A","C"]。
   - 填空题：按空位顺序（①、② 或输入框 1、2）输出字符串列表，如 ["invalidate"]；编程方法名必须保留准确大小写。
3. 只输出纯 JSON，禁止任何解释、禁止 Markdown 代码围栏。

输出格式示例：
{"type":"single","answers":["A"]}
{"type":"multiple","answers":["A","C"]}
{"type":"fill_in","answers":["invalidate","session"]}
{"type":"boolean","answers":["A"]}
"""

SYSTEM_PROMPT_MULTI = """你将看到一张包含多道题目的屏幕截图（整卷单页布局）。请从上到下依次回答图中所有「选项完整可见」的题目。

规则：
1. 每题给出答案列表：单选/多选/判断题为选项大写字母，如 ["A"] 或 ["A","C"]。
2. 只回答 A/B/C/D 选项完整可见的题目；选项被截断的题目跳过。
3. 严格只输出 JSON，结构固定为：
{"answers": [["A"], ["C"], ["B"]]}
4. 外层列表顺序必须与图中题目从上到下的顺序一致。
5. 禁止任何解释、禁止 Markdown 代码围栏。
"""


# ---------------- 公共工具 ----------------
def _is_placeholder(value: Optional[str]) -> bool:
    return not value or str(value).startswith("在此填写")


def _prepare(image: Image.Image) -> Image.Image:
    """最长边限制到 MAX_IMAGE_SIDE，降低 token 与失败率。"""
    if image.mode != "RGB":
        image = image.convert("RGB")
    max_side = config.MAX_IMAGE_SIDE
    w, h = image.size
    longest = max(w, h)
    if longest > max_side:
        ratio = max_side / longest
        image = image.resize((max(1, int(w * ratio)), max(1, int(h * ratio))))
    return image


def _raw_b64(image: Image.Image) -> str:
    """纯 Base64（无 data: 前缀），智谱使用。"""
    buf = io.BytesIO()
    _prepare(image).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _data_url(image: Image.Image) -> str:
    """带 data: 前缀的 Base64，OpenAI 兼容端点使用。"""
    return "data:image/png;base64," + _raw_b64(image)


def extract_json(text: str) -> dict:
    """从模型输出中提取第一个合法 JSON 对象（多级容错）。"""
    if not text:
        raise ValueError("模型返回为空")
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except Exception:
        pass
    for m in re.finditer(r"\{.*\}", clean, re.S):
        try:
            return json.loads(m.group(0))
        except Exception:
            continue
    start, end = clean.find("{"), clean.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(clean[start:end + 1])
        except Exception:
            pass
    raise ValueError(f"无法解析 JSON: {text[:200]}")


def normalize(data: dict) -> dict:
    qtype = str(data.get("type", "single")).strip().lower()
    if qtype not in {"single", "multiple", "fill_in", "boolean"}:
        qtype = "single"
    answers = data.get("answers", [])
    if isinstance(answers, str):
        answers = [answers]
    answers = [str(a).strip() for a in answers if str(a).strip() != ""]
    return {"type": qtype, "answers": answers}


# ---------------- 基类（含重试）----------------
class BaseSolver:
    name = "base"

    def _request(self, image: Image.Image, prompt: str) -> str:
        last_err: Optional[Exception] = None
        for attempt in range(1, config.MAX_RETRY + 1):
            try:
                return self._call(image, prompt)
            except Exception as exc:
                last_err = exc
                print(f"  [{self.name}] 第 {attempt} 次调用失败: {exc}")
                if attempt < config.MAX_RETRY:
                    time.sleep(config.RETRY_DELAY)
        raise RuntimeError(f"{self.name} 调用失败: {last_err}")

    def solve(self, image: Image.Image) -> dict:
        """单题：返回 {"type":..., "answers":[...]}。"""
        return normalize(extract_json(self._request(image, SYSTEM_PROMPT)))

    def solve_all(self, image: Image.Image) -> list:
        """整卷多题：按从上到下顺序返回每题答案 [[...], ...]。"""
        data = extract_json(self._request(image, SYSTEM_PROMPT_MULTI))
        answers = data.get("answers", []) if isinstance(data, dict) else []
        out = []
        for a in answers:
            if isinstance(a, str):
                a = [a]
            out.append([str(x).strip() for x in a if str(x).strip() != ""])
        return out

    def _call(self, image: Image.Image, prompt: str) -> str:
        raise NotImplementedError


# ---------------- 通用 OpenAI 兼容基类 ----------------
class OpenAICompatSolver(BaseSolver):
    """适配任意 OpenAI 兼容 /chat/completions 视觉端点。"""

    def __init__(self, name: str, api_key: Optional[str], base_url: str,
                 model: str, max_tokens: int = 1024):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("缺少依赖 openai，请先 pip install -r requirements.txt") from exc

        self.name = name
        if _is_placeholder(api_key):
            raise ValueError(f"[{name}] 请先在 config.py 填写对应的 API Key")
        if _is_placeholder(model):
            raise ValueError(f"[{name}] 请先在 config.py 填写对应的模型名")
        self.model = model
        self.max_tokens = max_tokens
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def _call(self, image: Image.Image, prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _data_url(image)}},
                    {"type": "text", "text": prompt},
                ],
            }],
            temperature=config.TEMPERATURE,
            max_tokens=self.max_tokens,
        )
        return resp.choices[0].message.content


# ---------------- 豆包（火山方舟）----------------
class DoubaoSolver(OpenAICompatSolver):
    def __init__(self):
        super().__init__(
            name="doubao",
            api_key=config.DOUBAO_API_KEY,
            base_url=config.DOUBAO_BASE_URL,
            model=config.DOUBAO_MODEL,
            max_tokens=config.DOUBAO_MAX_TOKENS,
        )


# ---------------- 通用 OpenAI 兼容端点 ----------------
class GenericSolver(OpenAICompatSolver):
    def __init__(self):
        super().__init__(
            name="openai",
            api_key=config.GENERIC_API_KEY,
            base_url=config.GENERIC_BASE_URL,
            model=config.GENERIC_MODEL,
            max_tokens=config.GENERIC_MAX_TOKENS,
        )


# ---------------- 智谱 glm-4v-flash ----------------
class ZhipuSolver(BaseSolver):
    name = "zhipu"

    def __init__(self):
        try:
            from zhipuai import ZhipuAI
        except ImportError as exc:
            raise ImportError("缺少依赖 zhipuai，请先 pip install -r requirements.txt") from exc

        if _is_placeholder(config.ZHIPU_API_KEY):
            raise ValueError("请先在 config.py 填写 ZHIPU_API_KEY")
        self.model = config.ZHIPU_MODEL
        self.client = ZhipuAI(api_key=config.ZHIPU_API_KEY)

    def _call(self, image: Image.Image, prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _raw_b64(image)}},
                    {"type": "text", "text": prompt},
                ],
            }],
            temperature=config.TEMPERATURE,
        )
        return resp.choices[0].message.content


# ---------------- 工厂 ----------------
def create_solver(provider: Optional[str] = None) -> BaseSolver:
    p = (provider or config.PROVIDER).lower()
    if p in ("doubao", "ark", "volc", "volcengine"):
        return DoubaoSolver()
    if p in ("zhipu", "glm", "zhipuai"):
        return ZhipuSolver()
    if p in ("openai", "generic", "siliconflow", "modelscope", "gemini", "openrouter", "compatible"):
        return GenericSolver()
    raise ValueError(f"未知提供方: {p}（可选 zhipu / doubao / openai）")
