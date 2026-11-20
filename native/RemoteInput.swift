import CoreGraphics
import Foundation

enum RemoteInputFailure: LocalizedError {
    case invalidArguments
    case permissionRequired
    case invalidPoint
    case invalidScroll
    case unknownKey
    case invalidText
    case unsupportedCharacter
    case eventCreationFailed

    var errorDescription: String? {
        switch self {
        case .invalidArguments:
            return "입력 요청 형식이 올바르지 않아."
        case .permissionRequired:
            return "Mac 리모컨에서 클릭·입력 권한을 허용해."
        case .invalidPoint:
            return "Mac 화면에 있는 위치를 다시 눌러."
        case .invalidScroll:
            return "스크롤 값이 올바르지 않아."
        case .unknownKey:
            return "지원하지 않는 키야."
        case .invalidText:
            return "보낼 텍스트의 길이를 확인해."
        case .unsupportedCharacter:
            return "지원하지 않는 글자가 있어."
        case .eventCreationFailed:
            return "Mac에 입력을 보낼 수 없어. 다시 시도해."
        }
    }
}

func hasRemoteInputPermission() -> Bool {
    CGPreflightPostEventAccess()
}

func requestRemoteInputPermission() -> Bool {
    CGRequestPostEventAccess()
}

func performRemoteInput(_ args: [String]) throws {
    let operation = try RemoteInput(args)
    guard hasRemoteInputPermission() else { throw RemoteInputFailure.permissionRequired }
    try operation.post()
}

private enum RemoteInput {
    case move(CGPoint)
    case click(CGPoint)
    case scroll(Int32)
    case key(CGKeyCode)
    case text(String)

    private static let keyCodes: [String: CGKeyCode] = [
        "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "escape": 53, "esc": 53,
        "delete": 117, "forwarddelete": 117, "left": 123, "right": 124, "down": 125, "up": 126,
        "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
        "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4, "i": 34,
        "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15,
        "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
        "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
    ]

    init(_ args: [String]) throws {
        switch (args.first, args.count) {
        case ("move", 3):
            self = .move(try Self.screenPoint(x: args[1], y: args[2]))
        case ("click", 3):
            self = .click(try Self.screenPoint(x: args[1], y: args[2]))
        case ("scroll", 2):
            guard let amount = Int32(args[1]), amount != 0, (-1000...1000).contains(amount) else {
                throw RemoteInputFailure.invalidScroll
            }
            self = .scroll(amount)
        case ("key", 2):
            guard let code = Self.keyCodes[args[1].lowercased()] else { throw RemoteInputFailure.unknownKey }
            self = .key(code)
        case ("type", 2):
            let text = args[1]
            guard !text.isEmpty, text.utf16.count <= 4096 else { throw RemoteInputFailure.invalidText }
            guard text.allSatisfy({ String($0).utf16.count <= 20 }) else { throw RemoteInputFailure.unsupportedCharacter }
            self = .text(text)
        default:
            throw RemoteInputFailure.invalidArguments
        }
    }

    private static func screenPoint(x: String, y: String) throws -> CGPoint {
        guard let px = Double(x), let py = Double(y), px.isFinite, py.isFinite else {
            throw RemoteInputFailure.invalidPoint
        }
        let display = CGMainDisplayID()
        let width = Double(CGDisplayPixelsWide(display))
        let height = Double(CGDisplayPixelsHigh(display))
        let bounds = CGDisplayBounds(display)
        guard width > 0, height > 0, px >= 0, py >= 0, px < width, py < height else {
            throw RemoteInputFailure.invalidPoint
        }
        // Screenshot positions are pixels. Quartz event positions are display points.
        return CGPoint(x: bounds.minX + px * bounds.width / width,
                       y: bounds.minY + py * bounds.height / height)
    }

    func post() throws {
        switch self {
        case .move(let point):
            try Self.mouseEvent(.mouseMoved, at: point).post(tap: .cghidEventTap)
        case .click(let point):
            let move = try Self.mouseEvent(.mouseMoved, at: point)
            let down = try Self.mouseEvent(.leftMouseDown, at: point)
            let up = try Self.mouseEvent(.leftMouseUp, at: point)
            down.setIntegerValueField(.mouseEventClickState, value: 1)
            up.setIntegerValueField(.mouseEventClickState, value: 1)
            up.setIntegerValueField(.mouseEventNumber, value: down.getIntegerValueField(.mouseEventNumber))
            move.post(tap: .cghidEventTap)
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
        case .scroll(let amount):
            // The web client sends positive deltas for scrolling down. Quartz uses positive for up.
            guard let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: -amount, wheel2: 0, wheel3: 0) else {
                throw RemoteInputFailure.eventCreationFailed
            }
            event.post(tap: .cghidEventTap)
        case .key(let code):
            try Self.postKey(code)
        case .text(let text):
            for character in text {
                try Self.postKey(0, unicode: Array(String(character).utf16))
            }
        }
    }

    private static func mouseEvent(_ type: CGEventType, at point: CGPoint) throws -> CGEvent {
        guard let event = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left) else {
            throw RemoteInputFailure.eventCreationFailed
        }
        return event
    }

    private static func postKey(_ code: CGKeyCode, unicode: [UInt16] = []) throws {
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else {
            throw RemoteInputFailure.eventCreationFailed
        }
        if !unicode.isEmpty {
            unicode.withUnsafeBufferPointer { buffer in
                down.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
                up.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
            }
        }
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }
}
