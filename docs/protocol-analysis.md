# Protocol analysis — OpenRun Pro 2 management protocol

## Reference

| | |
|---|---|
| Repository | https://codeberg.org/Freeyourgadget/Gadgetbridge |
| Commit | `37f76243b7e78632ea3ea338f59f8318ecf9d417` (2026-11-12, shallow clone) |
| License | AGPL-3.0 (`LICENSE` at that commit). shokzctl is AGPL-3.0-or-later for this reason |
| Checked by | source reading on 2026-11-13; no captures, no hardware traffic |

Paths below are relative to `app/src/main/java/nodomain/freeyourgadget/gadgetbridge/` unless they start with `app/`. Abbreviations: **Support** = `service/devices/shokz/ShokzSupport.kt`, **Cmd** = `service/devices/shokz/ShokzCommand.kt`, **Args** = `service/devices/shokz/ShokzArguments.kt`, **Coord** = `devices/shokz/ShokzCoordinator.kt`, **Pro2** = `devices/shokz/ShokzOpenRunPro2Coordinator.kt`.

### Evidence tiers

1. **Vendor claim**: Shokz documentation or app UI.
2. **Source fact**: what the pinned code does. *Model-specific* when it sits in the Pro 2 coordinator; *shared* when it applies to every Shokz model Gadgetbridge supports.
3. **Upstream-tested**: the code carries a comment saying it was confirmed on a real Pro 2, or a unit-test vector exists. We haven't reproduced either.
4. **Offline tested here**: our tests pass against tier-2/3 evidence.
5. **Hardware verified here**: observed on Markos's headset from Fedora, recorded in `test-results.md`.

Tier 5 so far: the management channel, firmware read, battery read and EQ read (2026-11-13, `test-results.md` M3).

## Transport

- **Bluetooth Classic RFCOMM**, not BLE. The coordinator extends `AbstractBLClassicDeviceCoordinator` (Coord:29) and the support class extends `AbstractHeadphoneBTBRDeviceSupport` (Support:38).
- **Service UUID `0000fef0-0000-1000-8000-00805f9b34fb`** (Support:967-968), registered as the supported service (Support:57-59).
- **Channel resolved through SDP.** Shokz doesn't override the RFCOMM channel, so the queue calls `createRfcommSocketToServiceRecord(uuid)` (`service/btbr/BtBRQueue.java:329-342`). That is an authenticated, encrypted RFCOMM socket whose channel comes from the headset's SDP record for the UUID. There's no hard-coded channel.
- On Fedora, BlueZ lists `0xFEF0` among this headset's service UUIDs (`diagnose`, 2026-11-12), so the SDP record is visible here. Gadgetbridge notes it doesn't show up in Android's discovered services (Support:967).
- No authentication or handshake beyond Bluetooth bonding. The first bytes on the socket are a normal `FIRMWARE_GET` (Support:82-86).

## Framing

Source: encoder Support:970-998, receive path Support:114-187, payload decode Support:333-391. All integers little-endian.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 2 | Preamble `A5 5A` | `0x5aa5` written LE (Support:964) |
| 2 | 2 | Frame length after byte 8 | Encoder writes payload length + 4 |
| 4 | 4 | Header flags | Encoder writes `01 00 00 00`. Decoder comments: byte 4 "always 1?", byte 5 "isAck?", bytes 6-7 "always 0?" (Support:140-142). **Observed on Pro 2:** every reply carried `01 01 00 00`, so byte 5 = 1 marks a reply. Not used for matching |
| 8 | 2 | Payload length | |
| 10 | 2 | CRC-16/MAXIM of the payload | Reflected poly 0x8005, init 0, xorout 0xFFFF (`util/CheckSums.java:223-241`) |
| 12 | n | Payload | TLV-like, all u32: `1, len(rest), 2, 4, group, 3, 4, code, 4, len(args), args` |

- Upstream unit-test vector for `FIRMWARE_GET`: `app/src/test/java/nodomain/freeyourgadget/gadgetbridge/service/devices/shokz/ShokzSupportTest.kt:9-12`. Our encoder reproduces it byte for byte (`tests/test_protocol.py`).
- Receive buffer is 2048 bytes (Support:963).
- **CRC quirk:** on the Pro 2, `MULTIPOINT_DEVICES_RET` arrives with a wrong CRC when more than two peers are remembered; Gadgetbridge accepts that one reply type anyway (Support:155-171, upstream-tested). Every other bad-CRC frame is dropped.

## Groups, replies and sequencing

- Group `0x01` = set, `0x02` = get, `0x03` = sent unprompted by the headset (Cmd).
- A reply has the request's group and its code with bit 15 set (`MASK_RESPONSE`, Support:965). Every get/set pair in Cmd follows this.
- **Gadgetbridge compares the code only** (Support:388-390). `LANGUAGE_ACK` (`0x01`/`0x8002`) and `FIRMWARE_RET` (`0x02`/`0x8002`) share a code, so it could take one for the other. **shokzctl matches group and code.**
- One request at a time. The comment at Support:82 says the headset doesn't answer everything otherwise; the queue waits for each reply (Support:752-796).
- Timeout 2 s, then **resends the same message up to 3 times** (Support:773-783, 810). That includes writes. **shokzctl won't resend writes;** after an uncertain write it reads the state back first.
- Pro 2 initial sequence (Support:70-103 with the Pro 2's `supportsMp3 = false`, `supportsControls = false`): `FIRMWARE_GET`, then `BATTERY_GET`, `EQUALIZER_GET`, `PLAYBACK_STATUS_GET`, `VOLUME_GET`, `LANGUAGE_GET`.

## Operations

| Operation | Scope | Request → reply | Source | Tier | Open questions |
|---|---|---|---|---|---|
| Firmware read | Shared | `0x02/0x0002`, no args → `0x02/0x8002`: `00`, then NUL-terminated string | Cmd:26-27; Support:83, 399-417; test vector ShokzSupportTest.kt:9-12 | 2, 3 (encoding), 4, **5** | Resolved: headset reports `BOS_HU_R_47_20251023`, the app shows it without `BOS_`. Reply args are padded with 13 zero bytes after the NUL |
| Battery read | Shared | `0x02/0x0003` → `0x02/0x8003`: `00`, level byte *b*; percent = (*b* + 1) × 10 | Cmd:7-8; Support:442-457 | 2, 4, **5** | Scale matched BlueZ once (byte 5 → 60 %). Reply args were `00 05 ff 00`: meaning of `ff 00` unknown, ignored. Headset sends no battery notifications (Coord:42-46) |
| EQ read | Shared | `0x02/0x0008` → `0x02/0x8008`: `00`, preset code; if custom, 5 signed band bytes follow | Cmd:23-24; Support:459-507 | 2, 4, **5** | Standard read back as code 1 with args `00 01` + six zero bytes; whether those six carry tuning or custom bands is open |
| EQ change notice | Shared | Headset sends `0x03/0x0009`: preset code (no status byte). Gadgetbridge calls it `EQUALIZER_ACK` | Cmd:22; Support:459-507 | 2, 4 | Sent after our own writes, after button changes, or both? |
| EQ set acknowledgement | Shared | `0x01/0x800e` (`EQ_UNK_ACK_1`); args not decoded; Gadgetbridge logs it as unhandled but it releases the queue | Cmd:21; Support:388-390, 748 | 2 | Args meaning; does it mean accepted or just received? |
| Standard preset | **Pro 2** | `0x01/0x000e` args `01 00 00 00 00 00 00 00` | Pro2:49-53 | 2, 3 ("captured from a live HCI snoop of the official app"), 4 | Readback, audible effect, persistence |
| Vocal preset | **Pro 2** | args `02 fa fe 04 05 01 00 00` (differs from other models: Coord:100-101) | Pro2:55-56 | 2, 3, 4 | Same |
| Bass Boost preset | **Pro 2** | args `03 09 f9 00 00 00 00 00` | Pro2:58-59, 33-36 | 2, 3, 4 | Same |
| Treble Boost preset | **Pro 2** | args `04 00 00 01 04 05 00 00` | Pro2:61-62, 33-36 | 2, 3, 4 | Same |
| Custom EQ | **Pro 2** flag; encoding shared | args `05 b1 b2 b3 b4 b5 00 00`, each band −5…5 | Pro2:38-41; Coord:109-117, 256-264; Args:29 | 2, 3 (flag), 4 | Implemented (`eq custom`), unverified. Band frequencies, step units, slots, persistence open. Writing it overwrites the stored custom profile |
| Classic / Volume Boost | **Pro 2** flag | codes `0x08` / `0x0a`, args code + seven `00` (shared default, not Pro 2-specific bytes) | Pro2:43-47; Coord:81-98; Args:32-33 | 2, 3 (flag only) | Pro 2 args were not captured separately. Out of scope until evidenced |
| Multipoint status | Shared | `0x02/0x0010` → `0x02/0x8010`: u32, `0x00010100` = on. No source names an "off" value; shokzctl reports others as unknown | Cmd:61-62; Support:634-639 | 2, 4 | Implemented, unverified. Observe the off value at test C2 |
| Multipoint peer list | Shared, Pro 2 quirk | `0x02/0x0004` → `0x02/0x8004`: `00`, slot count, then per entry index, address (reversed), connected, name length, name. Bad CRC with >2 peers; count over-reports; garbled tail | Cmd:47; Support:155-171, 667-718 | 2, 3 (quirks), 4 | Implemented, unverified. Bad CRC accepted for this reply only |
| Multipoint on / off | Shared | ON `0x01/0x0011` no args → `0x01/0x8011` u32 status (0 = ok). OFF `0x01/0x0010` args = the asking device's own address, reversed → `0x01/0x8010` | Support:641-665, 841-866 | 2, 4 | Implemented, unverified. Off sends Fedora's adapter address |
| Connect / disconnect a remembered device | Shared | `0x01/0x000b` / `0x01/0x000c`, args = address reversed → `0x01/0x800b` / `0x01/0x800c` u32 status; `0x400` = too many connected | Support:719-736, 905-918 | 2, 4 | Implemented, unverified. shokzctl never targets this computer's own address |
| Multipoint pairing mode | Shared | `0x01/0x001d`, `0x01/0x0023` (no reply waited) | Support:921-928 | 2 | Not implemented: out of scope |
| Button controls | Not on Pro 2 | `CONTROLS_GET` goes unanswered on Pro 2 | Pro2:28-31; Cmd:10 | 3 | Don't send |

## Where shokzctl differs from Gadgetbridge on purpose

- Replies are matched on group and code, not code alone.
- Writes are never resent blindly. Timeout on a write leaves the outcome uncertain until an `EQUALIZER_GET` confirms it.
- Frames whose two length fields disagree are treated as noise; the decoder rescans for the next `A5 5A` one byte at a time instead of skipping two bytes. All three Pro 2 replies seen so far satisfy *frame length = payload length + 4*.

## Transport (implemented, verified at M3)

Use BlueZ's profile API rather than opening RFCOMM by channel number, so BlueZ does the SDP lookup and we never guess a channel:

1. Export an `org.bluez.Profile1` object on our system-bus connection.
2. `ProfileManager1.RegisterProfile(path, "0000fef0-0000-1000-8000-00805f9b34fb", {Role: "client", AutoConnect: false, RequireAuthentication: true})`.
3. `Device1.ConnectProfile("0000fef0-…")`. BlueZ looks up the channel and passes us the socket in `Profile1.NewConnection(device, fd, props)`.
4. Close the fd and call `DisconnectProfile`, then `UnregisterProfile`, on exit or failure.

Verified 2026-11-13: BlueZ resolved the channel from SDP and delivered the socket; first open 2.08 s, later ones about 0.5 s. Code: `src/shokzctl/transport/management.py`.

## First hardware action (done, M3)

`FIRMWARE_GET` alone, then `FIRMWARE_GET`, `BATTERY_GET`, `EQUALIZER_GET`. All passed; details in `test-results.md`.

## Write frames for the remaining hardware tests

The test sequence is in `hardware-test-plan.md`. Every write is implemented as: read, send once, read back. A write is confirmed only by the readback; the `0x01/0x800e` acknowledgement counts as "received". Nothing is resent.

```text
EQUALIZER_SET Vocal:    a55a34000100000030006d10010000002800000002000000040000000100000003000000040000000e000000040000000800000002fafe0405010000
EQUALIZER_SET Standard: a55a3400010000003000c218010000002800000002000000040000000100000003000000040000000e00000004000000080000000100000000000000
EQUALIZER_SET Bass:     a55a3400010000003000cea8010000002800000002000000040000000100000003000000040000000e00000004000000080000000309f90000000000
EQUALIZER_SET Treble:   a55a34000100000030002ed6010000002800000002000000040000000100000003000000040000000e00000004000000080000000400000104050000
EQUALIZER_SET custom +2 0 0 0 0:
                        a55a3400010000003000e02b010000002800000002000000040000000100000003000000040000000e00000004000000080000000502000000000000
MULTIPOINT_GET:         a55a2c00010000002800628f01000000200000000200000004000000020000000300000004000000100000000400000000000000
MULTIPOINT_DEVICES_GET: a55a2c0001000000280052bf01000000200000000200000004000000020000000300000004000000040000000400000000000000
MULTIPOINT_ON:          a55a2c000100000028007bb301000000200000000200000004000000010000000300000004000000110000000400000000000000
```

`MULTIPOINT_OFF`, connect and disconnect carry a Bluetooth address, so their frames aren't written here; the trace records them.
