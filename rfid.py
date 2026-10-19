#!/usr/bin/env python3
"""Configure and read a Sycreader-type USB keyboard RFID reader (ffff:0035).

The reader has two HID interfaces: 0 is the keyboard that types the card
number, 1 is a vendor config channel driven with feature reports. The protocol
below was taken from the vendor's Windows tool (DEMO.exe + usb.dll).

  rfid.py info            show the device and its config-interface descriptor
  rfid.py get             show the current output format
  rfid.py set 10h         switch to 10 hex digits (= the full 5 byte ID)
  rfid.py read            print card IDs instead of letting them be typed
"""
import argparse
import ctypes
import fcntl
import glob
import os
import select
import sys
import time

VID, PID = 0xFFFF, 0x0035
CFG_IFACE = 1
REPORT_LEN = 256

CMD_SET_FORMAT = 0x82
CMD_GET_FORMAT = 0x83

# name -> (code, description as shown in the vendor tool)
FORMATS = {
    "10h": (0x07, "10 hex digits (5 bytes, full ID)"),
    "8h": (0x0C, "8 hex digits (last 4 bytes)"),
    "8h-rev": (0x03, "8 hex digits, reversed"),
    "10d": (0x0F, "10 decimal digits (last 4 bytes) [factory default]"),
    "10d-rev": (0x0A, "10 decimal digits, reversed"),
    "13d": (0x05, "13 decimal digits"),
    "18d": (0x06, "18 decimal digits"),
    "8d": (0x09, "8 decimal digits"),
    "8d-4b": (0x0E, "8 decimal digits (last 4 bytes)"),
    "8d-3b": (0x0B, "8 decimal digits (last 3 bytes)"),
    "00+8d-3b": (0x0D, "00 + 8 decimal digits (last 3 bytes)"),
    "5d": (0x08, "5 decimal digits"),
    "2h4d": (0x00, "2H4D + 2H4D"),
}
FORMAT_BY_CODE = {code: (name, desc) for name, (code, desc) in FORMATS.items()}
# suffix flags, in the order the reader stores them after the format byte
FLAGS = ("semicolon", "question", "comma", "enter")


class CtrlTransfer(ctypes.Structure):
    _fields_ = [
        ("bRequestType", ctypes.c_uint8),
        ("bRequest", ctypes.c_uint8),
        ("wValue", ctypes.c_uint16),
        ("wIndex", ctypes.c_uint16),
        ("wLength", ctypes.c_uint16),
        ("timeout", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
    ]


# _IOWR('U', 0, struct usbdevfs_ctrltransfer)
USBDEVFS_CONTROL = (3 << 30) | (ctypes.sizeof(CtrlTransfer) << 16) | (ord("U") << 8)


def find_usb_node():
    for dev in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        base = os.path.dirname(dev)
        try:
            vid = int(open(dev).read(), 16)
            pid = int(open(base + "/idProduct").read(), 16)
        except (OSError, ValueError):
            continue
        if (vid, pid) == (VID, PID):
            bus = int(open(base + "/busnum").read())
            num = int(open(base + "/devnum").read())
            return "/dev/bus/usb/%03d/%03d" % (bus, num)
    sys.exit("reader %04x:%04x not found - is it plugged in?" % (VID, PID))


# low bits of bmRequestType; a browser (WebUSB) may only use device/other here
RECIPIENTS = {"interface": 1, "device": 0, "other": 3}
# request type bits; Chrome blocks "class" for HID devices but lets "vendor" through
TYPES = {"class": 0x20, "vendor": 0x40}


class Reader:
    def __init__(self, addr=0, recipient="interface", reqtype="class"):
        self.addr = addr
        self.rcpt = RECIPIENTS[recipient] | TYPES[reqtype]
        self.node = find_usb_node()
        try:
            self.fd = os.open(self.node, os.O_RDWR)
        except PermissionError:
            sys.exit(
                "no permission for %s - run with sudo, or install the udev rule "
                "(see 70-usb-rfid-reader.rules)" % self.node
            )

    def control(self, reqtype, request, value, index, data):
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        xfer = CtrlTransfer(
            reqtype, request, value, index, len(data), 1000, ctypes.addressof(buf)
        )
        try:
            n = fcntl.ioctl(self.fd, USBDEVFS_CONTROL, xfer)
        except OSError as e:
            sys.exit("reader refused request 0x%02x/0x%02x: %s" % (reqtype, request, e.strerror))
        return buf.raw[:n]

    def report_descriptor(self):
        # GET_DESCRIPTOR(report) addressed to the config interface
        return self.control(0x81, 0x06, 0x2200, CFG_IFACE, bytes(255))

    def frame(self, payload):
        body = bytes([self.addr, len(payload)]) + bytes(payload)
        bcc = 0
        for b in body:
            bcc ^= b
        return b"\xaa" + body + bytes([bcc]) + b"\xbb"

    def command(self, payload):
        """Send one command, return the payload of the answer (status first)."""
        frame = self.frame(payload)
        # feature report 1: id, 5 zero bytes, frame length (LE16), frame
        report = bytes([1, 0, 0, 0, 0, 0, len(frame), 0]) + frame
        report = report.ljust(REPORT_LEN, b"\0")
        self.control(self.rcpt, 0x09, 0x0301, CFG_IFACE, report)  # SET_REPORT
        raw = b""
        for _ in range(20):
            time.sleep(0.05)
            raw = self.control(0x80 | self.rcpt, 0x01, 0x0302, CFG_IFACE, bytes(REPORT_LEN))
            answer = raw[8:]
            if len(answer) > 4 and answer[0] == 0xAA:
                n = answer[2]
                if len(answer) >= n + 5 and answer[n + 4] == 0xBB:
                    return answer[3 : 3 + n]
        sys.exit("no valid answer from reader, last report: " + raw[:32].hex(" "))

    def get_config(self):
        p = self.command([CMD_GET_FORMAT])
        if len(p) < 7 or p[0] != 0:
            sys.exit("reading the format failed, answer: " + p.hex(" "))
        cfg = {"format": p[2]}
        cfg.update({name: bool(p[3 + i]) for i, name in enumerate(FLAGS)})
        return cfg

    def set_config(self, cfg):
        payload = [CMD_SET_FORMAT, cfg["format"]]
        payload += [int(cfg[name]) for name in FLAGS] + [0, 0, 0]
        p = self.command(payload)
        if not p or p[0] != 0:
            sys.exit("setting the format failed, answer: " + p.hex(" "))


def describe(cfg):
    name, desc = FORMAT_BY_CODE.get(cfg["format"], ("?", "unknown"))
    flags = ", ".join(f for f in FLAGS if cfg[f]) or "none"
    return "format 0x%02X %s: %s; suffixes: %s" % (cfg["format"], name, desc, flags)


def cmd_info(args):
    r = Reader(args.addr)
    print("device:", r.node)
    print("config interface report descriptor:", r.report_descriptor().hex(" "))


def cmd_get(args):
    print(describe(Reader(args.addr, args.recipient, args.type).get_config()))


def cmd_set(args):
    r = Reader(args.addr, args.recipient, args.type)
    cfg = r.get_config()
    print("before:", describe(cfg))
    cfg["format"] = FORMATS[args.format][0]
    for name in FLAGS:
        value = getattr(args, name)
        if value is not None:
            cfg[name] = value
    r.set_config(cfg)
    after = r.get_config()
    print("after: ", describe(after))
    if after != cfg:
        sys.exit("reader did not take the new settings")


def cmd_read(args):
    import evdev
    from evdev import ecodes

    keys = {getattr(ecodes, "KEY_%s" % c): c for c in "0123456789ABCDEF"}
    keys.update({ecodes.KEY_SEMICOLON: ";", ecodes.KEY_COMMA: ",", ecodes.KEY_SLASH: "?"})

    dev = None
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        if (d.info.vendor, d.info.product) == (VID, PID):
            dev = d
            break
    if dev is None:
        sys.exit(
            "reader input device not found or not accessible - run with sudo, "
            "or install the udev rule"
        )

    dev.grab()  # keep the card number out of whatever window has focus
    print("waiting for cards on %s (Ctrl+C to quit)" % dev.path, file=sys.stderr)
    text = ""
    try:
        while True:
            # without the "enter" suffix a short pause ends the number
            ready, _, _ = select.select([dev.fd], [], [], 0.2 if text else None)
            done = not ready
            if ready:
                for ev in dev.read():
                    if ev.type != ecodes.EV_KEY or ev.value != 1:
                        continue
                    if ev.code in (ecodes.KEY_ENTER, ecodes.KEY_KPENTER):
                        done = True
                    elif ev.code in keys:
                        text += keys[ev.code]
            if done and text:
                print_card(text, args.decimal)
                text = ""
    except KeyboardInterrupt:
        pass
    finally:
        dev.ungrab()


def print_card(text, decimal):
    try:
        if decimal:
            # 10D only carries the low 4 bytes, the top byte never leaves the reader
            data = int(text).to_bytes(4, "big")
        else:
            data = bytes.fromhex(text)
    except (ValueError, OverflowError):
        print(text, flush=True)
        return
    print(data.hex(" ").upper(), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--addr", type=lambda s: int(s, 16), default=0, help="station address, hex (default 00)"
    )
    ap.add_argument(
        "--recipient",
        choices=RECIPIENTS,
        default="interface",
        help="USB request recipient for get/set (to test what WebUSB could send)",
    )
    ap.add_argument(
        "--type", choices=TYPES, default="class", help="USB request type for get/set"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="show device and config descriptor").set_defaults(func=cmd_info)
    sub.add_parser("get", help="show current output format").set_defaults(func=cmd_get)

    p = sub.add_parser(
        "set",
        help="change output format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="formats:\n"
        + "\n".join("  %-9s %s" % (n, d) for n, (_, d) in FORMATS.items()),
    )
    p.add_argument("format", choices=FORMATS, metavar="FORMAT")
    for name in FLAGS:
        p.add_argument(
            "--" + name,
            action=argparse.BooleanOptionalAction,
            help="append this after the number (default: keep current)",
        )
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("read", help="print card IDs as bytes")
    p.add_argument(
        "--decimal", action="store_true", help="reader is still in a decimal format (4 bytes only)"
    )
    p.set_defaults(func=cmd_read)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
