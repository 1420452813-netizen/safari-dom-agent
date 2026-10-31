#!/usr/bin/env bash
# =============================================================================
# build_app.sh — TronClass Auto Solver 分发版构建脚本 (TC-SPEC-2026-V3 §5)
#
# 全自动完成：PyInstaller 冻结后端 → swiftc 编译启动器 → 组装 .app →
# ad-hoc 重签名 → 清属性 → 生成干净 zip（无 ._ 冗余）。
#
# 用法：  bash build_app.sh
# 产出：  ./TronClass.app            (可直接拖进 应用程序 的包)
#         ./TronClass-macOS.zip      (分发给同学的最终产物)
# =============================================================================
set -euo pipefail

# 脚本所在目录 = 工程根（含 dom_main.py / Launcher.swift / .venv）
SRC="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SRC"
cd "$ROOT"

VENV_PY="$SRC/.venv/bin/python"
PYINSTALLER="$SRC/.venv/bin/pyinstaller"

# 中间产物目录
BUILD="$ROOT/build"          # PyInstaller 工作目录 + .spec
DIST="$ROOT/dist"            # PyInstaller 输出 (dom_main_bin)
STAGE="$ROOT/build_app/TronClass.app"

echo "==> [1/7] 清理旧构建产物"
rm -rf "$BUILD" "$DIST" "$ROOT/build_app" "$ROOT/__pycache__" "$ROOT"/*.spec
mkdir -p "$BUILD" "$DIST" "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources"

# ---------------------------------------------------------------------------
# [2] PyInstaller：冻结 dom_main.py -> dom_main_bin (arm64, --onefile)
#     - 剥离 cv2 / numpy（源码已无视觉方案依赖，体积从 260MB 压到 ~30MB）
#     - zhipuai / openai 在 text_solver 内「延迟 import」，必须 --collect-all 收集
#     - pydantic / httpx / jwt 为上述 SDK 的运行时依赖，显式 hidden-import 兜底
#     - pyautogui / Quartz 在 dom_main 内延迟 import，同样需 hidden-import
# ---------------------------------------------------------------------------
echo "==> [2/7] PyInstaller 冻结后端 (dom_main_bin)"
"$PYINSTALLER" --onefile \
    --name dom_main_bin \
    --exclude-module cv2 \
    --exclude-module numpy \
    --collect-all zhipuai \
    --collect-all openai \
    --hidden-import pydantic \
    --hidden-import httpx \
    --hidden-import jwt \
    --hidden-import pyautogui \
    --hidden-import Quartz \
    --paths "$SRC" \
    --distpath "$DIST" \
    --workpath "$BUILD" \
    --specpath "$BUILD" \
    dom_main.py

# ---------------------------------------------------------------------------
# [3] swiftc：编译 Launcher.swift -> DOMLauncher（GUI 启动器）
# ---------------------------------------------------------------------------
echo "==> [3/7] swiftc 编译启动器 (DOMLauncher)"
swiftc "$SRC/Launcher.swift" -o "$STAGE/Contents/MacOS/DOMLauncher" -O

# ---------------------------------------------------------------------------
# [4] 组装 .app 包
# ---------------------------------------------------------------------------
echo "==> [4/7] 组装 .app 包"
# 执行通道按需生成：二进制不入库（仓库只保留 runner.applescript 源码）。
# 首次构建会现场编译出一个独立 bundle；它有自己的签名身份，首次运行时需授权一次。
if [ ! -d "$SRC/exports/DOMRunner.app" ]; then
    echo "    → 未发现 DOMRunner.app，从源码编译"
    osacompile -o "$SRC/exports/DOMRunner.app" "$SRC/exports/runner.applescript"
fi
cp "$DIST/dom_main_bin"                       "$STAGE/Contents/MacOS/dom_main_bin"
ditto "$SRC/exports/DOMRunner.app"            "$STAGE/Contents/Resources/DOMRunner.app"
cp "$SRC/assets/AppIcon.icns"                 "$STAGE/Contents/Resources/AppIcon.icns"
cp "$SRC/Info.plist"                          "$STAGE/Contents/Info.plist"
# 随包使用说明：同时放进 app 内部（自包含）与 zip 根目录（解压即见）
cp "$SRC/首次使用说明.txt"                     "$STAGE/Contents/Resources/首次使用说明.txt"
chmod +x "$STAGE/Contents/MacOS/DOMLauncher" "$STAGE/Contents/MacOS/dom_main_bin"

# ---------------------------------------------------------------------------
# [5] ad-hoc 重签名
#     ⚠️ 绝不能用 --deep：它会把嵌套的 DOMRunner.app 重新签名，改变其 cdhash，
#        从而让用户已授权的 TCC（自动化 → Safari）记录失配、静默失效。
#        正确做法：只签包内两个可执行文件 + 外层 app，嵌套 Runner 保留出厂签名。
# ---------------------------------------------------------------------------
echo "==> [5/7] ad-hoc 重签名 (逐层，保留嵌套 Runner 原签名)"
codesign --force --sign - "$STAGE/Contents/MacOS/dom_main_bin"
codesign --force --sign - "$STAGE/Contents/MacOS/DOMLauncher"
codesign --force --sign - "$STAGE"
codesign --verify --strict "$STAGE" && echo "    ✓ 外层签名校验通过"
codesign -dv --verbose=2 "$STAGE/Contents/Resources/DOMRunner.app" 2>&1 | grep -E "Identifier|CDHash" | sed 's/^/    /'

# ---------------------------------------------------------------------------
# [6] 清除扩展属性（quarantine 等），避免首次打开被拦截
# ---------------------------------------------------------------------------
echo "==> [6/7] 清除扩展属性 (xattr -cr)"
xattr -cr "$STAGE"

# ---------------------------------------------------------------------------
# [7] 生成干净 zip（zip -X -y：排除 ._ 冗余、保留符号链接）
# ---------------------------------------------------------------------------
echo "==> [7/7] 生成 TronClass-macOS.zip"
rm -f "$ROOT/TronClass-macOS.zip"
( cd "$ROOT" && zip -X -y -r "$ROOT/TronClass-macOS.zip" TronClass.app "首次使用说明.txt" )

# 顺便把构建好的包放回工程根，便于直接调试 / 双击使用
rm -rf "$ROOT/TronClass.app"
cp -R "$STAGE" "$ROOT/TronClass.app"

# ---------------------------------------------------------------------------
# 清理中间目录，仅保留交付物：TronClass.app + TronClass-macOS.zip
# ---------------------------------------------------------------------------
rm -rf "$ROOT/build" "$ROOT/dist" "$ROOT/build_app" "$ROOT/__pycache__" "$ROOT"/*.spec

echo
echo "✅ 构建完成"
echo "   应用包 : $ROOT/TronClass.app"
echo "   分发包 : $ROOT/TronClass-macOS.zip  (含 TronClass.app + 首次使用说明.txt)"
du -h "$ROOT/TronClass-macOS.zip"
