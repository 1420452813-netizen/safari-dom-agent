<div align="center">

<h1>Safari DOM Agent</h1>

<b>基于 Safari DOM 直读与 LLM 并发调度的 macOS 原生自动化测试引擎</b>

<br>

[![Platform](https://img.shields.io/badge/Platform-macOS%2013%2B-000000?style=flat-square&logo=apple&logoColor=white)](https://apple.com)
[![SwiftUI](https://img.shields.io/badge/UI-SwiftUI%20%7C%20AppKit-F05138?style=flat-square&logo=swift&logoColor=white)](https://developer.apple.com/xcode/swiftui/)
[![Python](https://img.shields.io/badge/Core-Python%203.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Standalone](https://img.shields.io/badge/Runtime-Zero%20Config%20(Standalone)-10b981?style=flat-square)]()
[![License](https://img.shields.io/badge/License-MIT-3b82f6?style=flat-square)](LICENSE)

<br>

<a href="#quick-start">快速开始</a> ·
<a href="#architecture">架构</a> ·
<a href="#benchmarks">性能基准</a> ·
<a href="#faq">FAQ</a> ·
<a href="docs/ENGINEERING.md">工程文档</a>

</div>

---

## 这不是又一个「截图 + 识别」

把渲染结果降维成像素、再用 OCR 或模板匹配还原语义，是一条信息只减不增的链路：字体回退、窗口遮挡、Retina 缩放系数、懒加载时机，任何一环抖动都会变成不可复现的偏差，而风控恰恰被这类机械行为特征吸引。

本项目换了一个方向——不还原像素，直接读取渲染的**输入**：Safari 宿主环境里那棵真实存在的 DOM 树。

| 传统像素方案 | 本项目 |
| --- | --- |
| 屏幕 → 截图 → OCR/CV → 猜测节点 | `do JavaScript` → 直接取节点属性 |
| 依赖窗口在最前、无遮挡 | 无窗口上下文依赖，后台亦可读 |
| 缩放过 1px 就可能错位 | 坐标只用于最后一次物理点击 |
| 无法确认「是否真的选中了」 | 回读断言，逐题校验 `checked` |
| 需要无头浏览器换取自动化 | 复用你已登录的真实 Safari |

三条设计原则：

- **Read the source, not the shadow.** 语义来自 DOM，不来自渲染结果。
- **Prune, don't parse.** 题干提取靠结构剪枝，不靠正则去猜。
- **Assert, don't assume.** 每次写入后回读；不一致就重试一次，仍不一致则如实上报。

<br>

## 性能基准

<a id="benchmarks"></a>

同一份 25 题试卷、同一台机器、各 5 轮，取均值 ± 标准差。Baseline 为早期像素/视觉管线，Ours 为当前 DOM 管线，二者共用同一套回填与校验代码。

| 指标 | 像素基线 | 本项目 | 变化 |
| --- | ---: | ---: | ---: |
| 感知读取（秒/卷） | 6.72 ± 0.10 | **0.35 ± 0.00** | −94.8% |
| 模型推理（秒/卷） | 30.86 ± 4.74 | **10.37 ± 8.91** | −66.4% |
| 回填 + 回读（秒/卷） | 0.74 ± 0.01 | 0.78 ± 0.14 | +5.4% |
| **端到端（秒/卷）** | 38.32 ± 4.71 | **11.50 ± 8.88** | **−70.0%** |
| 输入 Token / 题 | 783.0 | **77.7** | −90.1% |
| 输出 Token / 题 | 10.0 | 22.7 | +127.0% |
| 总 Token / 题 | 793.0 | **100.4** | −87.3% |
| 回填成功率 | 100.0% | 98.4% ± 3.58 | −1.6 pp |

读数要点：

- 感知环节快了约 **19×**：一次 JS 注入替代「逐题截图 + 图像传输」。
- 端到端快约 **3.3×**，且瓶颈已从「像素搬运」转移到「模型推理」。
- 单题输入 Token 降到 **1/10**：喂给模型的是剪枝后的语义子树，不是整屏图像。
- 输出 Token 上升是预期代价——要求模型给出带判据的结构化 JSON，而非仅一个字母。
- 回填成功率略低于基线但方差极小的原因，见 [FAQ](#faq) 第 5 条。

> 数据采集脚本：`tests/benchmark_eval.py`（本项目）、`tests/benchmark_eval_legacy.py`（基线），原始结果：`tests/benchmark_results.json`。

<br>

## 架构

<a id="architecture"></a>

```
+-----------------------------------------------------------------------------+
|  Safari.app  ·  real profile  ·  real cookies  ·  real session state        |
|  document  ->  DOM tree (SPA rendered; Vue / AngularJS two-way bindings)    |
+--------------------------------------+--------------------------------------+
                                       |  AppleScript  do JavaScript
                                       |  (single injected pass, no screenshot)
                                       v
+-----------------------------------------------------------------------------+
|  DOM Spine  ·  structural extraction                                        |
|                                                                             |
|    clone(subtree)  ->  prune(noise)  ->  compact(node)  ->  JSON            |
|                                                                             |
|    keep : stem text / option label / checked / input[type] / group index    |
|    drop : layout wrappers, ad slots, hidden nodes, ~90% of markup weight    |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|  Concurrent inference fan-out  ·  N workers                                 |
|                                                                             |
|    in  : pruned subtree + strict output schema                              |
|    out : {"type": "single", "answers": ["C"], "reason": "..."}              |
|                                                                             |
|    providers : Zhipu GLM-4-Flash  |  OpenAI-compatible endpoints            |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|  Event-complete apply  +  closed loop                                       |
|                                                                             |
|    focus  ->  input  ->  change  ->  blur      what SPA listeners expect    |
|    read-after-apply assertion                  per-question verification     |
|    coordinate dead-zone guard                  physical click safety         |
+--------------------------------------+--------------------------------------+
                                       |
                        mismatch  <----+---->  match  ->  done
                            |
                            +--> one retry  ->  still mismatch  ->  report
```

四层职责边界清晰：**感知**不改页面，**推理**不碰 DOM，**执行**不做决策，**校验**只认回读结果。任一层可以单独替换——换成别的浏览器通道、别的模型、别的断言策略，其余不动。

<br>

## 快速开始

<a id="quick-start"></a>

### 方式一：直接运行（面向使用者，无需 Python）

1. 打开 [Releases](../../releases)，下载 `TronClass-macOS.zip`，解压后把 `TronClass.app` 拖进「应用程序」。
   （构建产物沿用内部代号 `TronClass`，与仓库名无关；想改名可自行调整 `build_app.sh` 与 `Info.plist`。）
2. 首次打开若被 Gatekeeper 拦下：右键 → 打开；或执行 `xattr -dr com.apple.quarantine /Applications/TronClass.app`。
3. 在 Safari 中打开目标页面并保持登录，再回到 app 点「刷新自检」，三项全绿即可使用。

> 执行通道（`DOMRunner.app`）会在首次自检时安装到 `~/Library/Application Support/TronClass/`，
> 随后系统弹出一次「DOMRunner 想要控制 Safari」——点允许，之后全自动。为什么必须这样装，见 [FAQ](#faq) 第 3 条。

### 方式二：从源码构建（面向开发者）

```bash
git clone https://github.com/<you>/safari-dom-agent.git
cd safari-dom-agent

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py        # 填入自己的 API Key，或改用 ~/.tronclass/config.json
./quick.sh go                         # 读题 → 判题 → 回填 → 回读校验（不提交）
```

一键产出可分发的 `.app`：

```bash
bash build_app.sh
# => TronClass.app / TronClass-macOS.zip
#    PyInstaller 冻结 dom_main.py -> dom_main_bin (arm64, ~30MB)
#    swiftc 编译 Launcher.swift  -> DOMLauncher
#    逐层 ad-hoc 签名（绝不用 --deep，原因见 FAQ 第 4 条）
```

CLI 分阶段调用，便于接入自己的流水线：

```bash
python dom_main.py read      # 只读：导出题干/选项/当前状态 -> questions.json
python dom_main.py solve     # 只判：并发推理 -> answers.json
python dom_main.py apply     # 只写：事件补全回填 + 回读校验
python dom_main.py submit    # 提交（不可逆，需显式调用）
```

<br>

## 适配到其它站点

读取层不写死任何站点的类名。题容器、题干容器、提交按钮、确认弹窗、按钮文案全部来自一份 profile——换站点只改配置，不动代码。

**加载顺序**（命中即用）：

1. 环境变量 `TRONCLASS_PROFILE=/path/to/profile.json`
2. `~/.tronclass/profile.json` ← 真实站点配置放这里，不进仓库
3. `profiles/example-spa.json` ← 仓库内示例，对应仿真页
4. 内置默认值（与示例等价，保证任何情况下都能启动）

**最小可用配置**：

```json
{
  "question": { "containers": ["li.q-item"] },
  "submit":   { "selectors": ["a.submit-btn"], "textMatch": [] },
  "confirm":  { "modalSelectors": [".modal"] }
}
```

未出现的字段自动回落到默认值，所以可以只写差异部分。

| 字段 | 作用 |
| --- | --- |
| `question.containers` | 题项容器选择器，按顺序尝试，取第一个有命中的 |
| `question.stemTitle` | 题干专用容器；命中则直接取文本 |
| `question.stemStrip` | 提取题干时从克隆节点剔除的选择器 |
| `submit.selectors` | 提交按钮选择器 |
| `submit.textMatch` | 按钮文本约束；空数组 = 不约束（纯选择器定位） |
| `submit.skipAncestors` | 文字兜底时排除的祖先（隐藏弹窗里的同名按钮） |
| `confirm.texts` | 确认按钮文本（完全相等匹配） |
| `confirm.modalAcceptTexts` / `modalRejectTexts` | 弹窗内确认 / 取消按钮的包含式匹配词 |
| `confirm.modalSelectors` | 弹窗容器选择器 |
| `confirm.hiddenMarkers` | 表示「已隐藏」的 class 名 |

**校准流程**：Safari 打开目标页 → 用 Web 检查器确定题容器与提交按钮 → 写 `~/.tronclass/profile.json` → `python dom_main.py read` 看题数与题型是否识别正确 → 再跑 `apply`。全程无需改 Python。

> 仓库里之所以只放一个「通用表单」示例，是有意为之：profile 里的类名与文案等价于一份站点指纹，公开它等于公开目标站点。示例页 `tests/mock_exam.html` 同样保持中立（四个字母选项、通用类名、确认弹窗），只用于验证结构而非复刻某个站点。

<br>

## 工程结构

```
.
├── dom_main.py              # 四阶段 CLI 调度：read / solve / apply / submit
├── dom_reader.py            # DOM 读取、剪枝、事件回填、回读断言、执行通道管理
├── dom_profile.py           # 站点适配层：加载 profile、注入 JS 模板
├── profiles/
│   └── example-spa.json     # 通用示例（对应 tests/mock_exam.html）；真实站点配置不入库
├── text_solver.py           # 并发推理、结构化输出解析、Token 计数
├── config.example.py        # 配置模板（真实 Key 不入库）
├── Launcher.swift           # SwiftUI 启动器（ObservableObject，零宏依赖）
├── Info.plist               # app bundle 元数据
├── build_app.sh             # 冻结 + 编译 + 逐层签名 + 打包
├── quick.sh                 # 常用流程封装
├── legacy/                  # 早期像素 / 视觉模型管线（保留作对照基线）
├── tests/
│   ├── benchmark_eval.py          # 本项目基准采集
│   ├── benchmark_eval_legacy.py   # 基线基准采集
│   ├── benchmark_results.json     # 上表原始数据
│   └── mock_exam.html             # 通用表单结构仿真页，零风险回归
└── docs/ENGINEERING.md      # 完整工程笔记：页面结构、TCC 排查、坑位实录
```

<br>

## FAQ

<a id="faq"></a>

<details>
<summary><b>1. 为什么不用 Playwright / Puppeteer / Selenium？</b></summary>

<br>

三个原因，按权重排序：

- **登录态**。目标页面在真实账号会话内，无头浏览器要重新过登录、验证码与设备指纹，成本与失败率都不可控。
- **协议特征**。CDP 驱动的浏览器带有可被识别的启动参数与运行时特征，而这里的通道直接跑在用户日常使用的 Safari 上。
- **授权模型**。macOS 的 TCC 天然把「控制另一个 app」变成一个需要用户显式同意的动作，这比注入脚本更符合最小权限原则。

代价是平台锁死在 macOS + Safari，这个取舍是有意的。
</details>

<details>
<summary><b>2. 为什么不做 OCR / 视觉模型？</b></summary>

<br>

做过，而且做过完整基线（`legacy/` 保留）。结论是像素链路有三个不可消除的成本：

- 截图 → 编码 → 传输，单题就吃掉数百 Token，整卷累计是文本方案的 8 倍；
- 缩放、遮挡、字体渲染差异会让同一道题在不同轮次得到不同结果；
- 最要命的是**无法回读**——你看不出「那个圆点是已选中，还是只是悬停样式」。

DOM 方案把这三个问题一次性消掉：读的是属性，不是观感。
</details>

<details>
<summary><b>3. 为什么执行通道必须装到用户目录，不能在 app 包内直接跑？</b></summary>

<br>

这是本项目调试耗时最长的一个坑，写出来省你两天。

把同一份 `DOMRunner.app`（cdhash 完全一致）放到不同位置启动，实测结果：

| 启动路径 | 结果 |
| --- | --- |
| `exports/DOMRunner.app`（独立目录） | 正常 |
| `/tmp/*.app`、`~/Library/Application Support/TronClass/` | 正常 |
| `TronClass.app/Contents/Resources/DOMRunner.app` | `-10004 未获得授权将 Apple 事件发送给 Safari` |

原因：嵌套在宿主 app 内的辅助应用，会被 macOS 归入**宿主的 TCC 责任主体（responsible process）**。宿主自己没有「控制 Safari」授权，系统也不会替它弹窗，于是 Apple Event 恒被拒——此时它在「系统设置 → 自动化」里根本不会出现，补 `CFBundleIdentifier`、重签名都没有用。

所以现在的做法是：包内那份只当**种子**，首次运行时用 `ditto` 复制到 `~/Library/Application Support/TronClass/`，之后一律从该独立路径启动。`ditto` 保留签名 → cdhash 不变 → 已有的授权记录继续有效。
</details>

<details>
<summary><b>4. 构建时为什么不能用 <code>codesign --deep</code>？</b></summary>

<br>

`--deep` 会递归重签嵌套的 `DOMRunner.app`，改变其 cdhash。cdhash 一变，用户此前授予的「自动化 → Safari」记录随即失配，表现为**升级一次就再也连不上 Safari，且不再弹窗**。

正确做法是逐层签：先签 `Contents/MacOS/` 下的可执行文件，再单独签外层 app，嵌套 Runner 保留出厂签名。`build_app.sh` 已按此实现，并在构建末尾打印嵌套 Runner 的 Identifier 供核对。
</details>

<details>
<summary><b>5. 回填成功率为什么不是 100%？</b></summary>

<br>

当前 98.4% ± 3.58（基线 100%）。差额来自判题环节的**主动弃权**：模型对某些题给不出可靠判定时输出空 `answers`，工具选择留空并在报告里标注，而不是猜一个填进去——这比一个错误结果更有价值。

回填动作本身的成功率是 100%：`apply` 结束后会逐题回读 `checked` 状态并与目标值比对，不一致即重试一次，仍不一致则列入失败清单，不会静默通过。
</details>

<details>
<summary><b>6. Safari 需要额外开什么设置？</b></summary>

<br>

`do JavaScript` 需要开启：**Safari → 设置 → 高级 → 勾选「显示网页开发者功能」**，然后在菜单栏 **开发 → 允许来自 Apple 事件中的 JavaScript**。

只读 DOM 不需要屏幕录制权限；只有涉及物理点击的环节才需要「辅助功能」。
</details>

<details>
<summary><b>7. 坐标禁区（dead-zone）是什么？</b></summary>

<br>

最后一公里的物理点击用的是屏幕绝对坐标，而坐标由 DOM 元素矩形 + 窗口偏移换算而来。当窗口被移动、缩放或跨越屏幕边缘时，换算结果可能落在页面之外——一旦点下去就是误触。

因此在派发鼠标事件前，会先用窗口几何信息校验目标矩形是否完整落在可视区域内，越界则改用 DOM 事件或滚动到可视区后重算，不盲目点击。
</details>

<details>
<summary><b>8. 为什么用 ad-hoc 签名而不是开发者证书？</b></summary>

<br>

项目不经过 App Store 分发，也不需要长期签名身份；ad-hoc 足以让系统接受 bundle 结构。代价是首次打开需右键 → 打开，且**每次重新构建外层的身份都会变**——这也是不重签嵌套 Runner 的原因（见第 4 条）。
</details>

<br>

## 已知边界

- 仅支持 macOS 13+ 与 Safari，不支持 Chrome / Edge。换浏览器需要替换感知层与执行通道。
- SPA 事件补全覆盖 `focus / input / change / blur`，对自定义事件派发（如 `dispatchEvent` 监听）需要按站点补充。
- 训练/评估类场景建议先用 `tests/mock_exam.html` 跑回归，避免在真实页面反复试错。
- 端侧推理尚未接入；当前依赖远程 API，离线不可用。

<br>

## 致谢

智谱 GLM-4-Flash（免费额度支撑了全部基准采集）、SwiftUI、PyInstaller。

<br>

## License

[MIT](LICENSE) © 2026 zero

<br>

## 免责声明

<a id="disclaimer"></a>

本项目定位为 **Web 自动化与无障碍辅助技术的工程样本**，用于研究以下问题：如何在真实浏览器宿主中做结构化感知、如何为现代 SPA 补全事件生命周期、如何在自动化链路中设计可验证的闭环。

使用范围严格限定为：

- 你**拥有或已获得明确授权**的页面与账号；
- 教学演示、自动化测试、可访问性研究与界面回归验证；
- 任何不违反所在机构学术规范与当地法律的环境。

作者不为下列用途提供支持，亦不承担由此产生的任何后果：规避课程评估、替代他人完成受监考的考核、绕过平台访问控制。使用者应自行确认其行为符合所在机构的学术诚信要求；因误用导致的账号处罚、成绩认定或法律纠纷，责任由使用者自负。

本项目按 MIT 协议「按原样」提供，不附带任何明示或暗示的担保。
