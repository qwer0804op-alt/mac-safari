import ApplicationServices
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

// A narrowly scoped bridge for the local remote-control server. Coordinates are
// pixels in the primary display screenshot; Quartz mouse events use points.
enum HelperError: Error, CustomStringConvertible {
    case message(String)
    var description: String {
        switch self { case .message(let value): return value }
    }
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

func requireAccessibility() throws {
    guard AXIsProcessTrusted() else {
        throw HelperError.message("Mac 리모컨에서 클릭·입력 권한을 허용해줘.")
    }
}

func requireScreenCapture() throws {
    guard CGPreflightScreenCaptureAccess() else {
        throw HelperError.message("Mac 리모컨에서 화면 보기 권한을 허용해줘.")
    }
}

func number(_ value: String) throws -> Double {
    guard let result = Double(value), result.isFinite else {
        throw HelperError.message("Coordinates must be finite numbers.")
    }
    return result
}

func screenPoint(_ x: String, _ y: String) throws -> CGPoint {
    let px = try number(x), py = try number(y)
    let display = CGMainDisplayID()
    let width = CGDisplayPixelsWide(display), height = CGDisplayPixelsHigh(display)
    let bounds = CGDisplayBounds(display)
    guard width > 0, height > 0, px >= 0, py >= 0,
          px < Double(width), py < Double(height) else {
        throw HelperError.message("Coordinates are outside the primary display.")
    }
    return CGPoint(x: bounds.minX + px * bounds.width / Double(width),
                   y: bounds.minY + py * bounds.height / Double(height))
}

func post(_ event: CGEvent?) throws {
    guard let event = event else { throw HelperError.message("Unable to create input event.") }
    event.post(tap: .cghidEventTap)
}

let keyPairs = "enter:36 return:36 tab:48 space:49 backspace:51 escape:53 esc:53 delete:117 forwarddelete:117 left:123 right:124 down:125 up:126 home:115 end:119 pageup:116 pagedown:121 a:0 s:1 d:2 f:3 h:4 g:5 z:6 x:7 c:8 v:9 b:11 q:12 w:13 e:14 r:15 y:16 t:17 1:18 2:19 3:20 4:21 6:22 5:23 9:25 7:26 8:28 0:29 o:31 u:32 i:34 p:35 l:37 j:38 k:40 n:45 m:46"
var keys: [String: CGKeyCode] = [:]
for pair in keyPairs.split(separator: " ") {
    let parts = pair.split(separator: ":")
    keys[String(parts[0])] = CGKeyCode(parts[1])!
}

let args = Array(CommandLine.arguments.dropFirst())
func usage() -> Never {
    fail("Usage: RemoteHelper permissions | screenshot <path> | click <x> <y> | move <x> <y> | scroll <dy> | key <name> | type <text>", code: 2)
}

func screenshot(_ args: [String]) async throws {
    guard args.count == 2, !args[1].isEmpty else { usage() }
    try requireScreenCapture()
    let display = CGMainDisplayID()
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
    guard let screen = content.displays.first(where: { $0.displayID == display }) else {
        throw HelperError.message("Unable to find the primary display.")
    }
    let filter = SCContentFilter(display: screen, excludingWindows: [])
    let configuration = SCStreamConfiguration()
    configuration.width = CGDisplayPixelsWide(display)
    configuration.height = CGDisplayPixelsHigh(display)
    configuration.showsCursor = true
    let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
    let url = URL(fileURLWithPath: args[1]) as CFURL
    guard let destination = CGImageDestinationCreateWithURL(url, UTType.jpeg.identifier as CFString, 1, nil) else {
        throw HelperError.message("Unable to create screenshot file.")
    }
    CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: 0.65] as CFDictionary)
    guard CGImageDestinationFinalize(destination) else { throw HelperError.message("Unable to save screenshot file.") }
    print("{\"width\":\(image.width),\"height\":\(image.height)}")
}

func mouse(_ args: [String], click: Bool) throws {
    guard args.count == 3 else { usage() }
    let point = try screenPoint(args[1], args[2])
    try requireAccessibility()
    if !click {
        try post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left))
    } else {
        try post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left))
        try post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left))
    }
}

func scroll(_ args: [String]) throws {
    guard args.count == 2 else { usage() }
    guard let amount = Int32(args[1]), amount != 0, (-1000...1000).contains(amount) else {
        throw HelperError.message("Scroll amount must be a nonzero integer between -1000 and 1000.")
    }
    try requireAccessibility()
    // The web client sends positive deltas for scrolling down. Quartz uses positive for up.
    try post(CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: -amount, wheel2: 0, wheel3: 0))
}

func key(_ args: [String]) throws {
    guard args.count == 2 else { usage() }
    guard let key = keys[args[1].lowercased()] else { throw HelperError.message("Unsupported key.") }
    try requireAccessibility()
    try post(CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: true))
    try post(CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: false))
}

func typeText(_ args: [String]) throws {
    guard args.count == 2, !args[1].isEmpty, args[1].utf16.count <= 4096 else {
        throw HelperError.message("Text must contain 1 to 4096 UTF-16 code units.")
    }
    guard args[1].allSatisfy({ String($0).utf16.count <= 20 }) else {
        throw HelperError.message("Text contains an unsupported long character.")
    }
    try requireAccessibility()
    for character in args[1] {
        let units = Array(String(character).utf16)
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
            throw HelperError.message("Unable to create keyboard event.")
        }
        units.withUnsafeBufferPointer { buffer in
            down.keyboardSetUnicodeString(stringLength: units.count, unicodeString: buffer.baseAddress)
            up.keyboardSetUnicodeString(stringLength: units.count, unicodeString: buffer.baseAddress)
        }
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }
}

do {
    guard let command = args.first else { usage() }
    switch command {
    case "screenshot": try await screenshot(args)
    case "permissions":
        guard args.count == 1 else { usage() }
        let accessibility = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
        let screen = CGRequestScreenCaptureAccess()
        print("{\"accessibility\":\(accessibility),\"screenCapture\":\(screen)}")
    case "click": try mouse(args, click: true)
    case "move": try mouse(args, click: false)
    case "scroll": try scroll(args)
    case "key": try key(args)
    case "type": try typeText(args)
    default: usage()
    }
} catch { fail(String(describing: error)) }
