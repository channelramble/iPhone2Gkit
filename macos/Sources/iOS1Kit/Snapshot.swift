import AppKit

/// Development aid: IOS1KIT_SNAPSHOT=/path.png makes the app save a picture of its own
/// window after a few seconds (no screen-recording permission needed), then quit.
enum Snapshot {
    @MainActor
    static func scheduleIfRequested(_ model: Model) {
        let env = ProcessInfo.processInfo.environment
        guard let path = env["IOS1KIT_SNAPSHOT"] else { return }
        // IOS1KIT_AUTORUN=probe|install|repair starts a job without the confirmation dialog
        if let job = env["IOS1KIT_AUTORUN"].flatMap(Job.init(rawValue:)), job != .restore {
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) { model.run(job) }
        }
        NSApp.appearance = NSAppearance(named: .aqua)
        DispatchQueue.main.asyncAfter(deadline: .now() + (Double(env["IOS1KIT_SNAPSHOT_DELAY"] ?? "") ?? 15)) {
            guard let view = NSApp.windows.first(where: { $0.isVisible })?.contentView,
                  let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { exit(1) }
            view.cacheDisplay(in: view.bounds, to: rep)
            // Flatten the cached transparent view onto an opaque light background.
            // Direct Core Graphics drawing also works when no AppKit drawing focus exists.
            let bounds = CGRect(x: 0, y: 0, width: rep.pixelsWide, height: rep.pixelsHigh)
            guard let pixels = rep.cgImage,
                  let context = CGContext(data: nil, width: rep.pixelsWide, height: rep.pixelsHigh,
                                          bitsPerComponent: 8, bytesPerRow: 0,
                                          space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(1) }
            context.setFillColor(CGColor(gray: 0.95, alpha: 1))
            context.fill(bounds)
            context.draw(pixels, in: bounds)
            guard let picture = context.makeImage() else { exit(1) }
            let out = NSBitmapImageRep(cgImage: picture)
            try? out.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
            exit(0)
        }
    }
}
