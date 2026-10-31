"""
图像匹配、坐标缩放换算、点击与文本输入执行。

要点：
- 截图按物理像素采集；pyautogui 点击用逻辑坐标，故需除以 scale（Retina=2）。
- 模板匹配使用局部极大值 + NMS，避免同一目标产生海量重复点。
- 中文/特殊符号通过剪贴板粘贴，规避输入法乱码。
"""

from __future__ import annotations

import platform
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import pyautogui
import pyperclip
from PIL import Image

import config

pyautogui.FAILSAFE = config.FAILSAFE
pyautogui.PAUSE = 0.05

Box = Tuple[int, int, int, int]  # x, y, w, h（截图物理像素坐标）
Point = Tuple[float, float]

IS_MAC = platform.system() == "Darwin"
MOD_KEY = "command" if IS_MAC else "ctrl"


# ---------------- 截屏与坐标 ----------------
def capture(roi: Optional[Box] = None) -> Image.Image:
    """截取全屏；roi 为物理像素区域时裁剪。"""
    shot = pyautogui.screenshot()
    if roi:
        l, t, w, h = roi
        shot = shot.crop((int(l), int(t), int(l + w), int(t + h)))
    return shot


def compute_scale(shot: Image.Image) -> float:
    """物理像素 / 逻辑坐标 的缩放比（Retina 通常为 2.0）。"""
    if config.SCREEN_SCALE:
        return float(config.SCREEN_SCALE)
    logical_w = pyautogui.size().width
    return shot.width / logical_w if logical_w else 1.0


def crop_roi(shot: Image.Image, roi_logical: Box, scale: float) -> Image.Image:
    """按逻辑坐标 ROI 从物理像素截图中裁剪题项区域。"""
    l, t, w, h = roi_logical
    return shot.crop((int(l * scale), int(t * scale),
                      int((l + w) * scale), int((t + h) * scale)))


# ---------------- 模板匹配 ----------------
def _pil_to_bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _iou(a, b) -> float:
    ax, ay, aw, ah = a[:4]
    bx, by, bw, bh = b[:4]
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, x2 - x1), max(0, y2 - y1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _nms(dets: List[Tuple[int, int, int, int, float]], thr: float):
    dets = sorted(dets, key=lambda d: d[4], reverse=True)
    keep = []
    while dets:
        best = dets.pop(0)
        keep.append(best)
        dets = [d for d in dets if _iou(best, d) < thr]
    return keep


def find_all(shot: Image.Image, template_path: Path,
             threshold: Optional[float] = None) -> List[Box]:
    """返回所有匹配框（物理像素），按从上到下、从左到右排序。"""
    template_path = Path(template_path)
    if not template_path.exists():
        return []
    thr = config.MATCH_THRESHOLD if threshold is None else threshold
    screen = _pil_to_bgr(shot)
    tpl = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if tpl is None:
        return []
    th, tw = tpl.shape[:2]
    sh, sw = screen.shape[:2]
    if th > sh or tw > sw:
        return []

    res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)

    # 局部极大值：抑制相邻像素重复命中
    kernel = np.ones((7, 7), np.uint8)
    local_max = cv2.dilate(res, kernel)
    ys, xs = np.where((res >= thr) & (res >= local_max - 1e-6))
    dets = [(int(x), int(y), tw, th, float(res[y, x])) for x, y in zip(xs, ys)]
    if not dets:
        return []

    keep = _nms(dets, config.NMS_IOU)
    keep.sort(key=lambda b: (b[1], b[0]))
    return [(b[0], b[1], b[2], b[3]) for b in keep]


# ---------------- 物理操作 ----------------
def _logical_center(box: Box, scale: float) -> Point:
    x, y, w, h = box
    return ((x + w / 2) / scale, (y + h / 2) / scale)


def click_box(box: Box, scale: float) -> None:
    cx, cy = _logical_center(box, scale)
    if config.DRY_RUN:
        print(f"  [DRY_RUN] click -> ({cx:.0f}, {cy:.0f})")
        return
    pyautogui.click(cx, cy)


def type_into(box: Box, text: str, scale: float) -> None:
    """点击输入框 → 全选清空 → 剪贴板粘贴，规避中文/符号乱码。"""
    if config.DRY_RUN:
        print(f"  [DRY_RUN] type into {box} -> {text!r}")
        return
    click_box(box, scale)
    time.sleep(0.12)
    pyautogui.hotkey(MOD_KEY, "a")   # 全选
    time.sleep(0.05)
    pyautogui.press("backspace")     # 清空
    time.sleep(0.05)
    pyperclip.copy(str(text))
    time.sleep(0.05)
    pyautogui.hotkey(MOD_KEY, "v")   # 粘贴


# ---------------- onepage 模式：聚类与滚动 ----------------
def click_option(box: Box, scale: float) -> None:
    """点击选项：横向取 CLICK_X_RATIO 位置，偏向左侧圆形单选框。"""
    x, y, w, h = box
    cx = (x + w * config.CLICK_X_RATIO) / scale
    cy = (y + h / 2) / scale
    if config.DRY_RUN:
        print(f"  [DRY_RUN] click option -> ({cx:.0f}, {cy:.0f})")
        return
    pyautogui.click(cx, cy)


def scroll_down(clicks: int) -> None:
    """向下滚动（pyautogui.scroll 正值=向上，故取负）。"""
    if config.DRY_RUN:
        print(f"  [DRY_RUN] scroll down {clicks}")
        return
    pyautogui.scroll(-abs(int(clicks)))


def build_clusters(shot: Image.Image, scale: float) -> List[dict]:
    """
    以选项 A 为锚点，把同一题的 A/B/C/D 聚成一组。

    返回按从上到下排序的 [{"A": box, "B": box, "C": box, "D": box}, ...]。
    已回填的题项因圆形单选框变为选中态，模板将不再匹配，从而天然跳过。
    """
    boxes = {L: find_all(shot, config.ASSET_DIR / config.TPL_CHOICES[L])
             for L in "ABCD"}
    if not boxes["A"]:
        return []

    gap = config.OPTION_GAP_LOGICAL * scale      # 相邻选项最大纵向间距（物理像素）
    xtol = 40 * scale                            # 横向对齐容差
    clusters = []

    for a in sorted(boxes["A"], key=lambda b: b[1]):
        ax = a[0] + a[2] / 2
        grp = {"A": a}
        prev_y = a[1]
        ok = True
        for L in "BCD":
            cands = [b for b in boxes[L]
                     if abs((b[0] + b[2] / 2) - ax) <= xtol
                     and 0 < b[1] - prev_y <= gap]
            if not cands:
                ok = False
                break
            best = min(cands, key=lambda b: b[1])
            grp[L] = best
            prev_y = best[1]
        if ok:
            clusters.append(grp)

    clusters.sort(key=lambda g: g["A"][1])
    return clusters
