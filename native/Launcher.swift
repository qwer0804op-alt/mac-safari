import AppKit
import ApplicationServices
import CoreGraphics

@main
final class Launcher: NSObject, NSApplicationDelegate {
    private enum ConnectionMode: Int {
        case local, internet
    }

    private let modeControl = NSSegmentedControl(labels: ["같은 Wi-Fi", "인터넷"], trackingMode: .selectOne, target: nil, action: nil)
    private let address = NSTextField(labelWithString: "접속 주소를 준비하는 중입니다…")
    private let addressNote = NSTextField(wrappingLabelWithString: "같은 Wi-Fi에 연결한 아이폰의 Safari에서 이 주소를 열어 주세요.")
    private let pairingCode = NSTextField(labelWithString: "····")
    private let pairingNote = NSTextField(wrappingLabelWithString: "4자리 코드를 준비하는 중입니다…")
    private let permissions = NSTextField(wrappingLabelWithString: "")
    private let checkButton = NSButton(title: "화면 확인", target: nil, action: nil)
    private let copyButton = NSButton(title: "주소 복사", target: nil, action: nil)
    private let renewButton = NSButton(title: "새 코드", target: nil, action: nil)
    private let certificateButton = NSButton(title: "Safari 보안 설정…", target: nil, action: nil)
    private let connectionNote = NSTextField(wrappingLabelWithString: "")
    private let startButton = NSButton(title: "다시 시작", target: nil, action: nil)
    private var window: NSWindow!
    private var server: Process?
    private var input: FileHandle?
    private var serverOutput = Data()
    private var serverError = ""
    private var certificate: URL?
    private var trustProfile: URL?
    private var connectURL: String?
    private var codeTimer: Timer?
    private var mode = ConnectionMode.local
    private var restartAfterStop = false
    private var approvalID: String?
    private var approvalAlert: NSAlert?
    private var capturing = false

    static func main() {
        let app = NSApplication.shared
        let launcher = Launcher()
        app.delegate = launcher
        app.setActivationPolicy(.regular)
        app.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = NSMenu()
        appItem.submenu?.addItem(withTitle: "Mac 리모컨 종료", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(appItem)
        let editItem = NSMenuItem()
        editItem.submenu = NSMenu(title: "편집")
        editItem.submenu?.addItem(withTitle: "복사", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editItem.submenu?.addItem(withTitle: "전체 선택", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menu.addItem(editItem)
        NSApp.mainMenu = menu
        if CommandLine.arguments.dropFirst().contains("--internet") { mode = .internet }
        showWindow()
        updatePermissions()
        startServer()
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        updatePermissions()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        dismissApproval()
        try? sendCommand("quit")
        try? input?.close()
    }

    private func showWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 542, height: 532),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Mac 리모컨"
        window.center()

        let title = NSTextField(labelWithString: "Safari에서 Mac 연결")
        title.font = .systemFont(ofSize: 24, weight: .bold)
        modeControl.target = self
        modeControl.action = #selector(selectMode)
        modeControl.setAccessibilityLabel("연결 방식")
        address.font = .monospacedSystemFont(ofSize: 15, weight: .medium)
        address.isSelectable = true
        address.lineBreakMode = .byTruncatingMiddle
        pairingCode.font = .monospacedDigitSystemFont(ofSize: 42, weight: .semibold)
        pairingCode.textColor = .controlAccentColor
        [addressNote, pairingNote, permissions, connectionNote].forEach {
            $0.font = .systemFont(ofSize: 13)
            $0.textColor = .secondaryLabelColor
        }

        let addressRow = NSStackView(views: [address, copyButton])
        let pairingRow = NSStackView(views: [pairingCode, renewButton])
        checkButton.target = self
        checkButton.action = #selector(checkScreen)
        let permissionsRow = NSStackView(views: [
            NSButton(title: "화면 보기 권한", target: self, action: #selector(allowScreenCapture)),
            NSButton(title: "클릭·입력 권한", target: self, action: #selector(allowAccessibility)),
            checkButton
        ])
        let helpRow = NSStackView(views: [certificateButton, startButton])
        [addressRow, pairingRow, permissionsRow, helpRow].forEach {
            $0.alignment = .centerY
            $0.spacing = 12
        }

        let addressLabel = heading("아이폰 Safari 접속 주소")
        let pairingLabel = heading("4자리 페어링 코드")
        let permissionsLabel = heading("Mac 권한")

        let stack = NSStackView(views: [
            title, modeControl, addressLabel, addressRow, addressNote, pairingLabel, pairingRow, pairingNote,
            permissionsLabel, permissions, permissionsRow, connectionNote, helpRow
        ])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.setCustomSpacing(14, after: title)
        stack.setCustomSpacing(21, after: modeControl)
        stack.setCustomSpacing(20, after: addressNote)
        stack.setCustomSpacing(19, after: pairingNote)
        stack.setCustomSpacing(17, after: permissionsRow)
        stack.translatesAutoresizingMaskIntoConstraints = false

        guard let content = window.contentView else { return }
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 24),
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            addressRow.widthAnchor.constraint(lessThanOrEqualTo: stack.widthAnchor),
            addressNote.widthAnchor.constraint(lessThanOrEqualTo: stack.widthAnchor),
            pairingNote.widthAnchor.constraint(lessThanOrEqualTo: stack.widthAnchor),
            permissions.widthAnchor.constraint(lessThanOrEqualTo: stack.widthAnchor),
            connectionNote.widthAnchor.constraint(lessThanOrEqualTo: stack.widthAnchor)
        ])

        copyButton.target = self
        copyButton.action = #selector(copyAddress)
        renewButton.target = self
        renewButton.action = #selector(renewCode)
        certificateButton.target = self
        certificateButton.action = #selector(showSafariSetup)
        startButton.target = self
        startButton.action = #selector(startServer)
        updateModePresentation()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    private func heading(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: 13, weight: .semibold)
        return label
    }

    private func updateModePresentation() {
        modeControl.selectedSegment = mode.rawValue
        certificateButton.isHidden = mode == .internet
        connectionNote.stringValue = mode == .internet
            ? "Cloudflare가 화면과 조작 요청을 중계합니다. 모드를 바꾸거나 앱을 닫으면 공개 주소와 연결이 종료됩니다."
            : "Safari에서 연결이 비공개 연결 경고가 표시되면 먼저 보안 설정을 확인해 주세요."
    }

    @objc private func selectMode() {
        guard let selected = ConnectionMode(rawValue: modeControl.selectedSegment), selected != mode else { return }
        guard selected == .internet else {
            switchMode(to: selected)
            return
        }

        modeControl.selectedSegment = mode.rawValue
        let notice = NSAlert()
        notice.messageText = "인터넷에서 연결하시겠습니까?"
        notice.informativeText = "Cloudflare가 임시 공개 주소를 만들고 화면과 조작 요청을 중계합니다. 숫자 4자리를 맞힌 사람도 이 Mac에서 직접 승인해야 연결됩니다. 연결 방식을 바꾸면 기존 연결은 종료됩니다."
        notice.addButton(withTitle: "취소")
        notice.addButton(withTitle: "인터넷 주소 만들기")
        notice.beginSheetModal(for: window) { [weak self] reply in
            if reply == .alertSecondButtonReturn {
                self?.switchMode(to: .internet)
            }
        }
    }

    private func switchMode(to selected: ConnectionMode) {
        mode = selected
        updateModePresentation()
        guard server != nil else {
            startServer()
            return
        }

        restartAfterStop = true
        modeControl.isEnabled = false
        startButton.isHidden = true
        clearConnection()
        address.stringValue = "기존 연결을 종료하는 중입니다…"
        addressNote.stringValue = "새 접속 주소를 준비하고 있습니다."
        setCodeMessage("새 코드를 준비하는 중입니다…")
        do {
            try sendCommand("quit")
        } catch {
            try? input?.close()
        }
    }

    private func clearConnection() {
        dismissApproval()
        connectURL = nil
        certificate = nil
        trustProfile = nil
        copyButton.isEnabled = false
        renewButton.isEnabled = false
        certificateButton.isEnabled = false
        addressNote.toolTip = nil
    }

    @objc private func startServer() {
        guard server == nil else { return }
        startButton.isHidden = true
        clearConnection()
        address.stringValue = "접속 주소를 준비하는 중입니다…"
        addressNote.stringValue = mode == .internet
            ? "Cloudflare를 통해 공개 연결을 준비하고 있습니다."
            : "같은 Wi-Fi에 연결한 아이폰의 Safari에서 이 주소를 열어 주세요."
        setCodeMessage("4자리 코드를 준비하는 중입니다…")
        serverOutput.removeAll()
        serverError = ""

        do {
            let support = try FileManager.default.url(
                for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
            ).appendingPathComponent("Mac Remote Pairing", isDirectory: true)
            guard let script = Bundle.main.url(forResource: "server", withExtension: "py") else {
                showStartupError("앱에서 서버 파일을 찾을 수 없습니다.")
                return
            }
            guard FileManager.default.isExecutableFile(atPath: "/usr/bin/python3") else {
                showStartupError("Mac에 Python 3가 필요합니다.")
                return
            }
            let process = Process()
            let toServer = Pipe()
            let fromServer = Pipe()
            let errors = Pipe()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            let modeArguments = mode == .internet ? ["--internet"] : []
            process.arguments = ["-B", script.path, "--runtime", support.path, "--app-capture"] + modeArguments
            var environment = ProcessInfo.processInfo.environment
            environment["TMPDIR"] = FileManager.default.temporaryDirectory.path
            process.environment = environment
            process.standardInput = toServer
            process.standardOutput = fromServer
            process.standardError = errors
            fromServer.fileHandleForReading.readabilityHandler = { [weak self, weak process] handle in
                let data = handle.availableData
                if data.isEmpty { handle.readabilityHandler = nil }
                DispatchQueue.main.async {
                    guard let self, let process, self.server === process, !self.restartAfterStop else { return }
                    self.readServerOutput(data)
                }
            }
            errors.fileHandleForReading.readabilityHandler = { [weak self, weak process] handle in
                let data = handle.availableData
                if data.isEmpty { handle.readabilityHandler = nil }
                DispatchQueue.main.async {
                    guard let self, let process, self.server === process else { return }
                    self.serverError = String((self.serverError + String(decoding: data, as: UTF8.self)).suffix(350))
                }
            }
            process.terminationHandler = { [weak self] finished in
                DispatchQueue.main.async {
                    guard let self, self.server === finished else { return }
                    self.server = nil
                    try? self.input?.close()
                    self.input = nil
                    if self.restartAfterStop {
                        self.restartAfterStop = false
                        self.modeControl.isEnabled = true
                        self.startServer()
                    } else {
                        self.showStartupError(self.serverError.trimmingCharacters(in: .whitespacesAndNewlines))
                    }
                }
            }
            try process.run()
            server = process
            input = toServer.fileHandleForWriting
        } catch {
            showStartupError(error.localizedDescription)
        }
    }

    private struct ServerEvent: Decodable {
        let event: String
        let code: String?
        let urls: [String]?
        let certificate: String?
        let profile: String?
        let id: String?
        let reason: String?
        let path: String?
        let args: [String]?
        let expiresAt: Double?
    }

    private func readServerOutput(_ data: Data) {
        serverOutput.append(data)
        while let newline = serverOutput.firstIndex(of: 10) {
            let line = serverOutput.prefix(upTo: newline)
            serverOutput.removeSubrange(...newline)
            guard let event = try? JSONDecoder().decode(ServerEvent.self, from: Data(line)) else { continue }
            switch event.event {
            case "input":
                handleInput(event)
            case "capture":
                if let id = event.id, let path = event.path {
                    captureForServer(id: id, path: path)
                }
            case "ready":
                connectURL = event.urls?.first
                if mode == .internet {
                    address.stringValue = connectURL ?? "공개 주소를 가져올 수 없습니다."
                    addressNote.stringValue = "아이폰 Safari에서 주소를 열고 코드를 입력하면 Mac에 승인 요청이 표시됩니다."
                } else {
                    address.stringValue = connectURL ?? "Mac의 Wi-Fi 연결을 확인해 주세요."
                    addressNote.stringValue = connectURL == nil
                        ? "네트워크에 연결한 뒤 앱을 닫았다 다시 열어 주세요."
                        : "같은 Wi-Fi에 연결한 아이폰의 Safari에서 이 주소를 열어 주세요."
                }
                copyButton.isEnabled = connectURL != nil
                certificate = event.certificate.map { URL(fileURLWithPath: $0) }
                trustProfile = event.profile.map { URL(fileURLWithPath: $0) }
                certificateButton.isEnabled = mode == .local && trustProfile != nil && certificate != nil
                if let code = event.code { showCode(code) }
            case "code":
                dismissApproval()
                if let code = event.code { showCode(code) }
            case "paired":
                dismissApproval()
                renewButton.isEnabled = true
                setCodeMessage("아이폰을 연결했습니다. 다시 연결하려면 새 코드를 눌러 주세요.")
            case "exhausted":
                setCodeMessage("코드를 다섯 번 틀렸습니다. 새 코드를 눌러 다시 연결해 주세요.")
            case "approval":
                if let id = event.id { askForApproval(id) }
            case "approval-ended":
                if let id = event.id, id == approvalID {
                    dismissApproval()
                    renewButton.isEnabled = true
                    setCodeMessage(event.reason == "denied" ? "연결을 거절했습니다. 새 코드를 눌러 주세요." : "승인 대기가 끝났습니다. 새 코드를 눌러 주세요.")
                }
            default:
                break
            }
        }
    }

    private func showCode(_ code: String) {
        codeTimer?.invalidate()
        pairingCode.stringValue = code
        pairingNote.stringValue = "5분 안에 한 번 쓸 수 있습니다."
        renewButton.isEnabled = true
        codeTimer = Timer.scheduledTimer(withTimeInterval: 300, repeats: false) { [weak self] _ in
            self?.setCodeMessage("코드가 만료됐습니다. 새 코드를 눌러 주세요.")
        }
    }

    private func setCodeMessage(_ message: String) {
        codeTimer?.invalidate()
        pairingCode.stringValue = "····"
        pairingNote.stringValue = message
    }

    private func askForApproval(_ id: String) {
        guard mode == .internet, approvalID == nil, !id.isEmpty,
              id.range(of: "[^A-Za-z0-9_-]", options: .regularExpression) == nil else { return }

        approvalID = id
        renewButton.isEnabled = false
        setCodeMessage("이 Mac에서 연결 승인을 기다리고 있습니다.")

        let alert = NSAlert()
        alert.messageText = "인터넷 원격 제어를 허용하시겠습니까?"
        alert.informativeText = "누군가 올바른 페어링 코드를 입력했습니다. 방금 연결하신 기기가 맞으면 허용해 주세요. 허용하면 그 기기에서 Mac 화면을 보고 클릭과 입력을 보낼 수 있습니다."
        let denyButton = alert.addButton(withTitle: "거절")
        denyButton.keyEquivalent = "\u{1b}"
        alert.addButton(withTitle: "연결 허용").keyEquivalent = ""
        approvalAlert = alert
        NSApp.requestUserAttention(.informationalRequest)
        alert.beginSheetModal(for: window) { [weak self, weak alert] response in
            guard let self, let alert, self.approvalID == id, self.approvalAlert === alert else { return }
            self.approvalAlert = nil
            do {
                let command = response == .alertSecondButtonReturn ? "approve" : "deny"
                try self.sendCommand("\(command) \(id)")
            } catch {
                self.dismissApproval()
                self.renewButton.isEnabled = true
                self.setCodeMessage("응답을 보내지 못했습니다. 새 코드를 눌러 다시 연결해 주세요.")
            }
        }
    }

    private func dismissApproval() {
        approvalID = nil
        guard let alert = approvalAlert else { return }
        approvalAlert = nil
        if alert.window.sheetParent === window {
            window.endSheet(alert.window, returnCode: .abort)
        }
    }

    private func showStartupError(_ detail: String) {
        clearConnection()
        if mode == .internet {
            address.stringValue = "공개 접속 주소를 만들 수 없습니다."
            addressNote.stringValue = detail.isEmpty ? "cloudflared와 인터넷 연결을 확인한 뒤 다시 시작을 눌러 주세요." : detail
        } else {
            address.stringValue = "접속 서버를 시작할 수 없습니다."
            addressNote.stringValue = "다시 시작을 눌러 주세요."
        }
        addressNote.toolTip = detail.isEmpty ? nil : detail
        startButton.isHidden = false
        setCodeMessage("서버가 켜지면 페어링 코드가 표시됩니다.")
    }

    @objc private func copyAddress() {
        guard let connectURL else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(connectURL, forType: .string)
    }

    @objc private func renewCode() {
        do {
            try sendCommand("pair")
        } catch {
            setCodeMessage("앱을 닫았다 다시 열어 주세요.")
        }
    }

    private func sendCommand(_ command: String) throws {
        guard let input else { throw CocoaError(.fileWriteUnknown) }
        try input.write(contentsOf: Data("\(command)\n".utf8))
    }

    @objc private func showSafariSetup() {
        guard let trustProfile, let certificate else { return }
        let setup = NSAlert()
        setup.messageText = "Safari에서 안전하게 연결하기"
        setup.informativeText = """
        아이폰에서는 이 앱이 만든 프로파일을 AirDrop으로 받아 주세요. 설정 → 일반 → VPN 및 기기 관리에서 설치한 뒤, 일반 → 정보 → 인증서 신뢰 설정에서 Mac Remote Pairing CA의 전체 신뢰를 켜 주세요.

        맥 Safari에서는 인증서를 키체인 접근에 추가하고 인증서의 SSL 신뢰를 켜 주세요.

        설정이 끝나면 이 앱에 표시된 HTTPS 주소를 열어 주세요.
        """
        setup.addButton(withTitle: "아이폰 프로파일 보기")
        setup.addButton(withTitle: "Mac 인증서 보기")
        setup.addButton(withTitle: "닫기")
        setup.beginSheetModal(for: window) { response in
            switch response {
            case .alertFirstButtonReturn:
                NSWorkspace.shared.activateFileViewerSelecting([trustProfile])
            case .alertSecondButtonReturn:
                NSWorkspace.shared.activateFileViewerSelecting([certificate])
            default:
                break
            }
        }
    }

    private func updatePermissions() {
        let screen = CGPreflightScreenCaptureAccess() ? "허용됨" : "허용 필요"
        let control = hasRemoteInputPermission() ? "허용됨" : "허용 필요"
        permissions.stringValue = "화면 기록 \(screen) · 손쉬운 사용 \(control)"
    }

    private func replyToServer(_ operation: String, id: String, error: String?) {
        let response: [String: Any] = [operation: id, "error": error as Any? ?? NSNull()]
        if let data = try? JSONSerialization.data(withJSONObject: response) {
            try? sendCommand(String(decoding: data, as: UTF8.self))
        }
    }

    private func handleInput(_ event: ServerEvent) {
        guard let id = event.id, UUID(uuidString: id) != nil else { return }
        guard let args = event.args, let expiresAt = event.expiresAt,
              Date().timeIntervalSince1970 < expiresAt else {
            replyToServer("input", id: id, error: "입력 요청이 만료됐습니다. 다시 눌러 주세요.")
            return
        }
        do {
            try performRemoteInput(args)
            replyToServer("input", id: id, error: nil)
        } catch {
            replyToServer("input", id: id, error: error.localizedDescription)
        }
    }

    private func captureForServer(id: String, path: String) {
        guard UUID(uuidString: id) != nil, let process = server else { return }
        let file = URL(fileURLWithPath: path).resolvingSymlinksInPath()
        let directory = file.deletingLastPathComponent()
        let temp = FileManager.default.temporaryDirectory.resolvingSymlinksInPath()
        let reply: (String?) -> Void = { [weak self] error in
            guard let self, self.server === process, !self.restartAfterStop else { return }
            self.replyToServer("capture", id: id, error: error)
        }
        guard file.lastPathComponent == "screen.jpg",
              directory.lastPathComponent.hasPrefix("macremote-screen-"),
              directory.deletingLastPathComponent().path == temp.path else {
            reply("화면 파일을 준비할 수 없습니다. Mac 리모컨을 다시 열어 주세요.")
            return
        }
        capture(to: file, completion: reply)
    }

    private func capture(to file: URL, completion: @escaping (String?) -> Void) {
        guard !capturing else {
            completion("이전 화면을 가져오는 중입니다. 잠시 뒤 다시 시도해 주세요.")
            return
        }
        capturing = true
        Task { @MainActor in
            var failure: String?
            do {
                try await captureScreen(to: file)
            } catch {
                failure = error.localizedDescription
            }
            capturing = false
            completion(failure)
        }
    }

    @objc private func checkScreen() {
        let manager = FileManager.default
        let directory = manager.temporaryDirectory.appendingPathComponent("macremote-check-" + UUID().uuidString)
        do {
            try manager.createDirectory(at: directory, withIntermediateDirectories: false,
                                        attributes: [.posixPermissions: 0o700])
        } catch {
            permissions.stringValue = "화면을 확인할 임시 폴더를 만들 수 없습니다."
            return
        }
        checkButton.isEnabled = false
        permissions.stringValue = "Mac 화면을 가져오는 중입니다…"
        capture(to: directory.appendingPathComponent("screen.jpg")) { [weak self] error in
            try? manager.removeItem(at: directory)
            self?.checkButton.isEnabled = true
            self?.permissions.stringValue = error ?? "이 Mac의 화면을 캡처했습니다."
        }
    }

    @objc private func allowScreenCapture() {
        if !CGPreflightScreenCaptureAccess() {
            _ = CGRequestScreenCaptureAccess()
        }
        openPrivacySettings("Privacy_ScreenCapture")
        updatePermissions()
    }

    @objc private func allowAccessibility() {
        if !hasRemoteInputPermission() {
            _ = requestRemoteInputPermission()
        }
        openPrivacySettings("Privacy_Accessibility")
        updatePermissions()
    }

    private func openPrivacySettings(_ panel: String) {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(panel)") else { return }
        NSWorkspace.shared.open(url)
    }
}
