# Protocol analysis — OpenRun Pro 2 management protocol

## Reference

| | |
|---|---|
| Repository | https://codeberg.org/Freeyourgadget/Gadgetbridge |
| Commit | `37f76243b7e78632ea3ea338f59f8318ecf9d417` (2026-11-12, shallow clone) |
| License | AGPL-3.0 (`LICENSE` at that commit). shokzer is AGPL-3.0-or-later for this reason |
| Checked by | source reading on 2026-11-13; no captures, no hardware traffic |

Paths below are relative to `app/src/main/java/nodomain/freeyourgadget/gadgetbridge/` unless they start with `app/`. Abbreviations: **Support** = `service/devices/shokz/ShokzSupport.kt`, **Cmd** = `service/devices/shokz/ShokzCommand.kt`, **Args** = `service/devices/shokz/ShokzArguments.kt`, **Coord** = `devices/shokz/ShokzCoordinator.kt`, **Pro2** = `devices/shokz/ShokzOpenRunPro2Coordinator.kt`.

### Evidence tiers

1. **Vendor claim**: Shokz documentation or app UI.
2. **Source fact**: what the pinned code does. *Model-specific* when it sits in the Pro 2 coordinator; *shared* when it applies to every Shokz model Gadgetbridge supports.
3. **Upstream-tested**: the code carries a comment saying it was confirmed on a real Pro 2, or a unit-test vector exists. We haven't reproduced either.
4. **Offline tested here**: our tests pass against tier-2/3 evidence.
5. **Hardware verified here**: observed on the maintainer's headset from Fedora, recorded in `test-results.md`.

Tier 5 (hardware verified on firmware `BOS_HU_R_47_20251023`, 2026-11-13): the management channel, firmware, battery and EQ reads, all four presets, custom EQ, multipoint status, on/off, and connecting/disconnecting a remembered device. Details in `test-results.md`.

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
| 2 | 2 | Frame length after byte 8 | Payload length + 4 in a whole frame or first fragment; just the data length in a later fragment |
| 4 | 4 | Header flags | **Observed on Pro 2:** byte 4 = fragment count, byte 5 = fragment number (requests `01 00`, whole replies `01 01`, the device list `02 01` then `02 02`), bytes 6-7 zero. Gadgetbridge's comments guessed "always 1?" and "isAck?" (Support:140-142) and its decoder doesn't handle fragments. In a later fragment the payload data starts at offset 8 (no length or CRC fields); the first fragment's CRC covers the whole reassembled payload |
| 8 | 2 | Payload length | |
| 10 | 2 | CRC-16/MAXIM of the payload | Reflected poly 0x8005, init 0, xorout 0xFFFF (`util/CheckSums.java:223-241`) |
| 12 | n | Payload | TLV-like, all u32: `1, len(rest), 2, 4, group, 3, 4, code, 4, len(args), args` |

- Upstream unit-test vector for `FIRMWARE_GET`: `app/src/test/java/nodomain/freeyourgadget/gadgetbridge/service/devices/shokz/ShokzSupportTest.kt:9-12`. Our encoder reproduces it byte for byte (`tests/test_protocol.py`).
- Receive buffer is 2048 bytes (Support:963).
- **No CRC quirk:** Gadgetbridge accepts `MULTIPOINT_DEVICES_RET` with a wrong CRC and skips a "garbled tail" (Support:155-171, 667-718). On the Pro 2 that reply comes in two fragments (116 + 46 payload bytes); Gadgetbridge glues the second fragment's header into the payload. Reassembled, the CRC is valid and the list parses cleanly (test C1). shokzer reassembles and accepts no bad CRC.

## Groups, replies and sequencing

- Group `0x01` = set, `0x02` = get, `0x03` = sent unprompted by the headset (Cmd).
- A reply has the request's group and its code with bit 15 set (`MASK_RESPONSE`, Support:965). Every get/set pair in Cmd follows this.
- **Gadgetbridge compares the code only** (Support:388-390). `LANGUAGE_ACK` (`0x01`/`0x8002`) and `FIRMWARE_RET` (`0x02`/`0x8002`) share a code, so it could take one for the other. **shokzer matches group and code.**
- One request at a time. The comment at Support:82 says the headset doesn't answer everything otherwise; the queue waits for each reply (Support:752-796).
- Timeout 2 s, then **resends the same message up to 3 times** (Support:773-783, 810). That includes writes. **shokzer won't resend writes;** after an uncertain write it reads the state back first.
- Pro 2 initial sequence (Support:70-103 with the Pro 2's `supportsMp3 = false`, `supportsControls = false`): `FIRMWARE_GET`, then `BATTERY_GET`, `EQUALIZER_GET`, `PLAYBACK_STATUS_GET`, `VOLUME_GET`, `LANGUAGE_GET`.

## Operations

| Operation | Scope | Request → reply | Source | Tier | Open questions |
|---|---|---|---|---|---|
| Firmware read | Shared | `0x02/0x0002`, no args → `0x02/0x8002`: `00`, then NUL-terminated string | Cmd:26-27; Support:83, 399-417; test vector ShokzSupportTest.kt:9-12 | 2, 3 (encoding), 4, **5** | Resolved: headset reports `BOS_HU_R_47_20251023`, the app shows it without `BOS_`. Reply args are padded with 13 zero bytes after the NUL |
| Battery read | Shared | `0x02/0x0003` → `0x02/0x8003`: `00`, level byte *b*, then `ff 00` (unknown); percent = (*b* + 1) × 10 | Cmd:7-8; Support:442-457 | 2, 4, **5** | Matched BlueZ at 60 % and 50 %. Headset sends no battery notifications |
| EQ read | Shared | `0x02/0x0008` → `0x02/0x8008`: `00`, preset code, then the preset's six tuning bytes as written (custom: the five band gains and `00`) | Cmd:23-24; Support:459-507 | 2, 4, **5** | — |
| EQ change notice | Shared | Headset sends `0x03/0x0009`: preset code (no status byte). Gadgetbridge calls it `EQUALIZER_ACK` | Cmd:22; Support:459-507 | 2, 4 | Sent after our own writes, after button changes, or both? |
| EQ set acknowledgement | Shared | `0x01/0x800e` (`EQ_UNK_ACK_1`) with args `00 00 00 00`, about 40 ms after the write | Cmd:21; Support:388-390, 748 | 2, **5** | Means "received"; shokzer confirms by readback. No `0x03/0x0009` notice followed our own writes |
| Standard preset | **Pro 2** | `0x01/0x000e` args `01 00 00 00 00 00 00 00` | Pro2:49-53 | 2, 3, 4, **5** | Read back and heard (test A) |
| Vocal preset | **Pro 2** | args `02 fa fe 04 05 01 00 00` | Pro2:55-56 | 2, 3, 4, **5** | Read back and heard |
| Bass Boost preset | **Pro 2** | args `03 09 f9 00 00 00 00 00` | Pro2:58-59 | 2, 3, 4, **5** | Read back and heard |
| Treble Boost preset | **Pro 2** | args `04 00 00 01 04 05 00 00` | Pro2:61-62 | 2, 3, 4, **5** | Read back and heard; survived a power cycle |
| Custom EQ | **Pro 2** | args `05 b1 b2 b3 b4 b5 00 00`, each band −5…5 | Pro2:38-41; Coord:109-117 | 2, 3, 4, **5** | Bands read back exactly. Band 1 is the low end, band 5 the high end (heard at +5; +2 was too subtle). Frequencies and persistence unknown. Writing it replaces the stored custom profile |
| Classic / Volume Boost | **Pro 2** flag | codes `0x08` / `0x0a`, args code + seven `00` (shared default, not Pro 2-specific bytes) | Pro2:43-47; Coord:81-98; Args:32-33 | 2, 3 (flag only) | Pro 2 args were not captured separately. Out of scope until evidenced |
| Multipoint status | Shared | `0x02/0x0010` → `0x02/0x8010`: bytes `00 01 01 00` = on, `00 00 01 00` = off (byte 1 is the flag) | Cmd:61-62; Support:634-639 | 2, **5** | Off value observed at test C2; other values reported as unknown |
| Multipoint peer list | Shared | `0x02/0x0004` → `0x02/0x8004`, fragmented: `00`, slot count (3 here), then per entry index, address (reversed), connected, name length (31), name | Cmd:47; Support:667-718 | 2, **5** | Matched the real connections |
| Multipoint on / off | Shared | ON `0x01/0x0011` → `0x01/0x8011` status 0. OFF `0x01/0x0010` with the asking device's address → `0x01/0x8010` status 0; other devices drop, the asker stays | Support:641-665, 841-866 | 2, **5** | — |
| Connect / disconnect a remembered device | Shared | `0x01/0x000b` / `0x01/0x000c` with the address reversed. Connect is acknowledged with status **`0x300`** and completes in about 3 s; disconnect with 0 | Support:719-736, 905-918 | 2, **5** | Gadgetbridge names only `0x400` (too many connected). shokzer judges by the device list, never by the status alone |
| Connection notice | Shared | Headset sends `0x03/0x000b` unprompted when a device connects or drops: address (reversed), connected, name length, name | Cmd:51; Support:729-732 | **5** | Layout observed at C2/C4; decoded, informational |
| Multipoint pairing mode | Shared | `0x01/0x001d`, `0x01/0x0023` (no reply waited) | Support:921-928 | 2 | Not implemented: out of scope |
| Button controls | Not on Pro 2 | `CONTROLS_GET` goes unanswered on Pro 2 | Pro2:28-31; Cmd:10 | 3 | Don't send |

## Where shokzer differs from Gadgetbridge on purpose

- Fragmented replies are reassembled; no reply type gets a CRC exception.

- Replies are matched on group and code, not code alone.
- Writes are never resent blindly. Timeout on a write leaves the outcome uncertain until an `EQUALIZER_GET` confirms it.
- Frames whose two length fields disagree are treated as noise; the decoder rescans for the next `A5 5A` one byte at a time instead of skipping two bytes. All three Pro 2 replies seen so far satisfy *frame length = payload length + 4*.

## Transport (implemented, verified at M3)

Use BlueZ's profile API rather than opening RFCOMM by channel number, so BlueZ does the SDP lookup and we never guess a channel:

1. Export an `org.bluez.Profile1` object on our system-bus connection.
2. `ProfileManager1.RegisterProfile(path, "0000fef0-0000-1000-8000-00805f9b34fb", {Role: "client", AutoConnect: false, RequireAuthentication: true})`.
3. `Device1.ConnectProfile("0000fef0-…")`. BlueZ looks up the channel and passes us the socket in `Profile1.NewConnection(device, fd, props)`.
4. Close the fd and call `DisconnectProfile`, then `UnregisterProfile`, on exit or failure.

Verified 2026-11-13: BlueZ resolved the channel from SDP and delivered the socket; first open 2.08 s, later ones about 0.5 s. Code: `src/shokzer/transport/management.py`.

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

## Observed BlueZ and headset behaviour (2026-11-13)

- **One management connection at a time.** With the Shokz app connected, `ConnectProfile` fails with `br-connection-refused` (test D1). shokzer suggests closing the app.
- **Don't abandon `ConnectProfile`.** Cancelling it mid-flight and unregistering the profile left BlueZ answering `br-connection-busy` to every new connect until the headset was power-cycled. shokzer now waits for BlueZ's answer before releasing the profile.
- **Channel open time:** 2.1 s the first time after the headset connects, 0.05-0.5 s afterwards.
- **BlueZ restart:** the headset dropped and reconnected by itself after about 29 s (test D6).
