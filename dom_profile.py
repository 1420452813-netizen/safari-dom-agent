"""
站点适配层（adapter profile）——把目标站点的 DOM 约定从代码里彻底剥离。

设计动机
--------
读取层原本把目标站点的类名、按钮文本、弹窗选择器硬编码在 5 段注入 JS 里。
这些字符串等价于一份「站点指纹」：任何人读到源码就知道它面向哪个平台。
本项目因此把它们抽成可替换的 profile，仓库内只保留一份**通用示例**，
真实站点配置放在用户目录（不入库、不打包）。

加载优先级
----------
1. 环境变量 ``TRONCLASS_PROFILE``（指向某个 json 文件）
2. ``~/.tronclass/profile.json``（用户自己的站点配置，推荐）
3. ``profiles/example-spa.json``（仓库内示例，对应 tests/mock_exam.html）
4. 内置 DEFAULT_PROFILE（与示例一致，保证任何情况下都能启动）

字段说明
--------
question.containers   题项容器选择器，按顺序尝试，取第一个有命中的
question.stemTitle     题干专用容器的选择器；命中则直接取文本
question.stemStrip     提取题干时需要从克隆节点中剔除的选择器
submit.selectors       提交按钮选择器，按顺序尝试
submit.textMatch       提交按钮文本约束（空数组 = 不约束）；用于选择器命中多个时的过滤
submit.skipAncestors   文字兜底时需排除的祖先（隐藏弹窗内的同名元素）
confirm.texts          确认按钮文本（完全相等匹配）
confirm.modalAcceptTexts / modalRejectTexts  弹窗内确认/取消按钮的包含式匹配词
confirm.modalSelectors 弹窗容器选择器
confirm.hiddenMarkers  表示「已隐藏」的 class 名
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PLACEHOLDER = "__PROFILE__"

# 与 tests/mock_exam.html 的结构一一对应。刻意保持「通用 SPA 表单」的形态，
# 不含任何真实站点的类名或文案。
DEFAULT_PROFILE: dict = {
    "name": "example-spa",
    "question": {
        "containers": ["li.q-item", ".q-item", "[data-question]", ".question-item"],
        "stemTitle": [".q-stem", ".stem", ".question-title"],
        "stemStrip": ["ol", "ul", ".q-options", ".options", "input", "textarea", "label"],
    },
    "submit": {
        "selectors": ["a.submit-btn", "button.submit-btn", "div.form-footer button.submit-btn"],
        "textMatch": [],
        "skipAncestors": [".modal", ".popup-area"],
    },
    "confirm": {
        "texts": ["确定", "确认", "提交", "继续", "是"],
        "modalAcceptTexts": ["确定", "确认", "提交", "继续", "是", "我知道了"],
        "modalRejectTexts": ["取消", "关闭", "返回", "再想想"],
        "modalSelectors": [".modal", ".popup-area"],
        "hiddenMarkers": ["is-hidden"],
    },
}


def _candidate_paths() -> list[Path]:
    out: list[Path] = []
    env = os.getenv("TRONCLASS_PROFILE")
    if env:
        out.append(Path(env).expanduser())
    out.append(Path.home() / ".tronclass" / "profile.json")
    out.append(Path(__file__).resolve().parent / "profiles" / "example-spa.json")
    return out


def _merge(base: dict, override: dict) -> dict:
    """浅合并到二层（question/submit/confirm），缺字段时用默认值补齐。"""
    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def load_profile() -> tuple[dict, str]:
    """返回 (profile, 来源描述)。任何加载失败都退回默认值，绝不让读取层因此崩溃。"""
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data:
            return _merge(DEFAULT_PROFILE, data), str(path)
    return dict(DEFAULT_PROFILE), "built-in default"


PROFILE, PROFILE_SOURCE = load_profile()


def inject(template: str, profile: dict | None = None) -> str:
    """把 profile 作为 JSON 字面量注入 JS 模板（ASCII 安全，兼容 GBK 落盘链路）。"""
    blob = json.dumps(profile or PROFILE, ensure_ascii=True, separators=(",", ":"))
    return template.replace(PLACEHOLDER, blob)


def reload_profile() -> str:
    """重新读取 profile 并刷新模块级常量（供调试/测试切换站点）。"""
    global PROFILE, PROFILE_SOURCE
    PROFILE, PROFILE_SOURCE = load_profile()
    return PROFILE_SOURCE
