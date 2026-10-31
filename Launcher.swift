// Launcher.swift
// 编译命令: swiftc Launcher.swift -o DOMLauncher -O
import SwiftUI
import AppKit

// MARK: - 配置与状态管理模型
class AppViewModel: ObservableObject {
    @Published var apiKey: String = ""
    @Published var isKeySaved: Bool = false
    @Published var showKey: Bool = false
    
    @Published var runMode: Int = 0 // 0: 极速整卷, 1: 逐题复核(不提交)
    @Published var autoSubmit: Bool = false
    @Published var isRunning: Bool = false
    @Published var currentStep: String = ""
    @Published var logText: String = "准备就绪。\n"

    // 状态自检项
    @Published var safariReady: Bool = false
    @Published var runnerReady: Bool = false
    @Published var binaryReady: Bool = false

    private let configPath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".tronclass/config.json")

    init() {
        loadConfig()
        checkEnvironment()
    }

    // 1. 本地 ~/.tronclass/config.json 读写
    func loadConfig() {
        guard let data = try? Data(contentsOf: configPath),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let key = json["api_key"] as? String, !key.isEmpty else {
            isKeySaved = false
            return
        }
        self.apiKey = key
        self.isKeySaved = true
    }

    func saveConfig() {
        let dir = configPath.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        
        let payload: [String: Any] = [
            "api_key": apiKey.trimmingCharacters(in: .whitespacesAndNewlines),
            "provider": "zhipu",
            "model": "glm-4-flash",
            "concurrency": 8
        ]
        if let data = try? JSONSerialization.data(withJSONObject: payload, options: .prettyPrinted) {
            try? data.write(to: configPath)
            isKeySaved = !apiKey.isEmpty
            checkEnvironment()
        }
    }

    // 2. 环境自检逻辑
    func checkEnvironment() {
        // 检查 Safari 是否运行
        let apps = NSWorkspace.shared.runningApplications
        self.safariReady = apps.contains { $0.bundleIdentifier == "com.apple.Safari" }

        // 检查后端二进制与 DOMRunner
        let bundleURL = Bundle.main.bundleURL
        let macosDir = bundleURL.appendingPathComponent("Contents/MacOS")
        let resDir = bundleURL.appendingPathComponent("Contents/Resources")

        let binPath = macosDir.appendingPathComponent("dom_main_bin").path
        self.binaryReady = FileManager.default.fileExists(atPath: binPath) || FileManager.default.fileExists(atPath: "./dom_main_bin")

        let runnerPath = resDir.appendingPathComponent("DOMRunner.app").path
        // Runner 必须从独立（非嵌套）路径启动：嵌在宿主 app 的 Resources 里时，
        // macOS 会把它算作宿主的 TCC 责任主体 → 「控制 Safari」恒被拒。
        // 因此包内那份只当种子，首次自检时安装到用户目录，再从那里调用。
        let installDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/TronClass")
        let installedRunner = installDir.appendingPathComponent("DOMRunner.app")
        if !FileManager.default.fileExists(atPath: installedRunner.path) {
            if FileManager.default.fileExists(atPath: runnerPath) {
                try? FileManager.default.createDirectory(at: installDir, withIntermediateDirectories: true)
                try? FileManager.default.copyItem(at: URL(fileURLWithPath: runnerPath), to: installedRunner)
                runQuiet("/usr/bin/xattr", ["-cr", installedRunner.path])
            }
        }
        self.runnerReady = FileManager.default.fileExists(atPath: installedRunner.path)
            || FileManager.default.fileExists(atPath: "./exports/DOMRunner.app")
    }

    /// 静默执行外部命令（忽略输出与失败）
    private func runQuiet(_ path: String, _ args: [String]) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
        p.waitUntilExit()
    }

    // 3. 后端二进制绝对路径解析
    var backendExecutablePath: String {
        let inAppPath = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/dom_main_bin").path
        if FileManager.default.fileExists(atPath: inAppPath) {
            return inAppPath
        }
        return "./dom_main_bin"
    }

    // 4. 非交互式纯 DOM 流水线 (read -> solve -> apply)
    func startPipeline() {
        guard isKeySaved && !apiKey.isEmpty else {
            logText += "⚠️ 请先填写并保存 API Key！\n"
            return
        }
        isRunning = true
        logText = "=== 启动执行引擎 ===\n"

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            self.updateStep("步骤 1/3: 正在读取页面题项 (read)...")
            guard self.runSubprocess(arg: "read") else { self.finish(false); return }

            self.updateStep("步骤 2/3: 正在并发判题 (solve)...")
            guard self.runSubprocess(arg: "solve") else { self.finish(false); return }

            self.updateStep("步骤 3/3: 正在精准回填选项 (apply)...")
            guard self.runSubprocess(arg: "apply") else { self.finish(false); return }

            if self.runMode == 0 && self.autoSubmit {
                self.updateStep("正在执行提交 (submit)...")
                _ = self.runSubprocess(arg: "submit")
            }

            self.finish(true)
        }
    }

    private func updateStep(_ text: String) {
        DispatchQueue.main.async {
            self.currentStep = text
            self.logText += "\n▶ \(text)\n"
        }
    }

    private func finish(_ success: Bool) {
        DispatchQueue.main.async {
            self.isRunning = false
            self.currentStep = ""
            self.logText += success ? "\n🎉 全流程执行完成！请在 Safari 中确认结果。\n" : "\n❌ 流程中断，请排查上方日志。\n"
        }
    }

    private func runSubprocess(arg: String) -> Bool {
        let task = Process()
        let outPipe = Pipe()
        let errPipe = Pipe()

        // 斩断 stdin，彻底避免 input() 造成的管道永久锁死
        task.standardInput = FileHandle.nullDevice
        task.standardOutput = outPipe
        task.standardError = errPipe

        // 合并继承环境并注入 PYTHONUNBUFFERED=1：
        // 让 Python  stdout/stderr 不经缓冲区、即时冲刷，实现启动器内的实时流式日志
        // （保留 PATH 等继承变量，避免丢失系统环境）。TC-SPEC-2026-V3 §Launcher 要求。
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        task.environment = env

        task.executableURL = URL(fileURLWithPath: backendExecutablePath)
        task.arguments = [arg]

        let outH = outPipe.fileHandleForReading
        let errH = errPipe.fileHandleForReading

        outH.readabilityHandler = { [weak self] handle in
            let d = handle.availableData
            if let s = String(data: d, encoding: .utf8), !s.isEmpty {
                DispatchQueue.main.async { self?.logText += s }
            }
        }
        errH.readabilityHandler = { [weak self] handle in
            let d = handle.availableData
            if let s = String(data: d, encoding: .utf8), !s.isEmpty {
                DispatchQueue.main.async { self?.logText += "⚠️ " + s }
            }
        }

        do {
            try task.run()
            task.waitUntilExit()
            outH.readabilityHandler = nil
            errH.readabilityHandler = nil
            return task.terminationStatus == 0
        } catch {
            DispatchQueue.main.async { self.logText += "系统调度异常: \(error.localizedDescription)\n" }
            outH.readabilityHandler = nil
            errH.readabilityHandler = nil
            return false
        }
    }
}

// MARK: - 主界面视图
struct ContentView: View {
    @ObservedObject var vm: AppViewModel

    var body: some View {
        ZStack {
            VisualEffectView(material: .sidebar, blendingMode: .behindWindow)
                .ignoresSafeArea()

            VStack(spacing: 14) {
                // 1. 顶部 Brand
                HStack(spacing: 12) {
                    Image(systemName: "bolt.shield.fill")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 34, height: 34)
                        .foregroundStyle(.linearGradient(colors: [.blue, .cyan], startPoint: .topLeading, endPoint: .bottomTrailing))

                    VStack(alignment: .leading, spacing: 2) {
                        Text("TronClass Auto Solver")
                            .font(.system(size: 16, weight: .bold, design: .rounded))
                        Text("全自包含免配置版 · DOM 树直读与并发判定")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                    Spacer()
                }
                .padding(.top, 16)

                // 2. API Key 设置卡片 (支持本地存取)
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("智谱 GLM-4-Flash API Key")
                            .font(.system(size: 11, weight: .semibold))
                        Spacer()
                        if vm.isKeySaved {
                            Text("已持久化存储").font(.system(size: 10)).foregroundColor(.green)
                        }
                    }

                    HStack(spacing: 8) {
                        if vm.showKey {
                            TextField("在此粘贴 API Key...", text: $vm.apiKey)
                                .textFieldStyle(.roundedBorder)
                                .font(.system(size: 11, design: .monospaced))
                        } else {
                            SecureField("在此粘贴 API Key...", text: $vm.apiKey)
                                .textFieldStyle(.roundedBorder)
                                .font(.system(size: 11, design: .monospaced))
                        }

                        Button(action: { vm.showKey.toggle() }) {
                            Image(systemName: vm.showKey ? "eye.slash" : "eye")
                                .foregroundColor(.secondary)
                        }.buttonStyle(.borderless)

                        Button("保存") {
                            vm.saveConfig()
                        }
                        .font(.system(size: 11, weight: .medium))
                        .controlSize(.small)
                    }
                }
                .padding(10)
                .background(Color(NSColor.controlBackgroundColor).opacity(0.6))
                .clipShape(RoundedRectangle(cornerRadius: 8))

                // 3. 自检三行 (Safari, DOMRunner, 内置引擎)
                VStack(spacing: 6) {
                    StatusRow(icon: "safari", title: "Safari 受测页面检测", status: vm.safariReady ? "已在运行" : "请先启动 Safari", isOk: vm.safariReady)
                    Divider().opacity(0.3)
                    StatusRow(icon: "lock.shield", title: "DOMRunner 自动化通道", status: vm.runnerReady ? "通道就绪" : "组件缺失", isOk: vm.runnerReady)
                    Divider().opacity(0.3)
                    StatusRow(icon: "cpu", title: "内置推理执行引擎", status: vm.binaryReady ? "内置就绪 (Mach-O)" : "二进制缺失", isOk: vm.binaryReady)
                }
                .padding(10)
                .background(Color(NSColor.controlBackgroundColor).opacity(0.6))
                .clipShape(RoundedRectangle(cornerRadius: 8))

                // 4. 双模式选择与自动提交控制
                VStack(spacing: 8) {
                    Picker("", selection: $vm.runMode) {
                        Text("🚀 极速整卷回填 (全自动)").tag(0)
                        Text("🔍 逐题复核模式 (仅勾选不提交)").tag(1)
                    }
                    .pickerStyle(.segmented)

                    if vm.runMode == 0 {
                        Toggle(isOn: $vm.autoSubmit) {
                            Text("回填完成后自动点击【提交】按钮 (不可逆)")
                                .font(.system(size: 11))
                        }
                        .toggleStyle(.checkbox)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

                // 5. 主执行大胶囊按钮
                Button(action: { vm.startPipeline() }) {
                    HStack(spacing: 8) {
                        if vm.isRunning {
                            ProgressView().controlSize(.small).colorInvert()
                            Text(vm.currentStep).font(.system(size: 12, weight: .semibold))
                        } else {
                            Image(systemName: "play.fill")
                            Text("一键开始执行流水线").font(.system(size: 13, weight: .semibold))
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 36)
                    .background(vm.isRunning ? Color.gray.opacity(0.5) : Color.blue)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .disabled(vm.isRunning)

                // 6. 折叠式终端实时日志
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("运行日志输出")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(.secondary)
                        Spacer()
                        Button("刷新自检") { vm.checkEnvironment() }
                            .font(.system(size: 10))
                            .buttonStyle(.borderless)
                    }

                    ScrollViewReader { proxy in
                        ScrollView {
                            Text(vm.logText)
                                .font(.system(size: 10, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .foregroundColor(.primary.opacity(0.85))
                                .padding(8)
                                .id("LOG_BOTTOM")
                        }
                        .frame(height: 120)
                        .background(Color(NSColor.textBackgroundColor).opacity(0.4))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                        .onChange(of: vm.logText) { _, _ in
                            proxy.scrollTo("LOG_BOTTOM", anchor: .bottom)
                        }
                    }
                }
                Spacer(minLength: 2)
            }
            .padding(.horizontal, 18)
        }
        .frame(width: 440, height: 550)
    }
}

// 辅助子组件
struct StatusRow: View {
    let icon: String
    let title: String
    let status: String
    let isOk: Bool

    var body: some View {
        HStack {
            Image(systemName: icon).frame(width: 16).foregroundColor(.secondary)
            Text(title).font(.system(size: 11))
            Spacer()
            Circle().fill(isOk ? Color.green : Color.orange).frame(width: 5, height: 5)
            Text(status).font(.system(size: 10, weight: .medium)).foregroundColor(.secondary)
        }
    }
}

struct VisualEffectView: NSViewRepresentable {
    let material: NSVisualEffectView.Material
    let blendingMode: NSVisualEffectView.BlendingMode

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = material
        view.blendingMode = blendingMode
        view.state = .active
        return view
    }
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let vm = AppViewModel()

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 440, height: 550),
            styleMask: [.titled, .closable, .miniaturizable, .fullSizeContentView],
            backing: .buffered, defer: false
        )
        window.center()
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.contentView = NSHostingView(rootView: ContentView(vm: vm))
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
