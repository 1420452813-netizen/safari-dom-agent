"""
启动自检：不进入主循环、不点击鼠标，只做环境与链路检查。

用法:
    python selftest.py            # 环境 + 模板 + 截屏 + 视觉 API 全检
    python selftest.py --no-api   # 跳过 API 调用（仅查环境/截屏）

检查项:
  1. Python 与依赖是否就绪
  2. 当前 provider / 模型 / key 是否已配置
  3. assets 模板图片是否齐全
  4. 截屏是否可用、缩放比是否正确（Retina）
  5. 视觉模型连通性（用合成题项图做一次真实调用）
"""

from __future__ import annotations

import argparse
import os
import sys

# 本文件已移至 tests/ 子目录；config 在项目根、vision/solver 在 legacy/，需把二者加入搜索路径
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)                            # 项目根（config 在此）
sys.path.insert(0, os.path.join(ROOT, "legacy"))     # legacy/（vision、solver 在此）

import config


def section(title: str) -> None:
    print("\n" + "-" * 56)
    print(title)
    print("-" * 56)


def check_env() -> bool:
    section("1. 运行环境")
    print("Python:", sys.version.split()[0])
    ok = True
    for mod in ("cv2", "numpy", "PIL", "pyautogui", "pyperclip"):
        try:
            __import__(mod)
            print(f"  [OK] {mod}")
        except Exception as exc:
            ok = False
            print(f"  [MISS] {mod}: {exc}")
    # 提供方 SDK
    p = config.PROVIDER.lower()
    sdk = "zhipuai" if p in ("zhipu", "glm", "zhipuai") else "openai"
    try:
        __import__(sdk)
        print(f"  [OK] {sdk}")
    except Exception as exc:
        ok = False
        print(f"  [MISS] {sdk}: {exc}")
    return ok


def check_config() -> bool:
    section("2. 配置")
    print("PROVIDER:", config.PROVIDER)
    print("MODEL   :", config.active_model())
    print("PAGE_MODE:", config.PAGE_MODE)
    print("DRY_RUN :", config.DRY_RUN)
    key = config.DOUBAO_API_KEY if config.PROVIDER.lower() == "doubao" else config.ZHIPU_API_KEY
    filled = bool(key) and not key.startswith("在此填写")
    print("API Key :", "已填写" if filled else "未填写")
    return filled


def check_assets() -> None:
    section("3. 模板图片")
    need = list(config.TPL_CHOICES.values())
    if config.PAGE_MODE == "onepage":
        need += [config.TPL_HANDIN]
    else:
        need += [config.TPL_NEXT, config.TPL_SUBMIT]
    missing = [n for n in need if not (config.ASSET_DIR / n).exists()]
    have = [n for n in need if (config.ASSET_DIR / n).exists()]
    print("已存在:", have or "无")
    print("缺失  :", missing or "无")
    if missing:
        print("  → 运行 `python legacy/crop_assets.py` 采集模板")


def check_screen() -> None:
    section("4. 截屏与缩放")
    try:
        import vision
        shot = vision.capture()
        scale = vision.compute_scale(shot)
        print(f"  截图尺寸: {shot.width} x {shot.height} (物理像素)")
        print(f"  逻辑尺寸: {__import__('pyautogui').size()}")
        print(f"  推算缩放: {scale:.3f}")
        if scale > 1.5:
            print("  → 判定为高 DPI（Retina），点击坐标将自动按 scale 折算")
    except Exception as exc:
        print("  截屏失败:", type(exc).__name__, exc)
        print("  → macOS 请在「系统设置 → 隐私与安全性 → 屏幕录制」授权后重启终端")


def _make_question_image():
    """合成一张单选题图片，用于脱离屏幕验证视觉链路。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (720, 300), "white")
    d = ImageDraw.Draw(img)
    lines = [
        "单选题：Java 中用于显式抛出异常的关键字是？",
        "A. throws",
        "B. throw",
        "C. catch",
        "D. final",
    ]
    y = 30
    for ln in lines:
        d.text((30, y), ln, fill="black")
        y += 45
    return img


def check_api() -> None:
    section("5. 视觉模型连通性")
    try:
        from solver import create_solver
        s = create_solver()
        img = _make_question_image()
        result = s.solve(img)
        print("  调用成功:", result)
    except Exception as exc:
        print("  调用失败:", type(exc).__name__)
        print(" ", str(exc)[:400])
        msg = str(exc)
        if "ModelNotOpen" in msg:
            print("  → 账号未开通该模型：火山方舟控制台 → 模型广场 → 开通对应 VLM")
        elif "InvalidEndpointOrModel" in msg:
            print("  → 模型/接入点不存在：检查 DOUBAO_MODEL 是否为 ep-... 或正确模型名")
        elif "401" in msg or "Authentication" in msg:
            print("  → API Key 无效")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-api", action="store_true", help="跳过视觉 API 调用")
    args = ap.parse_args()

    env_ok = check_env()
    cfg_ok = check_config()
    check_assets()
    check_screen()
    if not args.no_api:
        check_api()

    section("结论")
    if not env_ok:
        print("依赖未就绪：pip install -r requirements.txt")
    elif not cfg_ok:
        print("配置未完成：填写 API Key / 模型")
    else:
        print("环境基本就绪。模板齐全且模型已开通后，可运行 `python legacy/main.py`。")


if __name__ == "__main__":
    main()
