// Reads komacam's MJPEG stream (multipart/x-mixed-replace, boundary "komacam") from 127.0.0.1 and hands each JPEG
// to a callback. Reconnects every second while komacam is not running. DRAFT: typechecked, never built or run.
import Foundation

final class MJPEGReader: NSObject, URLSessionDataDelegate {
    private let url: URL
    private let onJPEG: (Data) -> Void
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var buffer = Data()
    private var running = false
    private let queue = DispatchQueue(label: "komacam.mjpeg")

    init(url: URL, onJPEG: @escaping (Data) -> Void) {
        self.url = url
        self.onJPEG = onJPEG
    }

    func start() {
        queue.async {
            self.running = true
            self.connect()
        }
    }

    func stop() {
        queue.async {
            self.running = false
            self.task?.cancel()
            self.session?.invalidateAndCancel()
            self.session = nil
        }
    }

    private func connect() {
        guard running else { return }
        buffer.removeAll(keepingCapacity: true)
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 20
        let opq = OperationQueue()
        opq.underlyingQueue = queue
        opq.maxConcurrentOperationCount = 1
        session = URLSession(configuration: config, delegate: self, delegateQueue: opq)
        task = session?.dataTask(with: url)
        task?.resume()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        // Each part: --komacam\r\n headers \r\n\r\n <Content-Length bytes> \r\n
        let sep = Data("\r\n\r\n".utf8)
        while let headerEnd = buffer.range(of: sep) {
            let header = String(decoding: buffer[buffer.startIndex..<headerEnd.lowerBound], as: UTF8.self)
            guard let length = header.split(separator: "\r\n").compactMap({ line -> Int? in
                let kv = line.split(separator: ":", maxSplits: 1)
                guard kv.count == 2, kv[0].lowercased() == "content-length" else { return nil }
                return Int(kv[1].trimmingCharacters(in: .whitespaces))
            }).first else {
                buffer.removeSubrange(buffer.startIndex..<headerEnd.upperBound)  // junk: skip it
                continue
            }
            let bodyStart = headerEnd.upperBound
            guard buffer.distance(from: bodyStart, to: buffer.endIndex) >= length else { return }  // wait for more
            let bodyEnd = buffer.index(bodyStart, offsetBy: length)
            onJPEG(buffer.subdata(in: bodyStart..<bodyEnd))
            buffer.removeSubrange(buffer.startIndex..<bodyEnd)
        }
        if buffer.count > 8 << 20 { buffer.removeAll() }  // never grow without bound
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard running else { return }
        queue.asyncAfter(deadline: .now() + 1.0) { self.connect() }
    }
}
