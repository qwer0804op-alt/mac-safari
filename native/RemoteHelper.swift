import CoreGraphics
import Foundation

private func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

private func usage() -> Never {
    fail("Usage: RemoteHelper permissions | screenshot <path> | click <x> <y> | move <x> <y> | scroll <dy> | key <name> | type <text>", code: 2)
}

@main
struct RemoteHelper {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        do {
            switch args.first {
            case "screenshot":
                guard args.count == 2, !args[1].isEmpty else { usage() }
                let size = try await captureScreen(to: URL(fileURLWithPath: args[1]))
                print("{\"width\":\(size.width),\"height\":\(size.height)}")
            case "permissions":
                guard args.count == 1 else { usage() }
                let accessibility = requestRemoteInputPermission()
                let screen = CGRequestScreenCaptureAccess()
                print("{\"accessibility\":\(accessibility),\"screenCapture\":\(screen)}")
            default:
                try performRemoteInput(args)
            }
        } catch RemoteInputFailure.invalidArguments {
            usage()
        } catch {
            fail((error as? LocalizedError)?.errorDescription ?? String(describing: error))
        }
    }
}
