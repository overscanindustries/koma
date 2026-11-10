# Komacam camera extension (DRAFT: typechecked, never built, signed or installed)

This is a CoreMediaIO Camera Extension (macOS 12.3+). It would make "Koma GR" a camera in Zoom without
OBS. The extension reads komacam's `http://127.0.0.1:8770/hd.mjpg` and decodes each JPEG with ImageIO into a 1280x720
BGRA buffer. It sends frames at 30 fps and repeats the last frame between camera frames; before the first frame it
sends black.

## Files

- `KomacamExtension/` holds the extension:
  - `ExtensionProvider.swift`: the provider, device and stream, following Apple's Camera Extension template;
  - `MJPEGReader.swift`: a URLSession multipart parser that reconnects every second;
  - `main.swift`;
  - `Info.plist`: `CMIOExtensionMachServiceName` is `$(TeamIdentifierPrefix)com.forthright.komacam.extension`, and
    ATS allows local networking;
  - the entitlements: sandbox, app group, `network.client`.
- `KomacamHost/` holds a SwiftUI host app with Install and Uninstall buttons (`OSSystemExtensionRequest`) and the
  `system-extension.install` entitlement.
- `project.yml`: an xcodegen spec with team `X57GZ92C8A` (Forthright Consulting LLC). The extension is embedded at
  `Contents/Library/SystemExtensions`.

## Environment check (2026-11-07, typecheck only)

- Checked with Xcode 26.3 and the macOS 26.2 SDK.
- Two Apple Development signing identities are present.
- xcodegen is installed.
- `swiftc -typecheck` passes on both targets.

## To try it (not done)

1. Run `cd tools/komacam/macos-extension && xcodegen generate`, then open `Komacam.xcodeproj`.
2. Check the signing: team X57GZ92C8A, and the app group and system-extension capabilities on the App IDs.
3. Build `KomacamHost`, copy `KomacamHost.app` to **/Applications** (system extensions install only from there), run it
   and click Install.
4. Approve it in System Settings > General > Login Items & Extensions > Camera Extensions.
5. Turn the webcam on in the Koma app (Webcam), then pick "Koma GR" in Zoom. The app finds the extension by its
   bundle ID in `systemextensionsctl list` and uses it instead of OBS Virtual Camera.

## Known gaps

- Never compiled into a bundle, so signing and entitlement problems are unknown.
- The sandboxed extension's localhost fetch relies on the `network.client` entitlement and `NSAllowsLocalNetworking`.
  Whether that is enough is unverified.
- Distributing it beyond the build Mac needs Developer ID signing and notarization.
- There is no state card: the extension shows whatever komacam serves, and komacam already sends its own state card
  while arming.
