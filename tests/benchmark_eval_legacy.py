# tests/benchmark_eval_legacy.py
# -----------------------------------------------------------------------------
# 论文表 1 基准评测脚本（Baseline 列：Legacy 视觉方案 = 截图 + VLM glm-4v-flash）
#
# 与 Ours(dom_reader + 纯文本 LLM) 对称的设计：
#   t_read   感知与读取耗时  —— 逐题截图采集（pyautogui.screenshot，与 legacy.vision.capture 同源）
#   t_solve  LLM 推理总耗时  —— 逐题裁图 → glm-4v-flash 视觉判题（复用 legacy.solver 同款图像编码）
#   t_apply  动作派发与回读  —— dom_reader.apply_answers(视觉判定结果) + 回读校验（物理点击与 Ours 一致）
#   t_total  端到端总耗时
#   apply_rate DOM 回填成功率
#   tokens   智谱 VLM 返回的 usage（图像 Token：prompt 含图片，远大于文本方案的 87/题）
#
# 说明：legacy 原 pipeline 的 config.PROVIDER/PAGE_MODE/TPL_* 字段在迁移 DOM 方案时已被剥离，
# 且模板 PNG 资产(crop_assets.py 交互生成)不存在，故无法直接跑 main.py。本脚本复刻其
# 真实代价核心——「逐题截图 → glm-4v-flash 视觉判题」——并复用相同图像预处理
# (最长边 MAX_IMAGE_SIDE 缩放 + PNG Base64)，因此测得的图像 Token / 耗时即视觉方案真实代价。
#
# 健壮性：① 启动清 /tmp 残留；② 所有 exec_js 走 safe_exec（失败清残留+重试，规避偶发 -10004 假死）。
#
# 用法：  TRIALS=1 .venv/bin/python tests/benchmark_eval_legacy.py   # 先单轮冒烟
#         .venv/bin/python tests/benchmark_eval_legacy.py            # 默认 5 轮
# -----------------------------------------------------------------------------
import os
import sys
import io
import json
import time
import base64
import subprocess
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
import dom_reader
from PIL import Image
from zhipuai import ZhipuAI

VISION_MODEL = "glm-4v-flash"
MAX_IMAGE_SIDE = 1280  # 与 legacy.solver._prepare 的最长边限制一致（像素）
TRIALS = int(os.environ.get("TRIALS", "5"))

# 内存补丁：让 legacy 视觉模型所需的配置属性存在（不改动任何文件）
config.ZHIPU_MODEL = VISION_MODEL
config.MAX_IMAGE_SIDE = MAX_IMAGE_SIDE
config.TEMPERATURE = 0.0

client = ZhipuAI(api_key=config.ZHIPU_API_KEY)

TASK_JS = dom_reader.TASK_JS
RESULT_JSON = dom_reader.RESULT_JSON


def _clean_tmp():
    for p in (TASK_JS, RESULT_JSON):
        try:
            p.unlink()
        except OSError:
            pass


def safe_exec(js: str, retries: int = 4):
    """带重试的 exec_js：失败（偶发 -10004 残留）时清 /tmp 并重试。"""
    last = None
    for a in range(1, retries + 1):
        try:
            return dom_reader.exec_js(js)
        except RuntimeError as e:
            last = e
            _clean_tmp()
            if a < retries:
                time.sleep(1.0 * a)
    raise RuntimeError(f"exec_js 多次失败: {last}")


def safe_call(fn, *args, retries: int = 4, **kwargs):
    last = None
    for a in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except RuntimeError as e:
            last = e
            _clean_tmp()
            if a < retries:
                time.sleep(1.0 * a)
    raise RuntimeError(f"{fn.__name__} 多次失败: {last}")


SYS_PROMPT = (
    "你是一个高精度的在线测试答题助手。你将看到一道题目的屏幕截图。"
    "严格只输出 JSON："
    '{"type":"single","answers":["A"]} 或 {"type":"multiple","answers":["A","C"]} '
    '或 {"type":"boolean","answers":["A"]} 或 {"type":"fill_in","answers":["xxx"]}。'
    "禁止任何解释、禁止 Markdown 代码围栏。"
)

RECT_JS = """
(function(){
  var SELS=['li[class*="subject"]','li.subject','.subject-item','div.q','.question-item','[class*=question]'];
  var conts=[];
  for(var s=0;s<SELS.length;s++){var c=document.querySelectorAll(SELS[s]); if(c.length){conts=c;break;}}
  var el=conts[{i}];
  if(!el) return JSON.stringify({missing:true});
  el.scrollIntoView({block:'center'});
  var r=el.getBoundingClientRect();
  var meta={winX:window.screenX, winY:window.screenY, scale:window.devicePixelRatio,
            vh:window.innerHeight, vw:window.innerWidth,
            ox:(window.outerWidth-window.innerWidth)/2,      // 左右边框(含滚动条)宽度
            oy:(window.outerHeight-window.innerHeight)};      // 工具栏等顶部 chrome 高度
  return JSON.stringify({rect:{top:r.top,left:r.left,width:r.width,height:r.height}, meta:meta});
})()
"""


def prepare_image(img: Image.Image) -> str:
    """与 legacy.solver._prepare/_raw_b64 一致的编码：RGB + 最长边缩放 + PNG Base64。"""
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > MAX_IMAGE_SIDE:
        ratio = MAX_IMAGE_SIDE / longest
        img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def vlm_solve(b64: str):
    last_err = None
    for attempt in range(1, config.MAX_RETRY + 1):
        try:
            resp = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": b64}},
                    {"type": "text", "text": SYS_PROMPT},
                ]}],
                temperature=0.0,
            )
            return resp.choices[0].message.content, resp.usage
        except Exception as exc:
            last_err = exc
            print(f"  [vlm] 第{attempt}次失败: {exc}")
            if attempt < config.MAX_RETRY:
                time.sleep(config.RETRY_DELAY * attempt)
    raise RuntimeError(f"VLM 调用失败: {last_err}")


def _parse(content: str):
    """只解析第一个完整 JSON 对象，忽略其后的围栏/重复副本/解释文本。"""
    import re
    i = content.find("{")
    if i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(content[i:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    clean = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except Exception:
        pass
    return {"type": "single", "answers": []}


def run_single_trial(n: int, dom_types: list):
    import pyautogui
    answers = [None] * n
    t_read = 0.0
    t_solve = 0.0
    p_tok = c_tok = tot_tok = 0

    for i in range(n):
        # 滚到该题居中并取包围盒（一次 JS 完成）
        info = json.loads(safe_exec(RECT_JS.replace("{i}", str(i))))
        if info.get("missing"):
            continue
        r = info["rect"]
        meta = info["meta"]
        scale = meta["scale"]
        # 视口在屏幕物理像素中的起点 = 窗口左上角 + 浏览器 chrome 偏移（工具栏/边框）
        vx0 = meta["winX"] + meta["ox"]
        vy0 = meta["winY"] + meta["oy"]

        # DOMRunner 执行 JS 会抢占前台焦点；截图前必须把 Safari 切回最前台，否则截到别的窗口
        subprocess.run(["open", "-a", "Safari"], check=False)
        time.sleep(0.4)

        # —— 感知：截图采集（该题为已居中，整屏截图后裁题）——
        st = time.perf_counter()
        shot = pyautogui.screenshot()
        t_read += time.perf_counter() - st

        # 裁题（物理像素 = (视口起点 + 视口内坐标) * devicePixelRatio；越界则 clamp）
        px = (vx0 + r["left"]) * scale
        py = (vy0 + r["top"]) * scale
        pw = r["width"] * scale
        ph = r["height"] * scale
        x0, y0 = max(0, int(px)), max(0, int(py))
        x1, y1 = min(shot.width, int(px + pw)), min(shot.height, int(py + ph))
        if x1 <= x0 or y1 <= y0:
            continue
        qimg = shot.crop((x0, y0, x1, y1)).convert("RGB")
        b64 = prepare_image(qimg)

        # —— 推理：VLM 视觉判题 ——
        vt = time.perf_counter()
        content, usage = vlm_solve(b64)
        t_solve += time.perf_counter() - vt
        p_tok += getattr(usage, "prompt_tokens", 0) or 0
        c_tok += getattr(usage, "completion_tokens", 0) or 0
        tot_tok += getattr(usage, "total_tokens", 0) or 0

        parsed = _parse(content)
        qtype = str(parsed.get("type", dom_types[i] or "single")).lower()
        ans = parsed.get("answers", [])
        if isinstance(ans, str):
            ans = [ans]
        ans = [str(a).strip() for a in ans if str(a).strip()]
        if qtype in ("single", "boolean"):
            answers[i] = {"type": qtype, "answer": (ans[0] if ans else None)}
        elif qtype == "multiple":
            answers[i] = {"type": "multiple", "answer": ans}
        else:
            answers[i] = {"type": "fill", "answer": (ans[0] if ans else "")}

    # —— 动作派发与回读校验 ——
    t2 = time.perf_counter()
    safe_call(dom_reader.apply_answers, answers, prefer_export=False)
    verify = safe_call(dom_reader.read_questions, prefer_export=False)
    t_apply = time.perf_counter() - t2

    # 用「回读后的真实选中状态」判定回填成功率（比 applied[].ok 可靠：
    # applied[].ok 在 radio 已选中且结果变更时会因逐项判定顺序误报 false）
    def _norm(x):
        if isinstance(x, (list, tuple)):
            return sorted(str(a).strip().upper() for a in x)
        if x is None:
            return []
        return [str(x).strip().upper()]

    vqs = verify.get("questions", [])
    match = 0
    for i, q in enumerate(vqs):
        tgt = answers[i]
        if not tgt:
            continue
        if _norm(q.get("current")) == _norm(tgt.get("answer")):
            match += 1
    rate = (match / n * 100.0) if n else 0.0

    return {
        "n_questions": n,
        "t_read": t_read,
        "t_solve": t_solve,
        "t_apply": t_apply,
        "t_total": t_read + t_solve + t_apply,
        "apply_rate": rate,
        "prompt_tokens": p_tok,
        "completion_tokens": c_tok,
        "total_tokens": tot_tok,
    }


if __name__ == "__main__":
    _clean_tmp()
    print(f"Legacy 视觉方案基准：共 {TRIALS} 次试验，glm-4v-flash，需 Safari 停留在答题页…")
    data0 = safe_call(dom_reader.read_questions, prefer_export=False)
    n = len(data0.get("questions", []))
    dom_types = [q.get("type", "single") for q in data0.get("questions", [])]
    print(f"题目数 n = {n}")

    results = []
    for i in range(TRIALS):
        r = run_single_trial(n, dom_types)
        results.append(r)
        print(f"  trial {i+1}: n={r['n_questions']} t_total={r['t_total']:.2f}s "
              f"apply_rate={r['apply_rate']:.1f}% tokens={r['total_tokens']}")

    print(f"\n题目数 n = {n}（{TRIALS} 次试验 → 均值 ± 标准差）\n")
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

    p_m, p_s = _agg("prompt_tokens")
    c_m, c_s = _agg("completion_tokens")
    t_m, t_s = _agg("total_tokens")
    print("\nToken 统计（单题平均 = 总量 / 题数 n）:")
    print(f"  prompt_tokens    : {p_m:.0f} ± {p_s:.0f}   (单题 {p_m/n:.1f})" if n
          else f"  prompt_tokens    : {p_m:.0f} ± {p_s:.0f}")
    print(f"  completion_tokens: {c_m:.0f} ± {c_s:.0f}   (单题 {c_m/n:.1f})" if n
          else f"  completion_tokens: {c_m:.0f} ± {c_s:.0f}")
    print(f"  total_tokens     : {t_m:.0f} ± {t_s:.0f}   (单题 {t_m/n:.1f})" if n
          else f"  total_tokens     : {t_m:.0f} ± {t_s:.0f}")
