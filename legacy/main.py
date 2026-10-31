"""
监听主循环、会话状态机与流程编排。

两种页面模式（config.PAGE_MODE）：
  "paged"   一题一页：轮询【下一题】，逐题作答（原逻辑）
  "onepage" 整卷单页：识别视口内全部题目 → 逐题点击 → 答题卡/滚动前进
            → 答完默认等待终端确认后才点【最终提交】（AUTO_SUBMIT=False 时）
"""

from __future__ import annotations

import hashlib
import random
import time
import traceback

import pyautogui

# 本文件已移至 legacy/ 子目录；config 仍在项目根，需把根目录加入模块搜索路径
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import vision
from solver import create_solver


class AutoSolver:
    def __init__(self):
        self.solver = create_solver()
        self.last_hash: str | None = None
        self.last_ts: float = 0.0

    # ---------- 入口 ----------
    def run(self) -> None:
        print("=" * 60)
        print("  Web Agent 全辅助评估系统 已启动")
        print(f"  提供方: {config.PROVIDER} | 模型: {config.active_model()}")
        print(f"  页面模式: {config.PAGE_MODE} | DryRun: {config.DRY_RUN}")
        print("  3 秒后开始，请切换到受测页面…")
        print("  紧急停止: 鼠标甩到屏幕左上角 / Ctrl+C")
        print("=" * 60)
        time.sleep(3)
        try:
            if config.PAGE_MODE == "onepage":
                self._run_onepage()
            else:
                self._run_paged()
        except pyautogui.FailSafeException:
            print("\n触发 FAILSAFE，已紧急退出。")
        except KeyboardInterrupt:
            print("\n用户中断，退出。")

    # ================= paged：一题一页 =================
    def _run_paged(self) -> None:
        while True:
            try:
                self._tick()
                time.sleep(config.POLL_INTERVAL)
            except SystemExit:
                raise
            except Exception:
                traceback.print_exc()
                time.sleep(config.POLL_INTERVAL)

    def _tick(self) -> None:
        shot = vision.capture()
        scale = vision.compute_scale(shot)
        next_btn = vision.find_all(shot, config.ASSET_DIR / config.TPL_NEXT)
        submit_btn = vision.find_all(shot, config.ASSET_DIR / config.TPL_SUBMIT)

        if not next_btn and submit_btn:
            self._done()
            raise SystemExit(0)

        if next_btn:
            self._solve_and_next(shot, scale, next_btn[0])

    def _solve_and_next(self, shot, scale: float, next_box) -> None:
        roi = vision.crop_roi(shot, config.QUESTION_ROI, scale) if config.QUESTION_ROI else shot

        digest = hashlib.md5(roi.tobytes()).hexdigest()
        if digest == self.last_hash and time.time() - self.last_ts < 5:
            return
        self.last_hash, self.last_ts = digest, time.time()

        data = self.solver.solve(roi)
        qtype = data.get("type", "single")
        answers = data.get("answers", []) or []
        print(f"[题目] type={qtype} answers={answers}")

        if qtype in ("single", "multiple", "boolean"):
            self._answer_choice(shot, scale, qtype, answers)
        elif qtype == "fill_in":
            self._answer_fill(shot, scale, answers)
        else:
            print("  未知题型，跳过作答")

        delay = random.uniform(*config.DELAY_BEFORE_NEXT)
        print(f"  停顿 {delay:.1f}s 后翻页…")
        time.sleep(delay)
        vision.click_box(next_box, scale)
        time.sleep(config.PAGE_LOAD_WAIT)

    def _answer_choice(self, shot, scale: float, qtype: str, answers) -> None:
        if not answers:
            return
        if qtype == "boolean":
            self._answer_boolean(shot, scale, answers)
            return
        for letter in [str(a).strip().upper() for a in answers]:
            tpl = config.TPL_CHOICES.get(letter)
            if not tpl:
                print(f"  ! 无选项模板: {letter}")
                continue
            boxes = vision.find_all(shot, config.ASSET_DIR / tpl)
            if not boxes:
                print(f"  ! 未匹配到选项 {letter}")
                continue
            vision.click_box(boxes[0], scale)
            print(f"  ✓ 点击选项 {letter}")
            time.sleep(0.45)

    def _answer_boolean(self, shot, scale: float, answers) -> None:
        true_set = {"TRUE", "T", "A", "正确", "对", "是", "√"}
        false_set = {"FALSE", "F", "B", "错误", "错", "否", "×", "X"}
        for a in answers:
            raw = str(a).strip()
            key = raw.upper()
            if key in true_set or raw in true_set:
                tpl = config.TPL_TRUE
            elif key in false_set or raw in false_set:
                tpl = config.TPL_FALSE
            else:
                print(f"  ! 无法判定判断题答案: {raw}")
                continue
            boxes = vision.find_all(shot, config.ASSET_DIR / tpl)
            if not boxes:
                print(f"  ! 未找到判断题按钮模板 {tpl}")
                continue
            vision.click_box(boxes[0], scale)
            time.sleep(0.45)

    def _answer_fill(self, shot, scale: float, answers) -> None:
        if not answers:
            return
        for i, text in enumerate(answers):
            if i >= len(config.TPL_BLANKS):
                print("  ! 填空数量超过模板数，忽略多余答案")
                break
            tpl = config.TPL_BLANKS[i]
            boxes = vision.find_all(shot, config.ASSET_DIR / tpl)
            if not boxes:
                print(f"  ! 未找到第 {i + 1} 个填空框模板 {tpl}")
                continue
            vision.type_into(boxes[0], text, scale)
            print(f"  ✓ 填入第 {i + 1} 空: {text!r}")
            time.sleep(0.45)

    # ================= onepage：整卷单页 =================
    def _run_onepage(self) -> None:
        answered = 0
        stall = 0
        while answered < config.MAX_QUESTIONS:
            try:
                shot = vision.capture()
                scale = vision.compute_scale(shot)
                clusters = vision.build_clusters(shot, scale)
                handin = vision.find_all(shot, config.ASSET_DIR / config.TPL_HANDIN)

                if not clusters:
                    if handin:
                        print("视口内无待处理题项，且【最终提交】可见。")
                        break
                    self._advance(shot, scale, answered)
                    stall += 1
                    if stall >= 4:
                        print("连续多次未发现新题项，停止作答。")
                        break
                    time.sleep(0.8)
                    continue

                stall = 0
                # 逐题裁剪识别：以上一题 D 选项底部为上界、本题 D 底部为下界
                prev_bottom = 0
                for i, cluster in enumerate(clusters):
                    top = prev_bottom + 4
                    d_box = cluster["D"]
                    bottom = d_box[1] + d_box[3] + 4
                    qimg = shot.crop((0, int(top), shot.width, int(bottom)))
                    try:
                        data = self.solver.solve(qimg)
                    except Exception as exc:
                        print(f"  ! 第{answered + i + 1}题识别失败: {exc}")
                        prev_bottom = bottom
                        continue
                    letters = [str(a).strip().upper() for a in data.get("answers", [])]
                    print(f"  第{answered + i + 1}题 type={data.get('type')} → {letters}")
                    for letter in letters:
                        box = cluster.get(letter)
                        if not box:
                            print(f"    ! 无选项 {letter} 模板")
                            continue
                        vision.click_option(box, scale)
                        time.sleep(0.35)
                    prev_bottom = bottom
                    done_here = i + 1
                answered += done_here
                print(f"进度: {answered}/{config.MAX_QUESTIONS}")
                self._advance(shot, scale, answered)
                time.sleep(0.8)
            except pyautogui.FailSafeException:
                raise
            except KeyboardInterrupt:
                raise
            except Exception:
                traceback.print_exc()
                time.sleep(1)

        self._finish_onepage()

    def _advance(self, shot, scale: float, answered: int) -> None:
        """前进到下一批题：优先用题卡序号导航，否则向下滚动。"""
        idx = answered + 1
        if idx <= len(config.TPL_CARDS):
            boxes = vision.find_all(shot, config.ASSET_DIR / config.TPL_CARDS[idx - 1])
            if boxes:
                vision.click_box(boxes[0], scale)
                return
        vision.scroll_down(config.SCROLL_CLICKS)

    def _finish_onepage(self) -> None:
        print("\n" + "=" * 60)
        print(f" 作答结束，共完成 {config.MAX_QUESTIONS} 题中的作答流程")
        print("=" * 60)
        try:
            shot = vision.capture()
            scale = vision.compute_scale(shot)
            handin = vision.find_all(shot, config.ASSET_DIR / config.TPL_HANDIN)
            if not handin:
                print("未找到【最终提交】按钮，请自行检查并手动最终提交。")
                return
            if config.AUTO_SUBMIT:
                vision.click_box(handin[0], scale)
                print("已自动点击【最终提交】。")
                self._done()
                return
            try:
                ans = input("输入 yes 回车 = 自动点【最终提交】；直接回车 = 手动最终提交: ").strip().lower()
            except EOFError:
                ans = ""
            if ans == "yes":
                vision.click_box(handin[0], scale)
                print("已点击【最终提交】。")
            else:
                print("请自行点击【最终提交】。")
            self._done()
        except Exception:
            traceback.print_exc()

    # ---------- 结束提示 ----------
    def _done(self) -> None:
        msg = "Web Agent：全部题项已完成"
        print("\n" + "=" * 60)
        print(" " + msg)
        print("=" * 60)
        try:
            import subprocess
            import sys
            if sys.platform == "darwin":
                subprocess.run(
                    ["osascript", "-e",
                     'display notification "全部题项已完成" with title "Web Agent" sound name "Glass"'],
                    check=False,
                )
            elif sys.platform.startswith("win"):
                subprocess.run(["msg", "*", "/TIME:10", msg], check=False)
        except Exception:
            pass


if __name__ == "__main__":
    AutoSolver().run()
