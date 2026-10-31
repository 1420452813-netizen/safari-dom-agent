"""
核心配置（发布模板）。

⚠️ 本文件是 config.py 的「安全模板」，不含任何真实密钥。
   使用方式：把本文件复制为 config.py，再填入你自己的 Key：
       cp config.example.py config.py
   然后编辑下面的 API Key / 模型名。

支持两类模型：
  A) 视觉模型（老「截图 + 坐标」方案用，solver.py）：豆包 / 智谱 glm-4v-flash / 通用 OpenAI 兼容
  B) 纯文本模型（推荐「DOM 直读」方案用，text_solver.py）：同上但走文本通道，更省更快
"""

from pathlib import Path

# ============ 路径 ============
BASE_DIR = Path(__file__).resolve().parent
ASSET_DIR = BASE_DIR / "assets"          # 模板图片目录（crop_assets.py 自动写入）
try:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
except FileExistsError:
    pass

# ============ 模型提供方（视觉方案，对应 solver.py）============
# "zhipu"  智谱 glm-4v-flash（免费多模态，默认，最省事）
# "doubao" 火山方舟 Ark（需先在控制台开通模型）
# "openai" 任意 OpenAI 兼容端点（硅基流动 / 魔搭 / Gemini / OpenRouter / 本地 Ollama）
PROVIDER = "zhipu"

# ---- 豆包 / 火山方舟（OpenAI 兼容）----
DOUBAO_API_KEY = "在此填写火山方舟APIKey"            # 火山引擎控制台 → 方舟 → API Key
DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
# 填「推理接入点 ID」(ep-xxxxxxxx) 或模型名，如 doubao-1.5-vision-pro-32k-250115
DOUBAO_MODEL = "在此填写模型名或ep-ID"
DOUBAO_MAX_TOKENS = 1024

# ---- 智谱 glm-4v-flash ----
ZHIPU_API_KEY = "在此填写智谱APIKey"                # https://open.bigmodel.cn/ → 控制台 → API Keys
ZHIPU_MODEL = "glm-4v-flash"

# ---- 通用 OpenAI 兼容端点（PROVIDER="openai" 时生效）----
GENERIC_BASE_URL = "https://api.siliconflow.cn/v1"   # 例：硅基流动
GENERIC_API_KEY = "在此填写对应平台APIKey"
GENERIC_MODEL = "Qwen/Qwen2.5-VL-72B-Instruct"
GENERIC_MAX_TOKENS = 1024

# ============ 通用模型参数 ============
TEMPERATURE = 0.0        # 设为 0，降低采样随机性，响应最快且结果确定
MAX_RETRY = 3            # 调用失败重试次数
RETRY_DELAY = 2.0        # 每次重试间隔（秒）
MAX_IMAGE_SIDE = 1600    # 送模型前图片最长边，降低 token 消耗

# ============ 截屏 ROI（仅视觉方案用）============
QUESTION_ROI = None      # (left, top, width, height)，None = 整屏

# ============ 轮询 ============
POLL_INTERVAL = 1.0

# ============ 模板匹配（仅视觉方案用）============
MATCH_THRESHOLD = 0.80
NMS_IOU = 0.30

# ============ 模板文件名（assets/ 下，仅视觉方案用）============
TPL_NEXT = "next.png"
TPL_SUBMIT = "submit.png"
TPL_CHOICES = {"A": "A.png", "B": "B.png", "C": "C.png", "D": "D.png"}
TPL_TRUE = "true.png"
TPL_FALSE = "false.png"
TPL_BLANKS = ["blank1.png", "blank2.png", "blank3.png", "blank4.png"]

# ============ 页面模式（仅视觉方案用）============
PAGE_MODE = "onepage"
TPL_HANDIN = "jiaojuan.png"
TPL_CARDS = [f"card{i}.png" for i in range(1, 11)]
AUTO_SUBMIT = False
CLICK_X_RATIO = 0.35
OPTION_GAP_LOGICAL = 120
SCROLL_CLICKS = 40
MAX_QUESTIONS = 10

# ============ 交互节奏 ============
DELAY_BEFORE_NEXT = (1.0, 2.0)
PAGE_LOAD_WAIT = 2.0

# ============ 安全 ============
FAILSAFE = True          # 鼠标甩至屏幕左上角紧急退出
DRY_RUN = False          # True = 只识别不点击，用于调试

# ============ 缩放 ============
SCREEN_SCALE = None      # None = 自动按「截图宽 / 逻辑宽」推算（适配 Retina）

# ============ DOM 方案（推荐主路径）============
# 用 Safari 的 do JavaScript 直接读页面 DOM 拿到题项文字，再用「纯文本模型」判题。
TEXT_PROVIDER = "zhipu"                      # zhipu / doubao / openai
ZHIPU_TEXT_MODEL = "glm-4-flash"             # 智谱文本模型（免费）
DOUBAO_TEXT_MODEL = "在此填写豆包文本模型名"
GENERIC_TEXT_MODEL = "Qwen/Qwen2.5-72B-Instruct"
TEXT_MAX_TOKENS = 64       # 极短分析+JSON 即可；原 1024 偏大，调小到 64 更省 token、更快
FILL_MAX_TOKENS = 256       # 填空题用独立上限：答案可能是较长中文短语，64 会被截断，放宽到 256

# 双模型会审（更稳，但慢）：False=单模型（默认）；True=主+备选各判一次，不一致交仲裁
ENSEMBLE = False
ZHIPU_TEXT_MODEL_2 = "glm-4.5-flash"
ZHIPU_ARBITER_MODEL = "glm-4-plus"
JUDGE_CONCURRENCY = 8                        # 建议根据 API 提供商的 RPM 限制设为 5 ~ 10

# 单选题判题提示词（可选覆盖项）。
# 若此处定义了 JUDGE_PROMPT，则优先使用本值；否则 text_solver.py 回退到内置的「带 CoT 详细版」。
# 极速版：压缩输出 token（只要求最后一行输出 JSON）。
# 注意：模板含 {"answer":"X"} 花括号，组装时务必用 .replace()，不要用 str.format()。
JUDGE_PROMPT = """你是课程助教。请在一句话内简明指出考点与正确选项，最后一行必须输出 JSON：{"answer":"X"}
题目：
{question}
选项：
{options}
"""

# DOM 脚本导出目录（无「控制 Safari」权限时，导出桌面 .applescript 双击运行）
DOM_EXPORT_DIR = "~/Desktop"
DOM_JS_NAME = "_tronclass_dom.js"
DOM_SCRIPT_NAME = "1_读题.applescript"
DOM_APPLY_SCRIPT_NAME = "2_答题.applescript"
DOM_QUESTIONS_FILE = "questions.json"
DOM_ANSWERS_FILE = "answers.json"


def active_model() -> str:
    """当前视觉提供方对应的模型名，供日志展示。"""
    p = PROVIDER.lower()
    if p in ("doubao", "ark", "volc", "volcengine"):
        return DOUBAO_MODEL
    if p in ("zhipu", "glm", "zhipuai"):
        return ZHIPU_MODEL
    return GENERIC_MODEL
