// Komacam CoreMediaIO Camera Extension (macOS 12.3+). One device, one 1280x720 BGRA stream at 30 fps, fed by
// komacam's http://127.0.0.1:8770/hd.mjpg (komacam does the 4:3 -> 16:9 fit and the looks). While komacam is not
// running, or no frame came for 1 s, the last frame repeats (black before the first one).
// DRAFT based on Apple's "Camera Extension" Xcode template: typechecked only, never built, signed or installed.
import AppKit
import CoreMediaIO
import CoreVideo
import ImageIO
import IOKit.audio
import os.log

let kWidth: Int32 = 1280
let kHeight: Int32 = 720
let kFrameRate: Int = 30
let kStreamURL = URL(string: "http://127.0.0.1:8770/hd.mjpg")!
let logger = Logger(subsystem: "com.forthright.komacam.extension", category: "camera")

// MARK: - Device

class ExtensionDeviceSource: NSObject, CMIOExtensionDeviceSource {
    private(set) var device: CMIOExtensionDevice!
    private var streamSource: ExtensionStreamSource!
    private var streamingCounter: UInt32 = 0
    private var timer: DispatchSourceTimer?
    private let timerQueue = DispatchQueue(label: "komacam.timer", qos: .userInteractive)
    private var videoDescription: CMFormatDescription!
    private var bufferPool: CVPixelBufferPool!
    private var bufferAuxAttributes: NSDictionary!
    private var reader: MJPEGReader?
    private let frameLock = NSLock()
    private var latest: CVPixelBuffer?

    init(localizedName: String) {
        super.init()
        let deviceID = UUID(uuidString: "6C1B7A0E-5E2A-4C5B-9E1D-4B4F4D41C001")!  // fixed: Zoom remembers the camera
        device = CMIOExtensionDevice(localizedName: localizedName, deviceID: deviceID, legacyDeviceID: nil, source: self)
        let dims = CMVideoDimensions(width: kWidth, height: kHeight)
        CMVideoFormatDescriptionCreate(allocator: kCFAllocatorDefault, codecType: kCVPixelFormatType_32BGRA,
                                       width: dims.width, height: dims.height, extensions: nil,
                                       formatDescriptionOut: &videoDescription)
        let attrs: NSDictionary = [kCVPixelBufferWidthKey: dims.width, kCVPixelBufferHeightKey: dims.height,
                                   kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
                                   kCVPixelBufferIOSurfacePropertiesKey: [:] as NSDictionary]
        CVPixelBufferPoolCreate(kCFAllocatorDefault, nil, attrs, &bufferPool)
        let format = CMIOExtensionStreamFormat(formatDescription: videoDescription,
                                               maxFrameDuration: CMTime(value: 1, timescale: Int32(kFrameRate)),
                                               minFrameDuration: CMTime(value: 1, timescale: Int32(kFrameRate)),
                                               validFrameDurations: nil)
        bufferAuxAttributes = [kCVPixelBufferPoolAllocationThresholdKey: 5]
        let streamID = UUID(uuidString: "6C1B7A0E-5E2A-4C5B-9E1D-4B4F4D41C002")!
        streamSource = ExtensionStreamSource(localizedName: "Komacam.Video", streamID: streamID,
                                             streamFormat: format, device: device)
        do { try device.addStream(streamSource.stream) } catch { fatalError("addStream: \(error)") }
    }

    var availableProperties: Set<CMIOExtensionProperty> { [.deviceTransportType, .deviceModel] }

    func deviceProperties(forProperties properties: Set<CMIOExtensionProperty>) throws -> CMIOExtensionDeviceProperties {
        let p = CMIOExtensionDeviceProperties(dictionary: [:])
        if properties.contains(.deviceTransportType) { p.transportType = kIOAudioDeviceTransportTypeVirtual }
        if properties.contains(.deviceModel) { p.model = "GR DIGITAL via komacam" }
        return p
    }

    func setDeviceProperties(_ deviceProperties: CMIOExtensionDeviceProperties) throws {}

    /// JPEG -> BGRA pixel buffer from the pool, scaled to fill 1280x720 (komacam already sends 1280x720).
    private func decode(_ jpeg: Data) -> CVPixelBuffer? {
        guard let src = CGImageSourceCreateWithData(jpeg as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return nil }
        var pb: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(kCFAllocatorDefault, bufferPool,
                                                                  bufferAuxAttributes, &pb) == kCVReturnSuccess,
              let buffer = pb else { return nil }
        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        guard let ctx = CGContext(data: CVPixelBufferGetBaseAddress(buffer), width: Int(kWidth), height: Int(kHeight),
                                  bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue
                                      | CGBitmapInfo.byteOrder32Little.rawValue) else { return nil }
        ctx.interpolationQuality = .medium
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: Int(kWidth), height: Int(kHeight)))
        return buffer
    }

    func startStreaming() {
        streamingCounter += 1
        guard streamingCounter == 1 else { return }
        reader = MJPEGReader(url: kStreamURL) { [weak self] jpeg in
            guard let self, let pb = self.decode(jpeg) else { return }
            self.frameLock.lock(); self.latest = pb; self.frameLock.unlock()
        }
        reader?.start()
        timer = DispatchSource.makeTimerSource(flags: .strict, queue: timerQueue)
        timer!.schedule(deadline: .now(), repeating: 1.0 / Double(kFrameRate), leeway: .milliseconds(2))
        timer!.setEventHandler { [weak self] in self?.emit() }
        timer!.resume()
        logger.info("komacam extension: streaming from \(kStreamURL.absoluteString, privacy: .public)")
    }

    private func emit() {
        frameLock.lock(); var pb = latest; frameLock.unlock()
        if pb == nil {  // nothing from komacam yet: a black frame
            CVPixelBufferPoolCreatePixelBufferWithAuxAttributes(kCFAllocatorDefault, bufferPool, bufferAuxAttributes, &pb)
            if let b = pb {
                CVPixelBufferLockBaseAddress(b, [])
                memset(CVPixelBufferGetBaseAddress(b), 0, CVPixelBufferGetDataSize(b))
                CVPixelBufferUnlockBaseAddress(b, [])
            }
        }
        guard let buffer = pb else { return }
        var timing = CMSampleTimingInfo(duration: .invalid,
                                        presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
                                        decodeTimeStamp: .invalid)
        var sample: CMSampleBuffer?
        guard CMSampleBufferCreateForImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: buffer, dataReady: true,
                                                 makeDataReadyCallback: nil, refcon: nil,
                                                 formatDescription: videoDescription, sampleTiming: &timing,
                                                 sampleBufferOut: &sample) == noErr, let s = sample else { return }
        streamSource.stream.send(s, discontinuity: [],
                                 hostTimeInNanoseconds: UInt64(timing.presentationTimeStamp.seconds * 1e9))
    }

    func stopStreaming() {
        guard streamingCounter > 0 else { return }
        streamingCounter -= 1
        guard streamingCounter == 0 else { return }
        timer?.cancel(); timer = nil
        reader?.stop(); reader = nil
    }
}

// MARK: - Stream

class ExtensionStreamSource: NSObject, CMIOExtensionStreamSource {
    private(set) var stream: CMIOExtensionStream!
    let device: CMIOExtensionDevice
    private let format: CMIOExtensionStreamFormat

    init(localizedName: String, streamID: UUID, streamFormat: CMIOExtensionStreamFormat, device: CMIOExtensionDevice) {
        self.device = device
        self.format = streamFormat
        super.init()
        stream = CMIOExtensionStream(localizedName: localizedName, streamID: streamID, direction: .source,
                                     clockType: .hostTime, source: self)
    }

    var formats: [CMIOExtensionStreamFormat] { [format] }
    var activeFormatIndex: Int = 0

    var availableProperties: Set<CMIOExtensionProperty> { [.streamActiveFormatIndex, .streamFrameDuration] }

    func streamProperties(forProperties properties: Set<CMIOExtensionProperty>) throws -> CMIOExtensionStreamProperties {
        let p = CMIOExtensionStreamProperties(dictionary: [:])
        if properties.contains(.streamActiveFormatIndex) { p.activeFormatIndex = 0 }
        if properties.contains(.streamFrameDuration) { p.frameDuration = CMTime(value: 1, timescale: Int32(kFrameRate)) }
        return p
    }

    func setStreamProperties(_ streamProperties: CMIOExtensionStreamProperties) throws {
        if let i = streamProperties.activeFormatIndex { activeFormatIndex = i }
    }

    func authorizedToStartStream(for client: CMIOExtensionClient) -> Bool { true }

    func startStream() throws {
        (device.source as? ExtensionDeviceSource)?.startStreaming()
    }

    func stopStream() throws {
        (device.source as? ExtensionDeviceSource)?.stopStreaming()
    }
}

// MARK: - Provider

class ExtensionProviderSource: NSObject, CMIOExtensionProviderSource {
    private(set) var provider: CMIOExtensionProvider!
    private var deviceSource: ExtensionDeviceSource!

    init(clientQueue: DispatchQueue?) {
        super.init()
        provider = CMIOExtensionProvider(source: self, clientQueue: clientQueue)
        deviceSource = ExtensionDeviceSource(localizedName: "Koma GR")  // the name in Zoom
        do { try provider.addDevice(deviceSource.device) } catch { fatalError("addDevice: \(error)") }
    }

    func connect(to client: CMIOExtensionClient) throws {}
    func disconnect(from client: CMIOExtensionClient) {}

    var availableProperties: Set<CMIOExtensionProperty> { [.providerManufacturer] }

    func providerProperties(forProperties properties: Set<CMIOExtensionProperty>) throws -> CMIOExtensionProviderProperties {
        let p = CMIOExtensionProviderProperties(dictionary: [:])
        if properties.contains(.providerManufacturer) { p.manufacturer = "Project Koma" }
        return p
    }

    func setProviderProperties(_ providerProperties: CMIOExtensionProviderProperties) throws {}
}
