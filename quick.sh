#!/usr/bin/env bash
# TronClass 辅助评估 · 便捷启动脚本
# 前置：Safari 已打开并登录到受测页面（评估进行中）
set -e
cd "$(dirname "$0")"
PY=./.venv/bin/python

case "${1:-help}" in
  go)      # 读题→判题→点选→回读校验（不提交，最常用、最安全）
    $PY dom_main.py read
    $PY dom_main.py solve
    $PY dom_main.py apply
    echo "=== 回读校验（确认点选已生效）==="
    $PY dom_main.py read
    ;;
  submit)  # 真实最终提交（不可逆！）
    $PY dom_main.py submit
    ;;
  auto)     # 读→判→点→回读→确认后最终提交（全自动，交前仍需输入 yes 确认）
    $PY dom_main.py auto
    ;;
  read|solve|apply|all)
    $PY dom_main.py "$1"
    ;;
  *)
    echo "用法: ./quick.sh [go|submit|read|solve|apply|all]"
    echo "  go      读题+判题+点选+回读校验（不提交，日常首选）"
    echo "  submit  真实最终提交（不可逆，确认无误再跑）"
    echo "  read/solve/apply/all  等价于 dom_main.py 同名子命令"
    ;;
esac
