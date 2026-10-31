#!/bin/bash
set -e
# 定位到当前项目目录（脚本与 Launcher.swift 同在项目根目录）
cd "$(dirname "$0")"

echo "=========================================="
echo "   正在启动 TronClass 自动化控制台..."
echo "=========================================="

APP="TronClass.app"
BIN="$APP/Contents/MacOS/DOMLauncher"

if command -v swiftc >/dev/null 2>&1; then
  # ⚠️ 不能用 `swift Launcher.swift`（解释器 JIT 模式）：
  #    实测报 JIT session error: Symbols not found: [ ___isPlatformVersionAtLeast ]，
  #    SwiftUI/AppKit 的可用性符号在 JIT 下无法解析。必须用 swiftc 编译成二进制。

  # 仅在「二进制不存在」或「源码比二进制新」时才重新编译，其余情况秒开
  need_build=0
  if [ ! -x "$BIN" ]; then
    need_build=1
  elif [ "Launcher.swift" -nt "$BIN" ]; then
    need_build=1
  fi

  if [ "$need_build" -eq 1 ]; then
    echo "检测到需重新编译，正在编译 Launcher.swift ..."
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
    swiftc Launcher.swift -o "$BIN" -O
    touch "$APP"    # 触发 Finder 刷新图标缓存
    echo "✅ 编译完成"
  fi

  echo "正在拉起原生 GUI ..."
  exec open "$APP"
else
  # 未装 Command Line Tools 时的优雅回退（原生通知 + 终端交互模式）
  osascript -e 'display notification "未检测到 Swift 编译器，改用命令行交互模式" with title "TronClass Auto Solver"'
  # 依赖装在 .venv 里，回退时也必须走 venv，否则会 import 失败
  if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
  elif [ -x ".venv/bin/python3" ]; then
    PY=".venv/bin/python3"
  else
    PY="python3"
  fi
  echo "使用解释器: $PY"
  exec "$PY" dom_main.py
fi
