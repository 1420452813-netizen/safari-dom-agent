"""
DOM 读写器：通过 Safari 的 AppleScript `do JavaScript` 直接操作受测页面。

为什么用 DOM 而不是截图：
  - 不受系统级浮窗遮挡、窗口布局漂移、Retina 缩放影响
  - 能直接读出题干/选项文字 → 交给文本模型判题（不需要视觉模型）
  - 能直接读取单选框 checked 状态 → 逐题校验，不会像像素方案那样"以为答了其实没答"

执行方式（两种）：
  1) exec_js()       —— 直接 osascript 执行（需要"控制 Safari"自动化权限）
  2) export_script() —— 导出桌面 .applescript 供用户双击运行
     （受管沙箱环境拿不到该权限，这是常规路径；控制方=脚本编辑器，授权一次即可）

页面结构假设（已实测）：
  整卷单页，N 题 × 4 选项；radio 按 DOM 顺序排列，每 4 个一组；
  name 属性各不相同，因此**按索引分组**（q*4+j），不能按 name 分组。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import config
from dom_profile import PROFILE, PROFILE_SOURCE, inject

# ---------------- 读题：题干 + 选项 + 当前选中状态 ----------------
JS_READ = inject(r"""
(function(){
  // ASCII-safe JSON：把非 ASCII 字符转成 \uXXXX。
  // 必须这么做：AppleScript 的 write 用系统主编码（本机是 GBK）落盘，
  // Python 侧按 UTF-8 读会乱码。输出纯 ASCII 后整条链路对编码免疫。
  function J(o){
    return JSON.stringify(o).replace(/[\u007F-\uFFFF]/g, function(c){
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
  // P 由 Python 侧注入（dom_profile.py）：站点相关的类名/文案全部来自 profile，
  // 本文件不再含任何目标站点的字面量。
  var P = __PROFILE__;

  // 题容器：按 profile 顺序尝试，取第一个有命中的；再按内部 input 类型细分题型。
  var CONTAINER_SELS = P.question.containers;
  var conts = [];
  for (var s=0; s<CONTAINER_SELS.length; s++){
    var c = document.querySelectorAll(CONTAINER_SELS[s]);
    if (c.length){ conts = c; break; }
  }

  // 题型判定：依据题容器内的 input 类型（不依赖具体 class，更稳健）
  function detectType(li){
    var cb = li.querySelectorAll('input[type=checkbox]').length;
    var rd = li.querySelectorAll('input[type=radio]').length;
    var tx = li.querySelectorAll('input[type=text], textarea, [contenteditable="true"]').length;
    if (tx > 0 && rd === 0 && cb === 0) return 'fill';     // 填空题
    if (cb > 0) return 'multiple';                          // 多选题
    if (rd === 2) return 'boolean';                         // 判断题（2 选 1）
    if (rd >= 1) return 'single';                           // 单选题
    if (tx > 0) return 'fill';
    return 'unknown';
  }

  function labelOf(el){
    var l = el.closest('label');
    if (!l) l = el.parentElement;
    return l ? (l.innerText || '').replace(/\s+/g,' ').trim() : '';
  }

  // 题干：优先抓题干专用容器；否则克隆节点剔除非题干元素（选项列表/解析/题卡锚点），兜底取前 240 字
  function stemOf(cont) {
    if (!cont) return '';
    // 1. 优先尝试直接抓取题干专用容器
    var titleEl = null;
    for (var t=0; t<P.question.stemTitle.length && !titleEl; t++){
      titleEl = cont.querySelector(P.question.stemTitle[t]);
    }
    if (titleEl) return (titleEl.innerText || '').replace(/\s+/g, ' ').trim();
    // 2. 深度克隆节点，移除非题干元素（选项列表、解析、题卡锚点）
    var clone = cont.cloneNode(true);
    var removeSels = P.question.stemStrip;
    for (var i = 0; i < removeSels.length; i++) {
      var nodes = clone.querySelectorAll(removeSels[i]);
      for (var j = 0; j < nodes.length; j++) {
        if (nodes[j].parentNode) nodes[j].parentNode.removeChild(nodes[j]);
      }
    }
    var stem = (clone.innerText || '').replace(/\s+/g, ' ').trim();
    return stem ? stem : (cont.innerText || '').slice(0, 240);
  }

  var out = [];
  for (var q=0; q<conts.length; q++){
    var li = conts[q];
    var type = detectType(li);
    var question = '', options = [], current = null;
    if (type === 'fill'){
      var inp = li.querySelector('input[type=text], textarea, [contenteditable="true"]');
      question = (li.innerText || '').replace(/\s+/g,' ').trim().slice(0, 300);
      current = inp ? (inp.value || inp.innerText || '') : '';
    } else {
      var inputs = (type === 'multiple')
        ? li.querySelectorAll('input[type=checkbox]')
        : li.querySelectorAll('input[type=radio]');
      var texts = [];
      for (var j=0; j<inputs.length; j++) texts.push(labelOf(inputs[j]));
      question = stemOf(li);
      options = texts;
      if (type === 'multiple'){
        current = [];
        for (var k=0; k<inputs.length; k++) if (inputs[k].checked) current.push(String.fromCharCode(65 + k));
      } else {
        var sel = -1;
        for (var m=0; m<inputs.length; m++) if (inputs[m].checked){ sel = m; break; }
        current = sel >= 0 ? String.fromCharCode(65 + sel) : null;
      }
    }
    out.push({index: q, type: type, question: question, options: options, current: current});
  }

  // 【提交】按钮定位：profile 选择器优先，文字兜底（DOM click 对 <a> 无效，需真实鼠标）
  // 文本约束来自 P.submit.textMatch（空数组 = 不约束，适用于纯选择器定位的站点）。
  function submitTextOk(s){
    var TM = P.submit.textMatch;
    if (!TM.length) return true;
    for (var m=0; m<TM.length; m++){
      if (s === TM[m]) return true;
      if (s.indexOf(TM[m]) >= 0 && s.length <= 6) return true;
    }
    return false;
  }
  var submitInfo = null;
  var SUBSEL = P.submit.selectors;
  for (var i=0; i<SUBSEL.length && !submitInfo; i++){
    var els = document.querySelectorAll(SUBSEL[i]);
    for (var j=0; j<els.length; j++){
      var t = (els[j].innerText || '').replace(/\s+/g,'');
      if (t && !submitTextOk(t)) continue;
      var r = els[j].getBoundingClientRect();
      if (r.width > 0 && r.height > 0){
        submitInfo = {tag: els[j].tagName, cx: r.left + r.width/2, cy: r.top + r.height/2,
                      windowX: window.screenX, windowY: window.screenY,
                      chromeH: window.outerHeight - window.innerHeight};
        break;
      }
    }
  }
  if (!submitInfo){
    var all = document.querySelectorAll('a, button, span, div');
    for (var n=0; n<all.length; n++){
      var tt = (all[n].innerText || '').replace(/\s+/g,'');
      if (!tt || !submitTextOk(tt)) continue;
      var rr = all[n].getBoundingClientRect();
      if (rr.width > 0 && rr.height > 0){
        submitInfo = {tag: all[n].tagName, cx: rr.left + rr.width/2, cy: rr.top + rr.height/2,
                      windowX: window.screenX, windowY: window.screenY,
                      chromeH: window.outerHeight - window.innerHeight};
        break;
      }
    }
  }

  return J({questionCount: out.length, questions: out, submit: submitInfo});
})()
""", PROFILE)

# ---------------- 点选：按判定结果数组点击（__ANSWERS__ 会被替换）----------------
JS_APPLY = inject(r"""
(function(){
  function J(o){
    return JSON.stringify(o).replace(/[\u007F-\uFFFF]/g, function(c){
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
  var P = __PROFILE__;
  // ANSWERS 形如：
  //   [{type:'single', answer:'C'},
  //    {type:'multiple', answer:['A','B']},
  //    {type:'boolean', answer:'A'},
  //    {type:'fill', answer:'文本'}]
  // 兼容旧格式：纯字符串视为 single，纯数组视为 multiple。
  var ANSWERS = __ANSWERS__;

  var CONTAINER_SELS = P.question.containers;
  var conts = [];
  for (var s=0; s<CONTAINER_SELS.length; s++){
    var c = document.querySelectorAll(CONTAINER_SELS[s]);
    if (c.length){ conts = c; break; }
  }

  var result = [];
  for (var q=0; q<conts.length; q++){
    var li = conts[q];
    var a = ANSWERS[q];
    if (a == null){ result.push({q: q+1, ok:false, reason:'NO_ANSWER'}); continue; }
    var type, answer;
    if (typeof a === 'string'){ type = 'single'; answer = a; }
    else if (Array.isArray(a)){ type = 'multiple'; answer = a; }
    else { type = a.type || 'single'; answer = (a.answer == null) ? '' : a.answer; }

    // —— 填空题：设置值并触发 input/change 事件（AngularJS 需要 $setViewValue）——
    if (type === 'fill'){
      var inp = li.querySelector('input[type=text], textarea, [contenteditable="true"]');
      if (!inp){ result.push({q:q+1, type:'fill', ok:false, reason:'NO_INPUT'}); continue; }
      var val = (answer == null) ? '' : String(answer);
      try {
        // 填空题赋值事件增强：focus → 设值（兼容 AngularJS ng-model）→ 完整触发输入生命周期
        inp.focus();
        var proto = (inp.tagName === 'TEXTAREA') ? window.HTMLTextAreaElement.prototype
                  : (inp.getAttribute && inp.getAttribute('contenteditable') === 'true') ? null
                  : window.HTMLInputElement.prototype;
        if (proto) {
          var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(inp, val);
        } else {
          inp.innerText = val;   // contenteditable
        }
        // 完整触发输入生命周期事件
        ['input', 'change', 'blur'].forEach(function(evtName) {
          inp.dispatchEvent(new Event(evtName, { bubbles: true, cancelable: true }));
        });
        result.push({q:q+1, type:'fill', ok:true, value: val});
      } catch(e){ result.push({q:q+1, type:'fill', ok:false, reason:String(e)}); }
      continue;
    }

    // —— 选择题（单选/多选/判断）—— 按索引 A/B/C/D 映射
    var inputs = (type === 'multiple') ? li.querySelectorAll('input[type=checkbox]')
                                       : li.querySelectorAll('input[type=radio]');
    if (!inputs.length){ result.push({q:q+1, type:type, ok:false, reason:'NO_INPUTS'}); continue; }
    var wantArr = Array.isArray(answer) ? answer : [answer];
    var okAll = true;
    for (var i=0; i<inputs.length; i++){
      var L = String.fromCharCode(65 + i);
      var should = false;
      for (var w=0; w<wantArr.length; w++){ if (String(wantArr[w]).toUpperCase() === L){ should = true; break; } }
      var isChecked = !!inputs[i].checked;
      if (should && !isChecked) inputs[i].click();
      else if (!should && isChecked) inputs[i].click();
      if (should !== !!inputs[i].checked) okAll = false;
    }
    result.push({q:q+1, type:type, ok:okAll, answer: answer});
  }
  return J({applied: result});
})()
""", PROFILE)


def _run_osascript(js_path: Path) -> subprocess.CompletedProcess:
    scpt = (
        'set js to (read (POSIX file "%s") as «class utf8»)\n'
        'tell application "Safari"\n'
        '\treturn do JavaScript js in document 1\n'
        'end tell'
    ) % str(js_path)
    return subprocess.run(["osascript", "-e", scpt], capture_output=True, text=True)


# ---------------- 执行通道 ----------------
# 独立 Runner.app：作为独立控制方，授权一次后即可用命令行反复触发。
#
# ⚠️ 关键约束（实测结论）：Runner 必须从一个「独立、非嵌套」的路径启动。
#    若直接运行 <宿主>.app/Contents/Resources/DOMRunner.app，macOS 会把该辅助应用
#    归入宿主 app 的 TCC 责任主体（responsible process），宿主没有「控制 Safari」
#    授权、也不会为它弹窗，于是 Apple Event 恒被拒（-10004）。
#    同一份二进制（cdhash 不变）放到任意独立路径（/tmp、~/Library/...）都能正常执行。
#    → 因此包内那份只作「种子」：首次运行复制到用户目录，再从那里启动。
#
# 定位优先级：
#   1) 环境变量 TRONCLASS_RUNNER_APP（存在且路径有效时优先）
#   2) 已安装位置：~/Library/Application Support/TronClass/DOMRunner.app
#   3) 源码开发态：<项目根>/exports/DOMRunner.app（本身就在独立路径，可直接用）
#   4) 冻结包内置 Resources/DOMRunner.app → 复制到 (2) 后使用，绝不原地启动
INSTALL_DIR = Path.home() / "Library" / "Application Support" / "TronClass"
INSTALLED_RUNNER = INSTALL_DIR / "DOMRunner.app"


def _bundled_runner_seed() -> Path | None:
    """返回可作为种子的 Runner 包（包内 Resources 或工作目录 exports）。"""
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        bundled = exe_path.parent.parent / "Resources" / "DOMRunner.app"
        if bundled.exists():
            return bundled
    cwd_runner = Path.cwd() / "exports" / "DOMRunner.app"
    if cwd_runner.exists():
        return cwd_runner
    return None


def install_runner(force: bool = False) -> Path | None:
    """把种子 Runner 安装到独立的用户目录（保留原签名，cdhash 不变 → 继承已授权状态）。"""
    if INSTALLED_RUNNER.exists() and not force:
        return INSTALLED_RUNNER
    seed = _bundled_runner_seed()
    if seed is None:
        return None
    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        if INSTALLED_RUNNER.exists():
            shutil.rmtree(INSTALLED_RUNNER, ignore_errors=True)
        # ditto 保留签名与权限；失败则退回 copytree
        proc = subprocess.run(
            ["ditto", str(seed), str(INSTALLED_RUNNER)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not INSTALLED_RUNNER.exists():
            shutil.copytree(seed, INSTALLED_RUNNER, symlinks=True)
        # 清掉 quarantine，避免首次被 Gatekeeper 拦
        subprocess.run(["xattr", "-cr", str(INSTALLED_RUNNER)], capture_output=True)
        return INSTALLED_RUNNER
    except OSError:
        return None


def _get_runner_app() -> Path:
    env_runner = os.getenv("TRONCLASS_RUNNER_APP")
    if env_runner and Path(env_runner).exists():
        return Path(env_runner)
    if INSTALLED_RUNNER.exists():
        return INSTALLED_RUNNER
    dev_runner = Path(__file__).resolve().parent / "exports" / "DOMRunner.app"
    if dev_runner.exists() and not getattr(sys, "frozen", False):
        return dev_runner
    installed = install_runner()
    return installed if installed is not None else (dev_runner if dev_runner.exists() else INSTALLED_RUNNER)


RUNNER_APP = _get_runner_app()
TASK_JS = Path("/tmp/tronclass_task.js")
RESULT_JSON = Path("/tmp/tronclass_result.json")


def _decode(raw: bytes) -> str:
    """结果文件解码：AppleScript write 用系统主编码（本机实测为 GBK），逐层兜底。"""
    for enc in ("utf-8", "gb18030", "mac_roman", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _exec_via_app(js_code: str, timeout: float = 15.0) -> str:
    """通过独立 Runner.app 执行（推荐通道：授权一次后可全自动反复触发）。"""
    import time

    global RUNNER_APP
    if not RUNNER_APP.exists():
        fixed = install_runner()
        if fixed is not None:
            RUNNER_APP = fixed

    if RESULT_JSON.exists():
        try:
            RESULT_JSON.unlink()
        except OSError:
            pass
    TASK_JS.write_text(js_code, encoding="utf-8")
    subprocess.run(["open", "-a", str(RUNNER_APP)], check=False)

    waited = 0.0
    while waited < timeout:
        time.sleep(0.3)
        waited += 0.3
        if not RESULT_JSON.exists():
            continue
        txt = _decode(RESULT_JSON.read_bytes()).strip()
        if not txt:
            # 文件已落盘但内容为空：JS 执行成功、只是没有返回值（如 openConfirm()）。
            # 复查一次防止读到写入中途的空态，仍为空则按「返回空串」处理，
            # 不要继续傻等到 15s 超时后误报「未授权」。
            time.sleep(0.2)
            txt = _decode(RESULT_JSON.read_bytes()).strip()
            if not txt:
                return ""
        try:
            data = json.loads(txt)
            if isinstance(data, dict) and "error" in data:
                err = str(data["error"])
                if "-10004" in err:
                    raise RuntimeError("NOT_AUTHORIZED")
                raise RuntimeError(f"Runner.app 执行失败: {err[:200]}")
        except json.JSONDecodeError:
            pass
        return txt
    raise RuntimeError(
        "NOT_AUTHORIZED 或超时：Runner.app 未在 15 秒内产出结果。\n"
        "  首次运行会弹出「DOMRunner 想要控制 Safari」——请在桌面点「允许」。"
    )


def _exec_via_osascript(js_code: str) -> str:
    """直接 osascript 执行（需当前环境已被授权控制 Safari）。"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js_code)
        js_path = Path(f.name)
    try:
        proc = _run_osascript(js_path)
    finally:
        try:
            js_path.unlink()
        except OSError:
            pass

    if proc.returncode != 0 or "execution error" in (proc.stderr or ""):
        err = (proc.stderr or proc.stdout or "").strip()
        if "-10004" in err:
            raise RuntimeError(
                "权限不足：当前环境未被授权「控制 Safari」(-10004)。\n"
                "  · 系统设置 → 隐私与安全性 → 自动化 → 给运行环境勾选 Safari\n"
                "  · 或改用导出脚本方式（见 export_script），双击运行即可经由授权通道解决该限制"
            )
        raise RuntimeError(f"执行 JS 失败: {err[:300]}")
    return proc.stdout.strip()


def exec_js(js_code: str) -> str:
    """
    执行 JS。优先走独立 Runner.app（授权一次后可全自动），未授权则回退直连 osascript。
    """
    if RUNNER_APP.exists():
        # 短时间连续调用时（read→apply→submit 一口气跑），Runner 实例可能还没退干净，
        # 偶发读到陈旧结果而误判为「未授权」。先在调用通道内重试一次，再考虑回退。
        for _ in range(2):
            try:
                return _exec_via_app(js_code)
            except RuntimeError as exc:
                if "NOT_AUTHORIZED" not in str(exc):
                    raise
                time.sleep(0.5)
            # 两次都未授权 → 回退直连（沙箱内通常同样失败，届时由调用方导出双击脚本）
    return _exec_via_osascript(js_code)


# ---------------- 确认弹窗：查找 / 点击（替代截图找色块）----------------
JS_CONFIRM = inject(r"""
(function(){
  function J(o){
    return JSON.stringify(o).replace(/[\u007F-\uFFFF]/g, function(c){
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
  var P = __PROFILE__;
  var KEYS = P.confirm.texts;
  var out = [];
  var els = document.querySelectorAll('button, a, span, div, input[type=button], input[type=submit]');
  for (var i=0; i<els.length; i++){
    var el = els[i];
    var t = ((el.innerText || el.value || el.textContent || '') + '').replace(/\s+/g, '');
    if (!t || t.length > 8) continue;
    if (KEYS.indexOf(t) < 0) continue;
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    // 是否在弹窗里（祖先有 fixed 定位且可见 → 视为模态层）
    var isModal = false, p = el;
    for (var k=0; k<8 && p; k++){
      var cs = window.getComputedStyle(p);
      if (cs && cs.position === 'fixed'){ isModal = true; break; }
      p = p.parentElement;
    }
    out.push({
      tag: el.tagName, text: t, isModal: isModal,
      cx: r.left + r.width/2, cy: r.top + r.height/2,
      area: r.width * r.height,
      windowX: window.screenX, windowY: window.screenY,
      chromeH: window.outerHeight - window.innerHeight
    });
  }
  return J({count: out.length, candidates: out});
})()
""", PROFILE)

JS_CLICK_CONFIRM = inject(r"""
(function(){
  function J(o){
    return JSON.stringify(o).replace(/[\u007F-\uFFFF]/g, function(c){
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
  var P = __PROFILE__;
  var KEYS = P.confirm.texts;
  var els = document.querySelectorAll('button, a, span, div, input[type=button], input[type=submit]');
  var best = null, bestScore = -1;
  for (var i=0; i<els.length; i++){
    var el = els[i];
    var t = ((el.innerText || el.value || el.textContent || '') + '').replace(/\s+/g, '');
    if (!t || t.length > 8) continue;
    if (KEYS.indexOf(t) < 0) continue;
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    var isModal = false, p = el;
    for (var k=0; k<8 && p; k++){
      var cs = window.getComputedStyle(p);
      if (cs && cs.position === 'fixed'){ isModal = true; break; }
      p = p.parentElement;
    }
    var score = (isModal ? 1000 : 0) + r.width * r.height / 1000;
    if (score > bestScore){ bestScore = score; best = el; }
  }
  if (!best) return J({clicked: false, reason: 'NOT_FOUND'});
  best.click();
  return J({clicked: true, text: (best.innerText || best.value || '').replace(/\s+/g,'')});
})()
""", PROFILE)

# 页面状态：用于提交后判断是否已离开受测页面（不截图）
JS_STATE = inject(r"""
(function(){
  function J(o){
    return JSON.stringify(o).replace(/[\u007F-\uFFFF]/g, function(c){
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
  var P = __PROFILE__;
  // 有文本约束时按文本判定「提交按钮是否还在页面上」；无约束的站点返回 false，
  // 由调用方改用 URL / 题数量变化判断。
  function isSubmitText(t){
    var TM = P.submit.textMatch;
    for (var m=0; m<TM.length; m++){ if (t === TM[m]) return true; }
    return false;
  }
  var rs = document.querySelectorAll('input[type=radio]');
  var hasSubmit = false;
  if (P.submit.textMatch.length){
    // 有文本约束：按文本判定
    var all = document.querySelectorAll('a, button, span, div');
    for (var i=0; i<all.length; i++){
      var t = (all[i].innerText || '').replace(/\s+/g,'');
      if (isSubmitText(t)){
        var r = all[i].getBoundingClientRect();
        if (r.width > 0 && r.height > 0){ hasSubmit = true; break; }
      }
    }
  } else {
    // 无文本约束：退回 profile 选择器，判断按钮是否仍可见
    for (var s=0; s<P.submit.selectors.length && !hasSubmit; s++){
      var cand = document.querySelectorAll(P.submit.selectors[s]);
      for (var c=0; c<cand.length; c++){
        var rr = cand[c].getBoundingClientRect();
        if (rr.width > 0 && rr.height > 0){ hasSubmit = true; break; }
      }
    }
  }
  // 以下两项仅用于仿真页(mocks)自检：提交<a>的真实 mousedown 计数 + 是否已确认；
  // 真实页面没有这两个全局变量，typeof 守卫保证返回 null 而不报错。
  var sc = (typeof window.__submitClicks !== 'undefined') ? window.__submitClicks : null;
  var cf = (typeof window.__confirmed !== 'undefined') ? window.__confirmed : null;
  return J({url: location.href, radios: rs.length, hasSubmit: hasSubmit,
            submitClicks: sc, confirmed: cf,
            head: (document.body ? (document.body.innerText||'').replace(/\s+/g,' ') : '').slice(0, 200)});
})()
""", PROFILE)


def _submit_finder_js() -> str:
    """生成「提交」按钮定位 JS（选择器优先，文字兜底）。"""
    return inject(r"""
(function(){
  function J(o){
    return JSON.stringify(o).replace(/[\u007F-\uFFFF]/g, function(c){
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
  function info(el, how){
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return null;
    var cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return null;
    return {how: how, tag: el.tagName, cls: el.className || '', id: el.id || '',
            cx: r.left + r.width/2, cy: r.top + r.height/2,
            w: r.width, h: r.height,
            windowX: window.screenX, windowY: window.screenY,
            chromeH: window.outerHeight - window.innerHeight,
            scrollY: window.scrollY};
  }
  var P = __PROFILE__;
  // 文本约束：textMatch 为空 → 不约束；否则「完全相等」或「包含且长度≤6」都算命中
  function submitTextOk(s){
    var TM = P.submit.textMatch;
    if (!TM.length) return true;
    for (var m=0; m<TM.length; m++){
      if (s === TM[m]) return true;
      if (s.indexOf(TM[m]) >= 0 && s.length <= 6) return true;
    }
    return false;
  }
  // ① 源码级选择器（按 profile 顺序，首个命中即返回）
  var SELS = P.submit.selectors;
  for (var i=0; i<SELS.length; i++){
    var els = document.querySelectorAll(SELS[i]);
    for (var j=0; j<els.length; j++){
      var t = (els[j].innerText || '').replace(/\s+/g, '');
      if (t && !submitTextOk(t)) continue;
      var v = info(els[j], 'selector:' + SELS[i]);
      if (v) return J(v);
    }
  }
  // ② 文字兜底：排除隐藏弹窗内的同名元素（skipAncestors 由 profile 提供）
  var all = document.querySelectorAll('a, button, span, div');
  for (var k=0; k<all.length; k++){
    var el = all[k];
    var skip = false;
    for (var a=0; a<P.submit.skipAncestors.length; a++){
      if (el.closest(P.submit.skipAncestors[a])){ skip = true; break; }
    }
    if (skip) continue;
    var tx = (el.innerText || '').replace(/\s+/g, '');
    if (!tx || !submitTextOk(tx)) continue;
    var v2 = info(el, 'text');
    if (v2) return J(v2);
  }
  return J(null);
})()
""", PROFILE)


JS_SUBMIT = _submit_finder_js()


def find_submit() -> dict:
    """实时定位【提交】按钮（源码选择器优先）。返回 None 表示未找到。"""
    return json.loads(exec_js(JS_SUBMIT))


def read_state() -> dict:
    """读取页面状态（是否已提交/离开受测页面），全程 DOM，不截图。"""
    return json.loads(exec_js(JS_STATE))


# ---------------- 确认弹窗（源码级：按 profile 的 modalSelectors 定位）----------------
_MODAL_PROBE = inject(r"""
  var P = __PROFILE__;
  function visibleModals(){
    var out = [], sels = P.confirm.modalSelectors;
    for (var s=0; s<sels.length; s++){
      var els = document.querySelectorAll(sels[s]);
      for (var i=0; i<els.length; i++){
        var el = els[i];
        var cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
        var hidden = false, HM = P.confirm.hiddenMarkers;
        for (var h=0; h<HM.length; h++){
          if (el.className && String(el.className).indexOf(HM[h]) >= 0){ hidden = true; break; }
        }
        if (hidden) continue;
        var r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        out.push(el);
      }
    }
    return out;
  }
  var GO = P.confirm.modalAcceptTexts;
  var NO = P.confirm.modalRejectTexts;
  function pickBtn(modal){
    // 宽泛匹配 modal 内所有可点元素：部分站点确认按钮由 UI 框架生成，形如
    // <a class="xxx-btn xxx-btn--primary">；旧的 a.button / a.btn 选择器匹配不到这种
    // class 命名，会出现「弹窗找到了但按钮被跳过」→ 弹窗滞留 → 被迫再次点提交。
    var btns = modal.querySelectorAll('button, a, input[type=button], input[type=submit]');
    var best = null, bestScore = -1;
    for (var i=0; i<btns.length; i++){
      var b = btns[i];
      var t = (b.innerText || b.value || '').replace(/\s+/g, '');
      if (!t) continue;
      var bad = false;
      for (var n=0; n<NO.length; n++) if (t.indexOf(NO[n]) >= 0) bad = true;
      if (bad) continue;
      var r = b.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      var hit = false;
      for (var g=0; g<GO.length; g++) if (t.indexOf(GO[g]) >= 0) hit = true;
      var score = (hit ? 100 : 0) + (String(b.className||'').indexOf('primary') >= 0 ? 50 : 0)
                + (String(b.className||'').indexOf('green') >= 0 ? 30 : 0) + r.width * r.height / 1000;
      if (score > bestScore){ bestScore = score; best = b; }
    }
    return best;
  }
""")

JS_MODAL = r"""
(function(){
  function J(o){
    return JSON.stringify(o).replace(/[\u007F-\uFFFF]/g, function(c){
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
""" + _MODAL_PROBE + r"""
  var ms = visibleModals();
  if (!ms.length) return J({count: 0});
  var m = ms[0], b = pickBtn(m);
  if (!b) return J({count: ms.length, modal: String(m.className), btn: null});
  var r = b.getBoundingClientRect();
  return J({count: ms.length, modal: String(m.className),
            body: (m.innerText||'').replace(/\s+/g,' ').slice(0,150),
            btn: {tag: b.tagName, cls: String(b.className||''), text: (b.innerText||b.value||'').replace(/\s+/g,''),
                  cx: r.left + r.width/2, cy: r.top + r.height/2,
                  windowX: window.screenX, windowY: window.screenY,
                  chromeH: window.outerHeight - window.innerHeight}});
})()
"""

JS_MODAL_CLICK = r"""
(function(){
  function J(o){
    return JSON.stringify(o).replace(/[\u007F-\uFFFF]/g, function(c){
      return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  }
""" + _MODAL_PROBE + r"""
  var ms = visibleModals();
  if (!ms.length) return J({clicked: false, reason: 'NO_MODAL'});
  var m = ms[0], b = pickBtn(m);
  if (!b) return J({clicked: false, reason: 'NO_BUTTON', modal: String(m.className)});
  var txt = (b.innerText || b.value || '').replace(/\s+/g, '');
  b.click();
  return J({clicked: true, text: txt, modal: String(m.className)});
})()
"""


def find_modal() -> dict:
    """查找当前可见的确认弹窗及其确认按钮。"""
    return json.loads(exec_js(JS_MODAL))


def click_modal() -> dict:
    """点掉当前可见弹窗的确认按钮（DOM click）。"""
    return json.loads(exec_js(JS_MODAL_CLICK))


def find_confirm() -> dict:
    """查找确认弹窗中的确认按钮候选。"""
    return json.loads(exec_js(JS_CONFIRM))


def click_confirm() -> dict:
    """用 DOM click 点掉确认弹窗（<button> 有效；<a> 类需退化为真实鼠标）。"""
    return json.loads(exec_js(JS_CLICK_CONFIRM))


def export_dir() -> Path:
    # 环境变量优先：分发态由 GUI 启动器指定独立工作目录，避免污染桌面
    env = os.environ.get("TRONCLASS_EXPORT_DIR")
    if env:
        p = Path(env).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return p
    return Path(config.DOM_EXPORT_DIR).expanduser()


def export_script(js_code: str, script_name: str, result_name: str) -> Path:
    """
    导出一个可双击运行的 .applescript（连同 JS 文件）到桌面。
    运行后会把 JS 的返回值写入桌面的 result_name。
    返回 .applescript 路径。
    """
    d = export_dir()
    d.mkdir(parents=True, exist_ok=True)

    js_path = d / config.DOM_JS_NAME
    js_path.write_text(js_code, encoding="utf-8")

    result_path = d / result_name
    scpt = (
        'set js to (read (POSIX file "%s") as «class utf8»)\n'
        'tell application "Safari"\n'
        '\tset res to do JavaScript js in document 1\n'
        'end tell\n'
        'set f to POSIX file "%s"\n'
        'try\n\tclose access f\nend try\n'
        'set fp to open for access f with write permission\n'
        'write res to fp\n'
        'close access fp\n'
        'display dialog "执行完成，结果已写入 %s"'
    ) % (str(js_path), str(result_path), result_name)

    script_path = d / script_name
    # .applescript 是纯文本，双击默认用「脚本编辑器」打开运行
    script_path.write_text(scpt, encoding="utf-8")
    return script_path


# ---------------- 高层封装 ----------------
def read_questions(prefer_export: bool = True) -> dict:
    """
    读取全部题项。默认优先直接执行；若无权限则导出双击脚本并抛出提示。
    返回 dict: {questionCount, questions:[{index,type,question,options,current}], submit:{...}}
      type ∈ single/multiple/boolean/fill；current 为当前作答（字母/字母数组/文本）
    """
    if not prefer_export:
        return json.loads(exec_js(JS_READ))
    try:
        return json.loads(exec_js(JS_READ))
    except RuntimeError as exc:
        if "-10004" not in str(exc):
            raise
        path = export_script(JS_READ, config.DOM_SCRIPT_NAME, config.DOM_QUESTIONS_FILE)
        raise RuntimeError(
            "无「控制 Safari」权限，已为你导出双击脚本：\n"
            f"  1) 双击桌面 {config.DOM_SCRIPT_NAME}\n"
            f"  2) 弹窗点「允许」（仅首次）\n"
            f"  3) 结果会写入桌面 {config.DOM_QUESTIONS_FILE}\n"
            f"  4) 然后运行: python dom_main.py solve\n"
            f"（脚本路径: {path}）"
        )


def apply_answers(answers: list, prefer_export: bool = True) -> dict:
    """
    按判定结果点选/填值。answers 形如 [{type, answer}, ...]：
      - single/boolean: answer 为单字母（如 'C'）
      - multiple: answer 为字母数组（如 ['A','B']）
      - fill: answer 为文本字符串
    兼容：纯字符串按单选、纯数组按多选处理。
    """
    js = JS_APPLY.replace("__ANSWERS__", json.dumps(answers, ensure_ascii=False))
    if not prefer_export:
        return json.loads(exec_js(js))
    try:
        return json.loads(exec_js(js))
    except RuntimeError as exc:
        if "-10004" not in str(exc):
            raise
        path = export_script(js, config.DOM_APPLY_SCRIPT_NAME, "apply_result.json")
        raise RuntimeError(
            "无「控制 Safari」权限，已导出回填脚本：\n"
            f"  双击桌面 {config.DOM_APPLY_SCRIPT_NAME} 即可按判定结果点选\n"
            f"  结果写入桌面 apply_result.json（脚本路径: {path}）"
        )
