import AppKit

let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let bounds = NSRect(x: 0, y: 0, width: pixels, height: pixels)
        NSColor(calibratedRed: 0.12, green: 0.48, blue: 0.36, alpha: 1).setFill()
        NSBezierPath(roundedRect: bounds.insetBy(dx: CGFloat(pixels) * 0.06, dy: CGFloat(pixels) * 0.06),
                     xRadius: CGFloat(pixels) * 0.2, yRadius: CGFloat(pixels) * 0.2).fill()
        let symbol = NSImage(systemSymbolName: "bubble.left.and.bubble.right.fill", accessibilityDescription: nil)!
            .withSymbolConfiguration(.init(paletteColors: [.white, NSColor(calibratedRed: 0.98, green: 0.8, blue: 0.26, alpha: 1)]))!
        symbol.draw(in: bounds.insetBy(dx: CGFloat(pixels) * 0.21, dy: CGFloat(pixels) * 0.24))
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let suffix = scale == 2 ? "@2x" : ""
        try bitmap.representation(using: .png, properties: [:])!.write(to:
            directory.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
