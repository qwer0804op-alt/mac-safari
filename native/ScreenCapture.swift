import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

private enum ScreenCaptureFailure: LocalizedError {
    case permissionDenied
    case displayUnavailable
    case captureFailed(Int)
    case saveFailed

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Mac 리모컨의 화면 보기 권한을 켠 뒤 앱을 완전히 닫았다가 다시 열어 주세요."
        case .displayUnavailable:
            return "Mac의 주 화면을 찾을 수 없습니다. 화면이 켜져 있는지 확인해 주세요."
        case .captureFailed(let code):
            return "Mac 화면을 캡처할 수 없습니다. Mac을 열어 둔 상태에서 다시 시도해 주세요. (오류 \(code))"
        case .saveFailed:
            return "Mac 화면 이미지를 저장할 수 없습니다. 다시 시도해 주세요."
        }
    }
}

@discardableResult
func captureScreen(to url: URL) async throws -> (width: Int, height: Int) {
    let display = CGMainDisplayID()
    let width = CGDisplayPixelsWide(display)
    let height = CGDisplayPixelsHigh(display)
    guard width > 0, height > 0 else { throw ScreenCaptureFailure.displayUnavailable }

    let image: CGImage
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let screen = content.displays.first(where: { $0.displayID == display }) else {
            throw ScreenCaptureFailure.displayUnavailable
        }
        let filter = SCContentFilter(display: screen, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.width = width
        configuration.height = height
        configuration.showsCursor = true
        image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
    } catch let failure as ScreenCaptureFailure {
        throw failure
    } catch is CancellationError {
        throw CancellationError()
    } catch {
        let underlying = error as NSError
        let declined = underlying.domain == SCStreamError.errorDomain && underlying.code == SCStreamError.Code.userDeclined.rawValue
        if declined || !CGPreflightScreenCaptureAccess() { throw ScreenCaptureFailure.permissionDenied }
        throw ScreenCaptureFailure.captureFailed(underlying.code)
    }

    try Task.checkCancellation()
    guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else {
        throw ScreenCaptureFailure.saveFailed
    }
    CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: 0.65] as CFDictionary)
    guard CGImageDestinationFinalize(destination) else { throw ScreenCaptureFailure.saveFailed }
    return (image.width, image.height)
}
