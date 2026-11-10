"""macOS: the menu bar item, its menu and the startup window, in AppKit (PyObjC).

The menu is the system's own NSMenu, built from model.menu() on every change, so it looks and behaves like every other
menu bar item (keyboard, VoiceOver, dark and light). Opening it is a look: the camera line is read from the bridge
as the menu opens and dropped when it closes (supervisor.look). The icon is a template image: macOS tints it to the
menu bar, and dims it (appearsDisabled) while Koma is not running. The startup window is drawn with the app's tokens
and Archivo, to the measurements in look.py; it follows the system's appearance.
Nothing here touches the camera; the supervisor does the reading.
"""
from __future__ import annotations

import ctypes
import logging
import os
import signal
import sys
import threading
from pathlib import Path

import objc
from AppKit import (
    NSAccessibilityDisclosureTriangleRole,
    NSAccessibilityLinkRole,
    NSApp,
    NSAppearance,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSKernAttributeName,
    NSLayoutManager,
    NSLineBreakByWordWrapping,
    NSMenu,
    NSMenuItem,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSPasteboard,
    NSPasteboardTypeString,
    NSStatusBar,
    NSTerminateLater,
    NSTerminateNow,
    NSTextField,
    NSTrackingActiveInKeyWindow,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSVariableStatusItemLength,
    NSView,
    NSWindow,
    NSWindowMiniaturizeButton,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
    NSWindowZoomButton,
    NSWorkspace,
)
from Foundation import NSAttributedString, NSMakeRect, NSObject, NSTimer, NSURL

from . import VERSION, look, model, paths, screens, selftest, version_text, words
from .look import (BUTTON_H, BUTTON_PAD_X, BUTTON_SIZE, BUTTON_TOP, DETAILS_LINE, DETAILS_TEXT_X, H, PAD, SMALL_SIZE,
                   TEXT_SIZE, TEXT_X, TOKENS, W, WORDMARK_BASELINE, WORDMARK_SIZE, WORDMARK_TRACK)

log = logging.getLogger("komalauncher")

STATUS_Y = 128           # the status text's frame top; its first baseline is look.STATUS_BASELINE (145), 20 pt lines
LABEL_INSET = 2.0        # an NSTextField label draws its text 2 pt inside its frame (measured)
RING = 4                 # room round a control for the focus ring: 2 pt of --ink, 2 pt out
ACTIONS = {"open": "openKoma:", "copy": "copyAddress:", "retry": "tryAgain:", "quit": "quitKoma:"}

_colors: dict = {}
_layout: list = []


def _rgb(hex_: str):
    v = int(hex_.lstrip("#"), 16)
    return NSColor.colorWithSRGBRed_green_blue_alpha_((v >> 16 & 255) / 255, (v >> 8 & 255) / 255, (v & 255) / 255, 1)


def color(name: str):
    """A token as a dynamic color: resolved against the appearance it is drawn in, so dark and light both hold."""
    if name not in _colors:
        dark, light = (_rgb(h) for h in TOKENS[name])

        def provider(appearance):
            match = appearance.bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
            return dark if match == NSAppearanceNameDarkAqua else light

        _colors[name] = NSColor.colorWithName_dynamicProvider_(f"koma.{name}", provider)
    return _colors[name]


def register_fonts():
    """Koma Wordmark, Koma Text and Koma Text Medium (Archivo instances) for this process only."""
    try:
        ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
        ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
    except OSError as error:
        log.warning("CoreText: %s", error)
        return
    for path in sorted(paths.FONTS.glob("*.ttf")):
        url = NSURL.fileURLWithPath_(str(path))
        ct.CTFontManagerRegisterFontsForURL(objc.pyobjc_id(url), 1, None)  # kCTFontManagerScopeProcess


def font(name: str, size: float, weight: float = 0.0):
    return NSFont.fontWithName_size_(name, size) or NSFont.systemFontOfSize_weight_(size, weight)


def bearing(face, first: str = "K") -> float:
    """Points from where this face's line starts to its first letter's left ink (look.bearing)."""
    return look.bearing(str(face.familyName()), face.pointSize(), first)


def baseline_offset(face) -> float:
    """How far below the point given to drawAtPoint_ the baseline falls: TextKit's own figure (12 for Koma Text
    Medium 13.5, measured). AppKit then puts the baseline on the device pixel grid (on_grid)."""
    if not _layout:
        _layout.append(NSLayoutManager.alloc().init())
    return _layout[0].defaultBaselineOffsetForFont_(face)


def on_grid(y: float) -> float:
    """A baseline on the 2x device grid, as AppKit draws it, so texts placed apart land on the same row."""
    return round(y * 2) / 2


def text(value: str, face, color, kern: float = 0.0, line: float = 0.0):
    attrs = {NSFontAttributeName: face, NSForegroundColorAttributeName: color}
    if kern:
        attrs[NSKernAttributeName] = kern
    if line:
        para = NSMutableParagraphStyle.alloc().init()
        para.setMinimumLineHeight_(line)
        para.setMaximumLineHeight_(line)
        para.setLineBreakMode_(NSLineBreakByWordWrapping)
        attrs[NSParagraphStyleAttributeName] = para
    return NSAttributedString.alloc().initWithString_attributes_(value, attrs)


def label(frame, selectable: bool = False):
    field = NSTextField.labelWithString_("")
    field.setFrame_(frame)
    field.setDrawsBackground_(False)
    field.setBezeled_(False)
    field.setEditable_(False)
    field.setSelectable_(selectable)
    return field


def home_path(path: Path) -> str:
    """A path as people type it: the home folder as ~."""
    home = str(Path.home())
    value = str(path)
    return "~" + value[len(home):] if value == home or value.startswith(home + os.sep) else value


class FlippedView(NSView):
    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        color("bg").setFill()
        NSBezierPath.fillRect_(rect)


class Dot(NSView):
    """The StatusLine dot: 6 px, faint while working, ok when running, bad when it could not start."""
    tone = "faint"

    def drawRect_(self, rect):
        color(self.tone).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(self.bounds()).fill()


def _focused(view) -> bool:
    win = view.window()
    return win is not None and win.isKeyWindow() and win.firstResponder() == view


def _ring(view, radius: float):
    """The keyboard focus ring: 2 pt of --ink, 2 pt outside the control (its frame keeps RING pt for it); radius is
    the ring's centre line (a 4 pt button corner plus 3)."""
    frame = view.bounds()
    ring = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(1, 1, frame.size.width - 2, frame.size.height - 2), radius, radius)
    color("ink").setStroke()
    ring.setLineWidth_(2)
    ring.stroke()


class QuietButton(NSButton):
    """--raise fill, 1 px --line border, --ink sentence-case title, radius 4; pressed moves 1 px
    down, no color change. The frame is the button's outer box plus RING on every side. The title's cap height is
    centred in the box (look.title_baseline); the version's baseline sits on the same row."""

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        down = 1 if self.cell().isHighlighted() else 0
        frame = self.bounds()
        width, height = frame.size.width - 2 * RING, frame.size.height - 2 * RING
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(   # the border inside the outer box
            NSMakeRect(RING + 0.5, RING + 0.5 + down, width - 1, height - 1), 3.5, 3.5)
        color("raise").setFill()
        path.fill()
        color("line").setStroke()
        path.setLineWidth_(1)
        path.stroke()
        face = font("KomaTextMedium", BUTTON_SIZE, 0.23)
        title = text(self.title(), face, color("ink"))
        baseline = on_grid(look.title_baseline(RING, height, BUTTON_SIZE)) + down
        title.drawAtPoint_((RING + (width - title.size().width) / 2, baseline - baseline_offset(face)))
        if _focused(self):
            _ring(self, 7)


class LinkBase(NSButton):
    """A link: --sub, underlined at rest (1 px, 0.2 em under the baseline, at 55 %), --ink and a solid
    underline on hover; keyboard focus draws the ring. The frame is the link's box plus RING on every side."""

    def initWithFrame_(self, frame):
        self = objc.super(LinkBase, self).initWithFrame_(frame)
        if self is None:
            return None
        self.hover = False
        self.setBordered_(False)
        area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            NSMakeRect(0, 0, 0, 0), NSTrackingMouseEnteredAndExited | NSTrackingActiveInKeyWindow
            | NSTrackingInVisibleRect, self, None)
        self.addTrackingArea_(area)
        return self

    def isFlipped(self):
        return True

    def mouseEntered_(self, _event):
        self.hover = True
        self.setNeedsDisplay_(True)

    def mouseExited_(self, _event):
        self.hover = False
        self.setNeedsDisplay_(True)

    @objc.python_method
    def tone(self):
        return color("ink") if self.hover else color("sub")

    @objc.python_method
    def draw_text(self, value: str, x: float):
        """The link's words at x, on the box's first baseline, underlined; returns the baseline."""
        face = font("KomaText", SMALL_SIZE)
        tone = self.tone()
        label_ = text(value, face, tone)
        baseline = on_grid(RING + baseline_offset(face))
        label_.drawAtPoint_((x, baseline - baseline_offset(face)))
        under = NSBezierPath.bezierPath()
        y = round(baseline + 0.2 * SMALL_SIZE) + 0.5
        under.moveToPoint_((x, y))
        under.lineToPoint_((x + label_.size().width, y))
        under.setLineWidth_(1)
        (tone if self.hover else tone.colorWithAlphaComponent_(0.55)).setStroke()
        under.stroke()
        return baseline


class DetailsLink(LinkBase):
    """The Details disclosure is a link, not a button. A chevron (right closed, down open; 1.5 px,
    square caps) whose point is the text column's left edge, then "Details"."""

    def initWithFrame_(self, frame):
        self = objc.super(DetailsLink, self).initWithFrame_(frame)
        if self is None:
            return None
        self.is_open = False
        self.setTitle_(words.DETAILS)
        self.setAccessibilityRole_(NSAccessibilityDisclosureTriangleRole)
        self.setAccessibilityLabel_(words.DETAILS)
        self.setAccessibilityValue_(0)
        return self

    @objc.python_method
    def set_open(self, value: bool):
        self.is_open = value
        self.setAccessibilityValue_(1 if value else 0)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def content_width(self) -> float:
        return DETAILS_TEXT_X - TEXT_X + text(words.DETAILS, font("KomaText", SMALL_SIZE), color("sub")).size().width

    def drawRect_(self, rect):
        face = font("KomaText", SMALL_SIZE)
        baseline = self.draw_text(words.DETAILS, RING + DETAILS_TEXT_X - TEXT_X)
        middle = baseline - face.xHeight() / 2   # the chevron sits on the x-height, like the app's summary
        points = look.chevron_points(RING, middle, self.is_open)
        chevron = NSBezierPath.bezierPath()
        chevron.setLineWidth_(look.CHEVRON_STROKE)
        chevron.setLineCapStyle_(2)              # square caps and mitre joins
        chevron.setLineJoinStyle_(0)
        chevron.moveToPoint_(points[0])
        for point in points[1:]:
            chevron.lineToPoint_(point)
        self.tone().setStroke()
        chevron.stroke()
        if _focused(self):
            _ring(self, 4)


class LogLink(LinkBase):
    """The log file's path under Details, as a link that shows the file in the Finder. Cut in the middle when the
    window is too narrow for it; VoiceOver reads the whole path, then what the link does."""

    def initWithFrame_(self, frame):
        self = objc.super(LogLink, self).initWithFrame_(frame)
        if self is None:
            return None
        self.full = ""
        self.shown = ""
        self.setTitle_("")
        self.setAccessibilityRole_(NSAccessibilityLinkRole)
        self.setToolTip_(words.LOG_REVEAL["darwin"])
        self.setAccessibilityHelp_(words.LOG_REVEAL["darwin"])
        return self

    @objc.python_method
    def set_path(self, value: str, width: float) -> float:
        """The path, fitted to width; returns the width it takes."""
        face = font("KomaText", SMALL_SIZE)
        self.full = value
        self.shown = look.fit_middle(value, lambda s: text(s, face, color("sub")).size().width, width)
        self.setAccessibilityLabel_(value)
        self.setNeedsDisplay_(True)
        return text(self.shown, face, color("sub")).size().width

    def drawRect_(self, rect):
        self.draw_text(self.shown, RING)
        if _focused(self):
            _ring(self, 4)


class StartupWindowFrame(NSWindow):
    def cancelOperation_(self, sender):  # Esc closes, like any small window
        self.performClose_(sender)


class StartupWindow:
    """First launch, or a launch that could not start: the wordmark, one status line, the version (once one is set),
    one action, and in the failed state a Details link that shows where the log file is, as a link to it."""

    def __init__(self, target, log_file: Path | None = None, version: str | None = version_text()):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskFullSizeContentView
        win = StartupWindowFrame.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False)
        win.setTitle_(words.WINDOW_TITLE)
        win.setTitleVisibility_(NSWindowTitleHidden)
        win.setTitlebarAppearsTransparent_(True)
        win.setMovableByWindowBackground_(True)
        win.setReleasedWhenClosed_(False)
        win.setBackgroundColor_(color("bg"))
        for which in (NSWindowMiniaturizeButton, NSWindowZoomButton):
            button = win.standardWindowButton_(which)
            if button:
                button.setHidden_(True)
        view = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        win.setContentView_(view)

        # the wordmark's K and the dot share the left ink edge PAD; the status text's stems and the chevron's point
        # share TEXT_X (look.py)
        mark = font("KomaWordmark", WORDMARK_SIZE, 0.4)
        self.wordmark = label(NSMakeRect(PAD - LABEL_INSET - bearing(mark), 0, 240, 20))
        self.wordmark.setAttributedStringValue_(text("KOMA", mark, color("ink"), kern=WORDMARK_TRACK * WORDMARK_SIZE))
        self.wordmark.setFrameOrigin_((self.wordmark.frame().origin.x,
                                       WORDMARK_BASELINE - self.wordmark.firstBaselineOffsetFromTop()))
        self.wordmark.setAccessibilityLabel_("Koma")

        self.text_face = font("KomaText", TEXT_SIZE, 0.0)
        self.dot = Dot.alloc().initWithFrame_(NSMakeRect(PAD, STATUS_Y + 10, 6, 6))
        self.dot.setAccessibilityElement_(False)
        self.status_x = TEXT_X - LABEL_INSET - bearing(self.text_face)
        self.status = label(NSMakeRect(self.status_x, STATUS_Y, W - PAD - self.status_x + LABEL_INSET, 44))
        self.status.setMaximumNumberOfLines_(2)
        self.status.cell().setWraps_(True)

        self.details = DetailsLink.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.details.setTarget_(target)
        self.details.setAction_("toggleDetails:")
        self.details.setHidden_(True)
        self.log_link = LogLink.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.log_link.setTarget_(target)
        self.log_link.setAction_("revealLog:")
        self.log_link.setHidden_(True)
        self.log_file = log_file
        self.log_text = home_path(log_file) if log_file else ""

        self.version = label(NSMakeRect(PAD, 0, 160, 16))
        if version:
            small = font("KomaText", SMALL_SIZE, 0.0)
            self.version.setAttributedStringValue_(text(version, small, color("faint")))
            self.version.setAccessibilityLabel_(f"Version {version}")
            # bottom-left, its baseline on the button title's, its first figure's ink on PAD
            baseline = on_grid(look.title_baseline(BUTTON_TOP, BUTTON_H, BUTTON_SIZE))
            self.version.setFrameOrigin_((PAD - LABEL_INSET - bearing(small, version[0]),
                                          baseline - self.version.firstBaselineOffsetFromTop()))
        self.version.setHidden_(not version)

        self.button = QuietButton.alloc().initWithFrame_(NSMakeRect(0, BUTTON_TOP - RING, 129, BUTTON_H + 2 * RING))
        self.button.setBordered_(False)
        self.button.setTarget_(target)
        self.button.setAction_("tryAgain:")
        self.button.setKeyEquivalent_("\r")
        self.button.setHidden_(True)
        self.details.setNextKeyView_(self.log_link)   # hidden views drop out of the loop by themselves
        self.log_link.setNextKeyView_(self.button)

        for sub in (self.wordmark, self.dot, self.status, self.details, self.log_link, self.version, self.button):
            view.addSubview_(sub)
        self.window = win
        self.line: model.WindowLine | None = None
        self.details_open = False

    def update(self, snap: model.Snapshot, announce: bool = True, now: float | None = None):
        line = model.window_line(snap, now)
        if line == self.line:
            return
        changed = self.line is None or line.key != self.line.key
        self.line = line
        self.dot.tone = line.dot
        self.dot.setNeedsDisplay_(True)
        status = text(line.text, self.text_face, color("ink"), line=20)
        self.status.setAttributedStringValue_(status)
        self.status.setAccessibilityLabel_(line.text)
        if line.action:
            title = line.action_text
            width = round(text(title, font("KomaTextMedium", BUTTON_SIZE, 0.23), color("ink")).size().width
                          + 2 * BUTTON_PAD_X)
            self.button.setTitle_(title)
            # the button's outer box ends PAD from the right edge, its bottom BUTTON_MARGIN above the window's
            self.button.setFrame_(NSMakeRect(W - PAD - width - RING, BUTTON_TOP - RING, width + 2 * RING,
                                             BUTTON_H + 2 * RING))
            self.button.setAccessibilityLabel_(title)
        self.button.setHidden_(not line.action)
        if changed:
            self.details_open = False
        self.layout_details(line)
        if changed and announce and self.window.isVisible():
            try:
                from AppKit import (NSAccessibilityAnnouncementKey, NSAccessibilityAnnouncementRequestedNotification,
                                    NSAccessibilityPostNotificationWithUserInfo, NSAccessibilityPriorityKey)
                NSAccessibilityPostNotificationWithUserInfo(
                    self.window, NSAccessibilityAnnouncementRequestedNotification,
                    {NSAccessibilityAnnouncementKey: line.text, NSAccessibilityPriorityKey: 50})
            except Exception:  # noqa: BLE001  (an announcement is a courtesy)
                pass

    def status_lines(self) -> int:
        return 1 if self.status.attributedStringValue().size().width <= self.status.frame().size.width - 4 else 2

    def layout_details(self, line: model.WindowLine | None = None):
        """The Details link 12 pt under the status text's last line (its baseline look.DETAILS_GAP below), the log
        path's link a line under it."""
        line = line or self.line
        show = bool(line and line.details and self.log_text)
        self.details.setHidden_(not show)
        self.log_link.setHidden_(not (show and self.details_open))
        if not show:
            return
        top = STATUS_Y + 20 * self.status_lines() + 12
        self.details.setFrame_(NSMakeRect(TEXT_X - RING, top - RING, self.details.content_width() + 2 * RING,
                                          16 + 2 * RING))
        self.details.set_open(self.details_open)
        width = self.log_link.set_path(line.detail or self.log_text, W - PAD - DETAILS_TEXT_X)  # the USB error, if any
        self.log_link.setFrame_(NSMakeRect(DETAILS_TEXT_X - RING, top + DETAILS_LINE - RING, width + 2 * RING,
                                           16 + 2 * RING))

    def toggle_details(self):
        self.details_open = not self.details_open
        self.layout_details()

    def reveal_log(self) -> bool:
        """The log file selected in a Finder window (its folder when there is no file yet)."""
        if not self.log_file:
            return False
        workspace = NSWorkspace.sharedWorkspace()
        if self.log_file.exists():
            workspace.activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(str(self.log_file))])
            return True
        return bool(workspace.openURL_(NSURL.fileURLWithPath_(str(self.log_file.parent))))

    def show(self):
        if not self.window.isVisible():
            self.window.center()
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def png(self, path: Path):
        view = self.window.contentView()
        rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
        data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        data.writeToFile_atomically_(str(path), True)


def tray_image():
    image = NSImage.alloc().initWithSize_((20, 20))
    for name in ("tray-template.png", "tray-template@2x.png"):
        rep_image = NSImage.alloc().initWithContentsOfFile_(str(paths.ASSETS / name))
        if rep_image:
            for rep in rep_image.representations():
                rep.setSize_((20, 20))
                image.addRepresentation_(rep)
    image.setTemplate_(True)
    image.setAccessibilityDescription_(words.APP)
    return image


class Delegate(NSObject):
    def initWithSupervisor_options_(self, sup, options):
        self = objc.super(Delegate, self).init()
        if self is None:
            return None
        self.sup = sup
        self.options = options
        self.snap = sup.snapshot()
        self.window = None
        self.item = None
        self.menu = None
        self.quitting = False
        self.exited = False
        self.exit_code = 0
        self.last_phase = None
        self.built = None  # the menu entries last built: an open menu is rebuilt only when what it shows changed
        return self

    # life cycle ------------------------------------------------------------

    def applicationDidFinishLaunching_(self, _note):
        register_fonts()
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.item.button()
        button.setImage_(tray_image())
        button.setToolTip_(words.APP)
        button.setAccessibilityLabel_(words.APP)
        self.show_state(self.snap)
        self.menu = NSMenu.alloc().initWithTitle_(words.APP)
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self)
        self.item.setMenu_(self.menu)
        self.build_menu()
        self.window = StartupWindow(self, self.sup.logs / "bridge.log")
        self.window.update(self.snap, announce=False)
        if self.options["first_run"]:
            self.window.show()
        self.sup.changes.add(self.changed)
        if self.options["open_browser"]:
            self.sup.on_ready.append(self.open_url)
        self.sup.on_exit.append(self.supervisor_exited)
        # Python signal handlers run only when Python code runs; this quiet timer gives them a turn, and moves the
        # startup window's elapsed counter while a start or a stop takes long
        self.signal_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self, "tick:", None, True)
        self.sup.start()
        if self.options["self_test"]:
            threading.Thread(target=selftest.run, args=(self.sup, self.selftest_done), daemon=True,
                             name="self-test").start()

    def tick_(self, _timer):
        if self.window is not None and self.snap.phase in ("starting", "stopping"):
            self.window.update(self.snap)

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
        """Koma.app opened again while it runs: open the app in the browser."""
        log.info("opened again while %s", self.snap.phase)
        if self.snap.phase == "running" and self.options["open_browser"]:
            self.open_url(self.snap.address)
        elif self.snap.phase in ("port_in_use", "failed"):
            self.window.show()
        return False

    def applicationShouldTerminate_(self, _app):
        if self.exited:
            return NSTerminateNow
        if not self.quitting:
            self.quitting = True
            self.sup.command("quit")
        return NSTerminateLater

    def applicationWillTerminate_(self, _note):
        logging.shutdown()
        sys.stdout.flush()
        os._exit(self.exit_code)

    # supervisor -> main thread --------------------------------------------

    # Every call from the supervisor's thread to the main thread is posted, never waited for: PyObjC's two-argument
    # pyobjc_performSelectorOnMainThread_withObject_ waits until done, and the main thread itself waits for the
    # supervisor while a menu opens (supervisor.look), which would hold both until the look's timeout.
    @objc.python_method
    def post(self, selector: str, value=None):
        self.pyobjc_performSelectorOnMainThread_withObject_waitUntilDone_(selector, value, False)

    @objc.python_method
    def changed(self, snap):
        self.post("refresh:", snap)

    @objc.python_method
    def show_state(self, snap):
        """The K dims while Koma is not running; VoiceOver hears the menu's first line as the item's value, so the
        dimming is not the only sign."""
        button = self.item.button()
        button.setAppearsDisabled_(model.dimmed(snap))
        button.setAccessibilityValue_(model.menu(snap)[0].text)

    def refresh_(self, snap):
        self.snap = snap
        self.show_state(snap)
        self.build_menu()
        self.window.update(snap)
        if snap.phase != self.last_phase and snap.phase in ("port_in_use", "failed"):
            self.window.show()
        self.last_phase = snap.phase
        usb = model.usb_line_key(snap)   # USB not usable, or the udev rule missing: shown once, when it appears
        if usb and usb != getattr(self, "last_usb", ""):
            self.window.show()
        self.last_usb = usb

    @objc.python_method
    def supervisor_exited(self):
        self.post("exited:")

    def exited_(self, _):
        self.exited = True
        if self.quitting:
            NSApp.replyToApplicationShouldTerminate_(True)
        elif not self.options["self_test"]:  # the self-test ends the app itself, once its report is out
            NSApp.terminate_(None)

    @objc.python_method
    def selftest_done(self, report):
        print(selftest.dumps(report), flush=True)
        self.exit_code = 0 if report.get("ok") else 1
        self.post("finish:")

    def finish_(self, _):
        self.exited = True
        NSApp.terminate_(None)

    # menu ------------------------------------------------------------------

    @objc.python_method
    def build_menu(self):
        entries = model.menu(self.snap)
        if entries == self.built:
            return
        self.built = entries
        menu = self.menu
        menu.removeAllItems()
        for entry in entries:
            if entry.kind == "separator":
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            action = ACTIONS.get(entry.action) if entry.kind == "action" else None
            key = "q" if entry.action == "quit" else ""
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(entry.text, action, key)
            if action:
                item.setTarget_(self)
            item.setEnabled_(bool(action) and entry.enabled)
            if entry.help:
                item.setToolTip_(entry.help)
                if item.respondsToSelector_("setSubtitle:"):
                    item.setSubtitle_(entry.help)
            menu.addItem_(item)

    def menuNeedsUpdate_(self, _menu):
        """The menu is about to open: a look. The camera line is read now (a short wait, so it is usually in the menu
        as it appears) and while the menu stays open; it goes when the menu closes."""
        self.snap = self.sup.look(True, wait=0.3)
        self.build_menu()

    def menuDidClose_(self, _menu):
        self.sup.look(False)

    # actions ---------------------------------------------------------------

    @objc.python_method
    def open_url(self, url):
        def go(_=None):
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
        if threading.current_thread() is threading.main_thread():
            go()
        else:
            self.post("openOnMain:", url)

    def openOnMain_(self, url):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))

    def openKoma_(self, _sender):
        self.open_url(self.snap.address)

    def copyAddress_(self, _sender):
        board = NSPasteboard.generalPasteboard()
        board.clearContents()
        board.setString_forType_(self.snap.address, NSPasteboardTypeString)

    def tryAgain_(self, _sender):
        line = self.window.line
        self.sup.command(line.action if line and line.action else "retry")

    def toggleDetails_(self, _sender):
        self.window.toggle_details()

    def revealLog_(self, _sender):
        self.window.reveal_log()

    def quitKoma_(self, _sender):
        NSApp.terminate_(None)


def run(sup, open_browser: bool = True, first_run: bool = False, self_test: bool = False) -> int:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = Delegate.alloc().initWithSupervisor_options_(
        sup, {"open_browser": open_browser, "first_run": first_run, "self_test": self_test})
    app.setDelegate_(delegate)

    def on_signal(_signum, _frame):
        NSApp.terminate_(None)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    app.run()
    return delegate.exit_code


# ------------------------------------------------------------------------------------------------ screens

def render_screens(out: str, port: int = 8765) -> int:
    """The startup window in each state, dark and light, as PNG files (the content as drawn; the system adds the
    close button at the top left). Plus menus.txt: the menu's lines in each state, as model.menu() gives them. While
    no version is set, startup-failed-version-sample-*.png draws the version slot with screens.SAMPLE_VERSION, for
    review."""
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    register_fonts()
    log_file = Path.home() / "Library" / "Logs" / paths.APP_DIR_NAME / "bridge.log"
    window = StartupWindow(None, log_file)
    sample = None if VERSION else StartupWindow(None, log_file, version=screens.SAMPLE_VERSION)
    states = screens.window_states(port, "darwin")
    for appearance, tag in ((NSAppearanceNameDarkAqua, "dark"), (NSAppearanceNameAqua, "light")):
        for win, names, suffix in ((window, list(states), ""), (sample, ["failed"], "-version-sample")):
            if win is None:
                continue
            win.window.setAppearance_(NSAppearance.appearanceNamed_(appearance))
            for name in names:
                snap, now, details = states[name]
                win.line = None
                win.update(snap, announce=False, now=now)
                if details:
                    win.toggle_details()
                win.png(folder / f"startup-{name}{suffix}-{tag}.png")
    (folder / "menus.txt").write_text(screens.menus_text(port, looks=True, quit_key="⌘Q"), encoding="utf-8")
    return 0
