"""
交互式模板截取工具。

用法：
  1) 默认流程（截取 A/B/C/D、下一题、提交）：
       python crop_assets.py

  2) 补充其它模板（如判断题、填空框）：
       python crop_assets.py true false blank1 blank2

操作：
  弹出截图窗口后，用鼠标框选目标区域 → 回车/空格确认 → 图像自动保存到 assets/。
  按 c 可跳过当前项。
"""

from __future__ import annotations

import sys
from typing import List, Optional, Tuple

import cv2
import numpy as np
import pyautogui

# 本文件已移至 legacy/ 子目录；config 仍在项目根，需把根目录加入模块搜索路径
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

DEFAULT_TARGETS: List[Tuple[str, str]] = [
    ("A.png", "选项 A 标记（建议连左侧圆圈一起框：○ A）"),
    ("B.png", "选项 B 标记（○ B）"),
    ("C.png", "选项 C 标记（○ C）"),
    ("D.png", "选项 D 标记（○ D）"),
    ("next.png", "【▼ 下一题】按钮（paged 模式用）"),
    ("submit.png", "【提交】按钮（paged 模式用）"),
    ("jiaojuan.png", "【最终提交】按钮（onepage 整卷单页模式用）"),
]


def select_region(title: str) -> Optional[np.ndarray]:
    """全屏截图后弹出选框，返回裁剪后的 BGR 图像；取消返回 None。"""
    print(f"\n[{title}] 请框选区域：回车/空格确认，c 取消")
    shot = pyautogui.screenshot()
    img = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)

    r = cv2.selectROI(title, img, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(title)

    x, y, w, h = int(r[0]), int(r[1]), int(r[2]), int(r[3])
    if w == 0 or h == 0:
        print("  已跳过")
        return None
    return img[y:y + h, x:x + w]


def save_region(name: str, region: np.ndarray) -> None:
    path = config.ASSET_DIR / name
    cv2.imwrite(str(path), region)
    print(f"  ✓ 已保存: {path}")


def _targets_from_argv(argv: List[str]) -> List[Tuple[str, str]]:
    """python crop_assets.py next blank1 -> 只截取这两项。"""
    out = []
    for name in argv:
        fn = name if name.lower().endswith(".png") else name + ".png"
        out.append((fn, fn.rsplit(".", 1)[0]))
    return out


def main() -> None:
    targets = _targets_from_argv(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_TARGETS
    print("=" * 56)
    print(" 模板截取工具")
    print(f" 共 {len(targets)} 项，截图将保存到: {config.ASSET_DIR}")
    print(" 提示：先切到受测页面，确保目标清晰可见再框选")
    print("=" * 56)

    for fn, label in targets:
        region = select_region(label)
        if region is not None:
            save_region(fn, region)

    print("\n全部完成。可用 `python main.py` 启动回填。")
    print("补充模板示例:")
    print("  python crop_assets.py true false blank1 blank2      # 判断题/填空框")
    print("  python crop_assets.py card1 card2 card3 ... card10  # 答题卡序号（onepage 精准导航，可选）")


if __name__ == "__main__":
    main()
