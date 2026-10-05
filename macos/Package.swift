// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "iPhone2Gkit",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "iPhone2Gkit", path: "Sources/iOS1Kit")
    ]
)
