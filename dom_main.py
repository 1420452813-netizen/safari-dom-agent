"""
DOM 方案主入口（推荐路径，替代截图方案）。

流程：读题 → 文本模型判题 → 点选 → 校验 → 最终提交

用法：
  python dom_main.py read      # 读题：能直连就直连，否则导出双击脚本
  python dom_main.py solve     # 用文本模型判题，生成 answers.json
  python dom_main.py apply     # 按 answers.json 点选（无权限则导出双击脚本）
  python dom_main.py submit    # 真实鼠标点击【最终提交】（DOM click 对 <a> 无效）
  python dom_main.py all       # 串联全流程

说明：
  · 无「控制 Safari」权限时，read/apply 会自动导出桌面 .applescript，
    双击运行（首次弹窗点允许）即可，结果写回桌面 json，再继续下一步。
  · 最终提交必须用真实鼠标事件：DOM 的 element.click() 对最终提交 <a> 不生效（实测）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import config
import dom_reader
from text_solver import TextSolver

LETTERS = "ABCD"


def _export_dir() -> Path:
    return dom_reader.export_dir()


def _load_questions() -> dict:
    path = _export_dir() / config.DOM_QUESTIONS_FILE
    if not path.exists():
        raise SystemExit(
            f"未找到 {path}。请先运行 `python dom_main.py read`，"
            f"并按提示双击桌面脚本生成该文件。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_read() -> None:
    print("== 读取题项 ==")
    try:
        data = dom_reader.read_questions(prefer_export=True)
        path = _export_dir() / config.DOM_QUESTIONS_FILE
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        qs = data.get("questions", [])
        if not qs:
            print("  ⚠️ 当前 Safari 页面未找到任何有效题项！请确认页面已进入受测状态。")
            sys.exit(1)
        print(f"✅ 读到 {len(qs)} 题，已写入 {path}")
        TYPE_LABEL = {"single": "单选", "multiple": "多选", "boolean": "判断", "fill": "填空", "unknown": "未知"}
        for q in qs:
            t = q.get("type", "single")
            cur = q.get("current")
            if t == "multiple":
                sel_txt = "".join(cur) if cur else "未答"
            elif t == "fill":
                sel_txt = (cur or "未答")[:18]
            else:
                sel_txt = cur if cur else "未答"
            print(f"  Q{q['index']+1} [{TYPE_LABEL.get(t, t)}]: 当前={sel_txt} | {q['question'][:34]}...")
    except RuntimeError as exc:
        print(f"⏸ 需要你操作一步：\n{exc}")


def cmd_solve() -> None:
    print("== 文本模型判题 ==")
    data = _load_questions()
    questions = data.get("questions", [])
    solver = TextSolver()
    answers = solver.judge_many(questions)

    path = _export_dir() / config.DOM_ANSWERS_FILE
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n判定结果: {answers}")
    print(f"✅ 已写入 {path}")

    # 本次 token 用量（GLM-4-Flash 免费，但占用每日免费额度，控制台可查累计）
    u = getattr(solver, "usage", None)
    if u:
        print(f"💡 本次 token 消耗：输入 {u['prompt_tokens']} / 输出 {u['completion_tokens']} "
              f"/ 合计 {u['total_tokens']}（模型 {solver.model}，免费额度以智谱控制台为准）")


def cmd_apply() -> None:
    print("== 按判定结果回填 ==")
    path = _export_dir() / config.DOM_ANSWERS_FILE
    if not path.exists():
        raise SystemExit("未找到 answers.json，先运行 `python dom_main.py solve`")
    answers = json.loads(path.read_text(encoding="utf-8"))
    # 兼容旧格式：纯字符串→单选，纯数组→多选；新格式为 [{type, answer}]
    norm = []
    for a in answers:
        if isinstance(a, dict):
            norm.append(a)
        elif isinstance(a, list):
            norm.append({"type": "multiple", "answer": a})
        else:
            norm.append({"type": "single", "answer": a})
    try:
        res = dom_reader.apply_answers(norm, prefer_export=True)
        applied = res.get("applied", [])
        ok = sum(1 for r in applied if r.get("ok"))
        print(f"✅ 点选完成 {ok}/{len(norm)}")
        for r in applied:
            if r.get("type") == "fill":
                print(f"  Q{r['q']} [填空]: {'✓' if r.get('ok') else '✗ ' + str(r.get('reason', ''))} → {str(r.get('value', ''))[:28]}")
            else:
                ans = r.get("answer")
                ans_txt = "".join(ans) if isinstance(ans, list) else ans
                print(f"  Q{r['q']} [{r.get('type')}]: {ans_txt} → {'✓' if r.get('ok') else '✗ ' + str(r.get('reason', ''))}")
    except RuntimeError as exc:
        print(f"⏸ 需要你操作一步：\n{exc}")


def _click_at(x: float, y: float, duration: float = 0.2) -> None:
    """真实鼠标点击（最终提交 <a> 类元素只认真实事件，DOM click 实测无效）。

    合并原 _dom_click / _click_mouse 两个冗余点击函数（TC-SPEC-2026-V3）。
    """
    import pyautogui

    pyautogui.moveTo(x, y, duration=duration)
    pyautogui.mouseDown()
    time.sleep(0.08)
    pyautogui.mouseUp()


def _ensure_safari_front(retries: int = 5) -> bool:
    """
    确保 Safari 处于最前台。
    实测教训：Safari 不在前台时，真实鼠标点击会被系统吞掉（页面收不到任何 mousedown），
    表现为"点了没反应"。因此点击前必须先激活并用 CGWindowList 校验。
    """
    import subprocess

    for i in range(retries):
        subprocess.run(["open", "-a", "Safari"], check=False)
        time.sleep(1.0)
        try:
            import Quartz

            wins = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
            )
            top = next((w for w in wins if w.get("kCGWindowLayer") == 0), None)
            owner = str(top.get("kCGWindowOwnerName")) if top else ""
            if "Safari" in owner:
                return True
        except Exception as exc:
            print(f"  前台校验失败: {exc}")
            return True  # 无法校验时不再阻塞，按已激活处理
    print("  ⚠ Safari 仍未置于前台，鼠标点击可能被吞")
    return False


def _confirm_dialog(submit_target=None, rounds: int = 4, wait_each: float = 8.0) -> bool:
    """
    按「源码级弹窗结构」点掉确认流程：
      提交后会依次弹出平台自己的提示层（如网络校验类「我知道了」）
      → #submit-exam-confirmation-popup（真正确认），所以要循环点，不能只点一次。
    每轮：找可见弹窗 → DOM click 主按钮 → 校验消失；不生效则退化为真实鼠标点击。
    全程不截图。

    submit_target: (lx, ly) 提交按钮的屏幕坐标。任何弹窗「确认按钮」一旦落在该点 40px
      范围内，一律禁止真实点击 —— 这是防止「最终提交完成后鼠标又点回提交按钮」的硬防护。
    """
    # 禁区判定：确认按钮坐标与提交按钮重合 → 视为危险，禁止真实鼠标点击
    def _in_forbidden(bx, by):
        if submit_target is None:
            return False
        return abs(bx - submit_target[0]) < 40 and abs(by - submit_target[1]) < 40

    for rnd in range(1, rounds + 1):
        waited = 0.0
        modal = None
        while waited < wait_each:
            time.sleep(0.5)
            waited += 0.5
            try:
                modal = dom_reader.find_modal()
            except Exception as exc:
                print(f"  查询弹窗失败: {exc}")
                return False
            if modal.get("count") and modal.get("btn"):
                break
        if not (modal and modal.get("count") and modal.get("btn")):
            if rnd == 1:
                print("  未出现确认弹窗（可能已直接提交，或按钮需手动确认）")
            else:
                print("  ✅ 弹窗流程结束")
            return rnd > 1

        btn = modal["btn"]
        # —— 硬防护：确认按钮坐标与提交按钮重合 → 跳过真实点击，仅尝试 DOM click ——
        bx = btn["windowX"] + btn["cx"]
        by = btn["windowY"] + btn["chromeH"] + btn["cy"]
        if _in_forbidden(bx, by):
            print("  ⚠ 弹窗按钮坐标与【最终提交】按钮重合，跳过真实点击（防二次提交）")
            try:
                dom_reader.click_modal()
            except Exception:
                pass
            continue

        print(f"  第{rnd}轮弹窗[{modal.get('modal','')[:40]}] 按钮「{btn['text']}」"
              f" 内容: {modal.get('body','')[:50]}")

        try:
            res = dom_reader.click_modal()
        except Exception as exc:
            print(f"  DOM 点击失败: {exc}")
            res = {"clicked": False}

        time.sleep(1.2)
        try:
            gone = dom_reader.find_modal().get("count", 0) == 0
        except Exception:
            gone = False
        if gone:
            continue

        # DOM click 未生效（如 <a>）→ 退化真实鼠标（但仍在禁区外才允许）
        cx = btn["windowX"] + btn["cx"]
        cy = btn["windowY"] + btn["chromeH"] + btn["cy"]
        if _in_forbidden(cx, cy):
            print("  ⚠ 退化真实点击目标紧邻【最终提交】按钮，放弃点击（防二次提交）")
            return False
        print(f"  DOM click 未生效，改用真实鼠标 → ({cx:.0f}, {cy:.0f})")
        _click_at(cx, cy)
        time.sleep(1.5)
        try:
            if dom_reader.find_modal().get("count", 0) == 0:
                continue
        except Exception:
            pass
        print("  ⚠ 弹窗仍在，请手动查看 Safari")
        return False
    print(f"  ⚠ 已点满 {rounds} 轮，请手动确认 Safari 状态")
    return False


def cmd_submit() -> None:
    """
    最终提交：用 DOM 读到的按钮位置换算屏幕坐标，再用真实鼠标点击，并自动点掉确认弹窗。

    · 位置来源：questions.json 中 DOM 的 getBoundingClientRect() + 窗口偏移（**不截图**，
      不受上下层窗口遮挡、布局漂移影响）
    · 确认弹窗同样走 DOM 定位（不再依赖 teal 色块识别 —— 该色同时是「已选选项」高亮色，
      历史上误点过）
    · 最终提交 <a> 只认真实鼠标事件（DOM click 实测无效），故用 mouseDown/mouseUp
    · 仅在 DOM 定位信息缺失时才退回颜色检测
    · 防护：① 预检"是否已提交"，已提交绝不重复点击；② 确认弹窗若落在最终提交坐标禁区内，
      绝不真实点击，杜绝"最终提交完成后鼠标又点回提交按钮"
    """
    print("== 最终提交 ==")
    try:
        import pyautogui
    except ImportError as exc:
        raise SystemExit(f"最终提交需要 pyautogui: {exc}")

    pyautogui.FAILSAFE = config.FAILSAFE

    # 0) 预检：若页面已不在受测页面（已提交 / 已离开），绝不重复点击最终提交
    try:
        pre = dom_reader.read_state()
        if not pre.get("hasSubmit"):
            print("  ✅ 页面已无【最终提交】按钮（可能已提交 / 已离开受测页面），无需重复操作。")
            return
    except Exception as exc:
        print(f"  （预检跳过：{exc}）")

    # 1) 先把 Safari 拉到前台（否则真实鼠标点击会被系统吞掉，实测踩过坑）
    _ensure_safari_front()

    # 2) 优先源码级实时定位（选择器优先于文字扫描，见 dom_reader.find_submit）
    target = None
    try:
        si = dom_reader.find_submit()
        if si and si.get("cx") is not None:
            lx = si["windowX"] + si["cx"]
            ly = si["windowY"] + si["chromeH"] + si["cy"]
            target = (lx, ly)
            print(f"  源码定位【最终提交】{si.get('how')} → 屏幕 ({lx:.0f}, {ly:.0f})（未使用截图）")
    except SystemExit:
        pass
    except Exception as exc:
        print(f"  实时定位失败: {exc}")

    # 3) 退回 read 阶段缓存的位置
    if target is None:
        try:
            data = _load_questions()
            si = data.get("submit")
            if si and si.get("cx") is not None:
                lx = si["windowX"] + si["cx"]
                ly = si["windowY"] + si["chromeH"] + si["cy"]
                target = (lx, ly)
                print(f"  缓存定位【最终提交】→ 屏幕坐标 ({lx:.0f}, {ly:.0f})（未使用截图）")
        except SystemExit:
            pass
        except Exception as exc:
            print(f"  读取 DOM 定位信息失败: {exc}")

    if target is None:
        print("❌ 未能定位【最终提交】按钮（DOM 与缓存均未找到），请手动点击。")
        return

    subprocess.run(["open", "-a", "Safari"], check=False)  # 再兜一次，防被其他窗口遮挡
    time.sleep(0.8)

    lx, ly = target
    print(f"  → 真实点击【最终提交】({lx:.0f}, {ly:.0f})（全过程中仅此一次）")
    _click_at(lx, ly)
    time.sleep(1.6)

    # 4) 确认弹窗（传入最终提交坐标为禁区，防止误点回提交按钮）
    _confirm_dialog(submit_target=target)

    # 5) 验证：重新读取页面状态（不截图）
    time.sleep(1.5)
    try:
        st = dom_reader.read_state()
        sc = st.get("submitClicks")
        print(f"  页面状态: url=…{st['url'][-24:]} | radios={st['radios']} | 仍显示最终提交={st['hasSubmit']}")
        if sc is not None:
            print(f"  [自检] 提交按钮被真实点击次数={sc}（仿真页可见；应=1，>1 说明有重复点击）")
        if not st["hasSubmit"] or st["radios"] == 0:
            print("  ✅ 提交按钮已消失（大概率提交成功）")
        else:
            print("  ⚠ 仍在受测页面，请检查 Safari（本次未重复点击最终提交）")
        print(f"  页面摘要: {st['head'][:100]}")
    except Exception as exc:
        print(f"  状态回读失败: {exc}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    cmds = {
        "read": cmd_read,
        "solve": cmd_solve,
        "apply": cmd_apply,
        "submit": cmd_submit,
    }
    if cmd not in cmds:
        print(__doc__)
        raise SystemExit(1)
    cmds[cmd]()


if __name__ == "__main__":
    main()
