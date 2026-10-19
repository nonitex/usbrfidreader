# usbrfidreader

Linux command-line tool to configure and read the cheap **Sycreader-type USB keyboard RFID reader** (USB ID `ffff:0035`) — no Windows vendor tool needed.

These readers act as a USB keyboard and "type" the card number. Out of the box they output only 10 decimal digits (the low 4 bytes of the ID). With this tool you can switch them to e.g. 10 hex digits (the full 5-byte ID), change the suffix characters (Enter, `;`, `,`, `?`), or read card IDs directly without them being typed into the focused window.

The configuration protocol was reverse-engineered from the vendor's Windows tool (`DEMO.exe` + `usb.dll`).

## Requirements

- Linux, Python 3
- [`python-evdev`](https://pypi.org/project/evdev/) for the `read` command only (`pip install evdev` or `sudo apt install python3-evdev`)

## Permissions (optional)

To use the reader without `sudo`, install the udev rule:

```sh
sudo cp 70-usb-rfid-reader.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
# then re-plug the reader
```

## Usage

```sh
./rfid.py info            # show the device and its config-interface descriptor
./rfid.py get             # show the current output format
./rfid.py set 10h         # switch to 10 hex digits (= the full 5-byte ID)
./rfid.py set 10d --enter # factory default format, with Enter suffix
./rfid.py read            # print card IDs as bytes instead of typing them
```

Supported formats (`./rfid.py set --help`):

| name       | output                                        |
|------------|-----------------------------------------------|
| `10h`      | 10 hex digits (5 bytes, full ID)              |
| `8h`       | 8 hex digits (last 4 bytes)                   |
| `8h-rev`   | 8 hex digits, reversed                        |
| `10d`      | 10 decimal digits (last 4 bytes) — factory default |
| `10d-rev`  | 10 decimal digits, reversed                   |
| `13d`      | 13 decimal digits                             |
| `18d`      | 18 decimal digits                             |
| `8d`       | 8 decimal digits                              |
| `8d-4b`    | 8 decimal digits (last 4 bytes)               |
| `8d-3b`    | 8 decimal digits (last 3 bytes)               |
| `00+8d-3b` | 00 + 8 decimal digits (last 3 bytes)          |
| `5d`       | 5 decimal digits                              |
| `2h4d`     | 2H4D + 2H4D                                   |

Suffix flags: `--enter`, `--semicolon`, `--comma`, `--question` (and their `--no-…` forms).

## Protocol notes

The reader has two HID interfaces: interface 0 is the keyboard, interface 1 is a vendor config channel driven with HID feature reports (`SET_REPORT` / `GET_REPORT`, report ID 1, 256 bytes).
Each report is `01 00 00 00 00 00 <len> 00` followed by a frame:

```
AA <addr> <n> <payload…> <bcc> BB      bcc = XOR of addr, n and payload
```

| command | payload                                                        |
|---------|----------------------------------------------------------------|
| `0x83`  | get format → `00 ?? <fmt> <;> <?> <,> <enter>`                 |
| `0x82`  | set format: `82 <fmt> <;> <?> <,> <enter> 00 00 00`            |

## License

MIT
