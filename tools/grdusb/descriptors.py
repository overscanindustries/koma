"""The USB identity recovered from firmware, and the gate that must pass before any command is sent."""
from __future__ import annotations

from dataclasses import dataclass, field

VENDOR = 0x05ca
PRODUCT_ORIGINAL = 0x2218
OTHER_MODES = {
    0x0109: "mass-storage mode: macOS should mount the card as a disk; this tool has nothing to do here",
    0x0331: "PTP/still-image mode: this tool does not speak PTP",
}
EXPECTED_MANUFACTURER = "Ricoh Company Ltd."
EXPECTED_PRODUCT = "GR DIGITAL"
BULK_IN, BULK_OUT, INTERRUPT_IN = 0x81, 0x02, 0x83


@dataclass(frozen=True)
class Endpoint:
    address: int
    attributes: int
    max_packet: int


@dataclass(frozen=True)
class Interface:
    number: int
    klass: int
    subclass: int
    protocol: int
    endpoints: tuple[Endpoint, ...]


@dataclass(frozen=True)
class Configuration:
    value: int
    interfaces: tuple[Interface, ...]


@dataclass(frozen=True)
class Device:
    vendor: int
    product: int
    bcd_usb: int
    num_configurations: int
    configurations: tuple[Configuration, ...] = field(default_factory=tuple)
    location: str = ""


def describe(device: Device) -> list[str]:
    lines = [f"{device.vendor:04x}:{device.product:04x} bcdUSB={device.bcd_usb:04x} "
             f"configurations={device.num_configurations} {device.location}".rstrip()]
    for config in device.configurations:
        for interface in config.interfaces:
            lines.append(f"  config {config.value} interface {interface.number} class "
                         f"{interface.klass:02x}/{interface.subclass:02x}/{interface.protocol:02x}")
            lines += [f"    endpoint {e.address:#04x} attributes={e.attributes:#04x} max_packet={e.max_packet}"
                      for e in interface.endpoints]
    return lines


def mode_note(device: Device) -> str:
    if device.vendor != VENDOR:
        return "not a Ricoh vendor ID"
    if device.product == PRODUCT_ORIGINAL:
        return "matches the firmware's ORIGINAL-mode identity (descriptors not yet compared)"
    return OTHER_MODES.get(device.product, "unknown Ricoh product ID; firmware does not list it for this model")


def mismatches(device: Device) -> list[str]:
    """Every difference from the recovered ORIGINAL-mode descriptors. Empty means the gate passes."""
    problems = []
    if device.vendor != VENDOR:
        problems.append(f"vendor {device.vendor:04x} != {VENDOR:04x}")
    if device.product != PRODUCT_ORIGINAL:
        problems.append(f"product {device.product:04x} != {PRODUCT_ORIGINAL:04x} ({mode_note(device)})")
    if device.bcd_usb != 0x0200:
        problems.append(f"bcdUSB {device.bcd_usb:04x} != 0200")
    if device.num_configurations != 2:
        problems.append(f"configurations {device.num_configurations} != 2")
    if not device.configurations:
        return problems + ["no configuration descriptor available"]
    config = device.configurations[0]
    if config.value != 1:
        problems.append(f"first configuration value {config.value} != 1")
    if len(config.interfaces) != 1:
        return problems + [f"interface count {len(config.interfaces)} != 1"]
    interface = config.interfaces[0]
    if (interface.klass, interface.subclass, interface.protocol) != (0xff, 0, 0):
        problems.append(f"interface class {interface.klass:02x}/{interface.subclass:02x}/{interface.protocol:02x}"
                        " != ff/00/00")
    endpoints = {e.address: e for e in interface.endpoints}
    if set(endpoints) != {BULK_IN, BULK_OUT, INTERRUPT_IN}:
        problems.append(f"endpoints {sorted(hex(a) for a in endpoints)} != 0x2, 0x81, 0x83")
        return problems
    for address in (BULK_IN, BULK_OUT):
        if endpoints[address].attributes != 2 or endpoints[address].max_packet not in (64, 512):
            problems.append(f"endpoint {address:#04x} is not bulk with a 64 or 512 byte packet")
    if endpoints[INTERRUPT_IN].attributes != 3 or endpoints[INTERRUPT_IN].max_packet != 64:
        problems.append("endpoint 0x83 is not interrupt with a 64 byte packet")
    return problems


def string_mismatches(manufacturer: str | None, product: str | None) -> list[str]:
    problems = []
    if manufacturer != EXPECTED_MANUFACTURER:
        problems.append(f"manufacturer string {manufacturer!r} != {EXPECTED_MANUFACTURER!r}")
    if product != EXPECTED_PRODUCT:
        problems.append(f"product string {product!r} != {EXPECTED_PRODUCT!r}")
    return problems
