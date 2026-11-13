# Hardware test plan — everything after M3

**Run 2026-11-13:** A-F done, results in `test-results.md`. Still open: A7 (GNOME reconnect), D5 (suspend/resume), RPM removal (F), G (Android comparison). Everything that passed is in `VERIFIED`.


Everything below is built and passes offline. None of it has run against the headset. Run it with Markos present, the Shokz app force-stopped on the Pixel unless a step says otherwise, and music playing from Fedora at low volume so changes are audible.

Each step has a trace (`--trace local/traces/<date>-<step>.jsonl`). Record results in `test-results.md`; after a step passes, add its feature to `VERIFIED` in `src/shokzer/capabilities.py` for `BOS_HU_R_47_20251023`.

**Stop** on: an unexpected reply, a readback that doesn't match, audio dropouts or loud sound, the headset resetting or disconnecting. Recover with a normal reconnect or power cycle, or the Shokz app. Never with guessed bytes.

## A. EQ presets (M4, M5)

Bytes are in `protocol-analysis.md`. Each `eq set` reads first, sends once, then reads back.

| Step | Command | Expect | Markos checks |
|---|---|---|---|
| A1 | `shokzer status` | EQ standard | — |
| A2 | `shokzer eq set vocal --allow-unverified` | `confirmed`, read back vocal | Audible change |
| A3 | `shokzer eq set standard --allow-unverified` | `confirmed` | Sounds as before |
| A4 | `shokzer eq set bass --allow-unverified`, then back to standard | `confirmed` both | Audible change, then back |
| A5 | `shokzer eq set treble --allow-unverified`, then back to standard | `confirmed` both | Audible change, then back |
| A6 | Set vocal; Markos turns the headset off and on; it reconnects; `shokzer eq get` | Vocal if the headset stores it | — |
| A7 | Disconnect and reconnect in GNOME Settings; `shokzer eq get`; set standard | Same as before reconnecting | — |
| A8 | Open the Shokz app; does it show the EQ shokzer set? | — | App view |

Pass for a preset → add `eq_write_<preset>` to `VERIFIED`.

## B. Custom EQ (M7a)

Writing the custom EQ overwrites the headset's stored custom profile. If Markos has one he wants to keep, write its five values down from the app first.

| Step | Command | Expect |
|---|---|---|
| B1 | `shokzer eq custom 2 0 0 0 0 --allow-unverified` | `confirmed` with bands read back, or `partly_confirmed` if the headset doesn't report them |
| B2 | `shokzer eq custom 0 0 0 0 2 --allow-unverified` | Audible difference from B1 (low vs high end tells which band is which) |
| B3 | Restore: the saved profile, or `shokzer eq set standard --allow-unverified` | `confirmed` |

Record which band sounds lowest and highest. Pass → add `eq_write_custom`.

## C. Multipoint (M7b)

Start read-only. The Pixel's Bluetooth can be on for this part; the Shokz app stays closed.

| Step | Command | Expect |
|---|---|---|
| C1 | `shokzer multipoint status --allow-unverified --show-identifiers` | Status and device list. Record the raw value and whether the list matches reality |
| C2 | With multipoint on: `shokzer multipoint off --allow-unverified` | Phone disconnects, this computer stays. Record the new raw value: it's the "off" value no source names yet |
| C3 | `shokzer multipoint on --allow-unverified` | `confirmed` |
| C4 | `shokzer multipoint disconnect <phone> --allow-unverified`, then `connect <phone>` | Phone drops, then reconnects |

After C2, add the observed raw value to `MULTIPOINT_OFF_VALUES` in `protocol/responses.py` so "off" can be confirmed. Recovery if the phone doesn't come back: connect it from the phone's Bluetooth settings. Pass → add `multipoint_read`, then `multipoint_write` and `multipoint_peers`.

## D. Robustness

| Step | Action | Expect |
|---|---|---|
| D1 | Shokz app open and connected; `shokzer status` | Works, or fails cleanly. Record which |
| D2 | `shokzer status` while the headset is turned off halfway | Clean error, no hang, lock released |
| D3 | GUI open and idle; `shokzer eq get` | Works (the GUI holds no session while idle) |
| D4 | Two `shokzer status` at once | Second one refuses with the lock message |
| D5 | Suspend and resume the laptop; `shokzer status` | Works after BlueZ reconnects the headset |
| D6 | Restart BlueZ (`sudo systemctl restart bluetooth`, needs approval) with `diagnose --watch` running | Watch shows unavailable, then the headset again |

## E. GUI (M6)

`shokzer-gui` from the repo venv. Check: values match `status`, EQ switching works for verified presets, unverified ones are greyed out, a disconnect clears values, closing the window during an action exits cleanly.

## F. Local RPM (M8)

Needs approval for `sudo`:

```sh
sudo dnf install rpm-build
sudo dnf builddep packaging/shokzer.spec
./packaging/build-rpm.sh
sudo dnf install ./build/rpm/RPMS/noarch/shokzer-*.rpm
```

Check: the app appears in GNOME with its icon and starts; `shokzer diagnose` works from `/usr/bin`; `sudo dnf remove shokzer` removes everything and leaves pairings untouched.

## G. Android reference

Record the Pixel's Android build and the Shokz app version. Compare EQ changed in the app with `shokzer eq get`.
