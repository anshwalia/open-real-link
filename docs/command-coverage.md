# Command coverage — every known TL ID vs. what `open-real-link` does

The full recovered TL command catalogue, cross-referenced against the
`open-real-link` implementation. It answers one question per row: *is this
ID implemented here, and does an Air7 Pro accept it?*

The command catalogue was recovered by static analysis of the realme Link Android
app and confirmed against a real Buds Air7 Pro. **`tlcore` is the authority** for
what is implemented; `docs/protocol.md` describes that surface.

**79 request command IDs**, **35 implemented** in `open-real-link`,
**44 not implemented**; an Air7 Pro advertises **49** of them
(capability bitmap `00bf4f5ae665dc4004`).

## Legend

| Column | Meaning |
| --- | --- |
| **open-real-link** | `build` = payload builder, `send` = sent by `Session`/`tlcli`, `decode` = reply/push decoder; `—` = not implemented |
| **Air7 Pro** | `live` = *advertised* by this unit's capability bitmap — **not** a claim that it was exercised end to end; `—` = not enabled |
| **gating** | `always-on`, `cap-bit N` (enabled when capability bit N is set), or `ungated` |

## By manager class

Every one of the 79 IDs, grouped by its manager class (`cmd & 0x7F00`, the
family in the catalogue below). Counts are derived from `tlcore.ALL_COMMANDS`;
*implemented* is the number of rows whose **open-real-link** column is not `—`.

| Manager class | Mask | Commands | Implemented | Advertised (Air7 Pro) | Gating |
| --- | --- | ---: | ---: | ---: | --- |
| `PollCommandManager` | `0x0100` | 31 | 21 | 28 | 6 always-on, 23 cap-bit, 2 ungated |
| `NotificationCommandManager` | `0x0200` | 6 | 2 | 1 | 1 cap-bit, 5 ungated |
| `UpgradeManager` (OTA) | `0x0300` | 9 | 0 | 0 | 9 ungated |
| `SetCommandManager` | `0x0400` | 22 | 8 | 18 | 22 cap-bit |
| `RequestCommandManager` | `0x0500` | 8 | 4 | 1 | 1 cap-bit, 7 ungated |
| `DebugCommandManager` | `0x0F00` | 3 | 0 | 1 | 1 always-on, 2 ungated |
| **total** | | **79** | **35** | **49** | |

## Full catalogue

| cmd | dec | family | gating | open-real-link | Air7 Pro | observed name |
|---|---:|---|---|---|---|:--:|---|
| `0x0100` | 256 | poll | always-on | send + decode | live | getRemoteCapability/getStatusFeature |
| `0x0101` | 257 | poll | always-on | send | live | getDeviceVersion, getProperty |
| `0x0102` | 258 | poll | always-on | send + decode | live | getRemoteVendorId |
| `0x0103` | 259 | poll | always-on | send + decode | live | — |
| `0x0104` | 260 | poll | always-on | send + decode | live | getRemoteProtocolVersion |
| `0x0105` | 261 | poll | cap-bit 0 | send + decode | live | — |
| `0x0106` | 262 | poll | cap-bit 1 | send + decode | live | — |
| `0x0107` | 263 | poll | cap-bit 2 | send + decode | live | getUpgradeCapability |
| `0x0108` | 264 | poll | cap-bit 3 | send + decode | live | getKeyFunction |
| `0x0109` | 265 | poll | cap-bit 4 | send + decode | live | — |
| `0x010b` | 267 | poll | always-on | send + decode | live | getEarbudsColorId |
| `0x010c` | 268 | poll | cap-bit 8 | send + decode | live | — |
| `0x010d` | 269 | poll | cap-bit 7 | send + decode | live | — |
| `0x010f` | 271 | poll | cap-bit 10 | send + decode | live | getEQStatus |
| `0x0110` | 272 | poll | cap-bit 11 | send + decode | live | — |
| `0x0111` | 273 | poll | cap-bit 15 | — | — | — |
| `0x0112` | 274 | poll | cap-bit 29 | send + decode | live | — |
| `0x0115` | 277 | poll | cap-bit 19 | — | live | — |
| `0x0116` | 278 | poll | cap-bit 19 | — | live | — |
| `0x0118` | 280 | poll | cap-bit 25 | — | live | getEarRestoreData |
| `0x011a` | 282 | poll | cap-bit 26 | send + decode | live | getPersonalizedNoiseReductionResultExist |
| `0x011e` | 286 | poll | cap-bit 30 | — | live | getEarScanData |
| `0x011f` | 287 | poll | cap-bit 30 | — | live | getEarScanFilterData |
| `0x0120` | 288 | poll | cap-bit 32 | — | live | getEarLanguageData |
| `0x0122` | 290 | poll | cap-bit 34 | send + decode | live | getAllEqInfo |
| `0x0124` | 292 | poll | cap-bit 38 | — | live | — |
| `0x0128` | 296 | poll | cap-bit 46 | send + decode | live | — |
| `0x0129` | 297 | poll | ungated | — | — | getStatusFeature |
| `0x012a` | 298 | poll | ungated | — | — | detectFeature |
| `0x012c` | 300 | poll | cap-bit 54 | send + decode | live | getMultiBassEngineValue |
| `0x0131` | 305 | poll | cap-bit 58 | send + decode | live | — |
| `0x0200` | 512 | notify | ungated | send + decode | — | getNotificationCapabilities |
| `0x0201` | 513 | notify | ungated | — | — | — |
| `0x0202` | 514 | notify | ungated | — | — | registerNotification |
| `0x0203` | 515 | notify | ungated | — | — | cancelRegisterNotification |
| `0x0204` | 516 | notify | ungated | decode | — | notificationEvent |
| `0x0205` | 517 | notify | cap-bit 22 | — | live | registerMultiNotification |
| `0x0300` | 768 | OTA | ungated | — | — | — |
| `0x0301` | 769 | OTA | ungated | — | — | — |
| `0x0302` | 770 | OTA | ungated | — | — | readSize |
| `0x0303` | 771 | OTA | ungated | — | — | readSize |
| `0x0304` | 772 | OTA | ungated | — | — | — |
| `0x0305` | 773 | OTA | ungated | — | — | — |
| `0x0306` | 774 | OTA | ungated | — | — | — |
| `0x0307` | 775 | OTA | ungated | — | — | — |
| `0x0308` | 776 | OTA | ungated | — | — | — |
| `0x0400` | 1024 | set | cap-bit 5 | build + send | live | setFindMode |
| `0x0401` | 1025 | set | cap-bit 3 | build | live | setKeyFunction |
| `0x0402` | 1026 | set | cap-bit 6 | — | — | — |
| `0x0403` | 1027 | set | cap-bit 7 | — | live | setSwitchFeature |
| `0x0404` | 1028 | set | cap-bit 8 | build + send | live | SetNoiseReduction |
| `0x0405` | 1029 | set | cap-bit 9 | build + send + decode | live | setCompactnessDetect |
| `0x0406` | 1030 | set | cap-bit 10 | build + send | live | setEQStatus |
| `0x0407` | 1031 | set | cap-bit 11 | build + send | live | setHighVolume |
| `0x0408` | 1032 | set | cap-bit 13 | — | — | — |
| `0x0409` | 1033 | set | cap-bit 14 | — | live | — |
| `0x040a` | 1034 | set | cap-bit 15 | — | — | setAutoPowerOffTime |
| `0x040b` | 1035 | set | cap-bit 29 | — | live | setMultiConnectAction |
| `0x040d` | 1037 | set | cap-bit 19 | — | live | — |
| `0x040e` | 1038 | set | cap-bit 19 | — | live | switchProcessHearingDetectionParams |
| `0x0411` | 1041 | set | cap-bit 25 | — | live | setRestoreData |
| `0x0412` | 1042 | set | cap-bit 26 | — | live | setPersonalizedNoiseReduction |
| `0x0415` | 1045 | set | cap-bit 30 | — | live | sendProcessEarScanData |
| `0x0418` | 1048 | set | cap-bit 34 | — | live | getDbValue, getFrequency, setEqInfo |
| `0x041b` | 1051 | set | cap-bit 38 | build + send | live | setBassEngineValue |
| `0x041e` | 1054 | set | cap-bit 43 | — | live | setSpatialAudioType |
| `0x0428` | 1064 | set | cap-bit 58 | build + send | live | setAIStatus / setTranslationAppStatus |
| `0x042c` | 1068 | set | cap-bit 60 | — | — | setHeadsetMicStatus |
| `0x0500` | 1280 | device->app | ungated | build + decode | — | requestHostTime |
| `0x0502` | 1282 | device->app | ungated | — | — | requestOperation |
| `0x0503` | 1283 | device->app | ungated | — | — | featureSwitchStatus |
| `0x0504` | 1284 | device->app | ungated | decode | — | equalizerMode |
| `0x0506` | 1286 | device->app | ungated | decode | — | customEq |
| `0x0508` | 1288 | device->app | ungated | decode | — | bassEngineStatus |
| `0x050e` | 1294 | device->app | cap-bit 46 | — | live | useTime |
| `0x0516` | 1302 | device->app | ungated | — | — | oneshotAudioData |
| `0x0f00` | 3840 | debug | always-on | — | live | switchDebugStatus |
| `0x0f01` | 3841 | debug | ungated | — | — | — |
| `0x0f02` | 3842 | debug | ungated | — | — | — |
| `0x079a` | 1946 | misc | ungated | — | — | — |
| `0x7f00` | 32512 | misc | ungated | — | — | — |

Two of the 81 rows are **not** TL commands and are not counted above: `0x7F00`
is the manager-class mask (`cmd & 0x7F00`, `Protocol.b`) and `0x079A` is the
OPPO interaction service's 16-bit UUID (`0000079A-D102-…`), not a request the
app sends. That is why the catalogue has 81 rows but only **79 command IDs**.

## Observed negative / unusable results

These IDs were driven on the Air7 Pro (FW `1.1.0.85`, 2026-10-20) and do not
produce a usable reply, so they stay unimplemented. They are recorded here so
the same probes are not repeated:

| ID | observed |
| --- | --- |
| `0x0115` | answers `[01 03 00]` — **status `01` = not available** (no stored hearing-enhancement data) |
| `0x0116` | answers a 250 B `float32` band blob (`[status][hdr:4][a][bandCount:2 LE][b][c] + bands`) |
| `0x011E` | answers `[01 03]` — **status `01` = not available** (no stored ear-scan data) |
| `0x011F` | answers a 155 B `float32` band blob (`sampleRate` `44100`, `bandCount` `36`) |
| `0x0129` | `getStatusFeature` — answers nothing for feature ids `01`..`10` |
| `0x0205` | `registerMultiNotification` — answers nothing, even with the app's `[count][types…]` payload |
| `0x0409` | echoes its payload in a fixed 4-byte buffer: `[status][data:4]` |

`0x0115`/`0x0116`/`0x011E`/`0x011F` are the ear-scan / hearing family — **reads
only**; the matching writes drive loud test tones and are never sent.

## Implemented but not live-verified

**Empty.** The project rule is **implement only what is verified on hardware and
live-tested** (`AGENTS.md`), and the ledger that tracked the exceptions has been
retired: every row was taken to a live Buds Air7 Pro, none could be verified, and
all three implementations were **removed** rather than shipped as guesses. There
is now nothing in `tlcore` that has not been exercised on hardware.

The three rows, and why each failed to verify:

| What | Was | Outcome |
| --- | --- | --- |
| bass-engine read `0x0124` | `parse_bass_engine` | the unit answers **nothing** — not `00 00` as this table used to claim, but no `0x8124` frame at all, for an empty body, `00`, `01`, `0000`, and still after a `0x041B` write. The bass value arrives on the verified `0x0508` push, and `0x012C` reads the multi-band table. **Removed.** |
| EQ write `0x0418` | `payload_set_eq` | `SetCommandManager.p` returns false *without sending* unless the frequency and dB arrays are present and equal in length, and those come from the device's own reply (`CommandUtil.parseAllEqData`); this unit reports `freqCount 0` for every entry, so the band table is unobtainable without the vendor EQ editor. **Removed.** |
| switch-feature write `0x0403` | `payload_set_switch_feature`, the `restore` entry | the device **accepts** any `[feature][enabled]` (`0x8403 00`, even for `00`, which is not a `SWITCH_FEATURE` code) but reports no features (`0x010D 00 00`), changes nothing and never pushes `0x0503` — no observable effect, so no end-to-end verification is possible. **Removed**; the verified `0x010D` read stays. |

The `0x0124` row's own obstacle text was also wrong, and the correction is worth
keeping: it never answered `00 00` — it never answered at all.

### Documented but deliberately not implemented

Four IDs are **out of scope by policy**, not by accident; the project does not
implement them, and all four stay documented here and in `docs/protocol.md`:

* `0x0503` (feature-switch sync) and `0x0516` (oneshot audio) can only ever be
  observed while the official realme Link app is running — a feature toggle for
  `0x0503`, an AI conversation for `0x0516` — so their trigger is not
  host-reachable and no live test can confirm the body.
* `0x0502` (operation request) and `0x050E` (use-time list) *are* host-reachable
  but were **never observed** on this unit: the device does not emit `0x0502`
  for its own gestures, and binding a gesture to an app-side function — the
  host-reachable way to provoke one — did not produce it either; and it never
  pushes `0x050E`, because the app reads use time from the verified `0x0128`
  reply instead.

`0x012A` is the same kind of entry: the unit does not advertise it and never
answers it, so its `0x812A` reply is not decoded either.

`0x0418`, `0x0403` and `0x0124` join this list, each for a reason the live run
established rather than assumed (see the table above and
[`protocol.md`](protocol.md) §7). Implementing any of them would put a
static-only guess behind a supported label. That is why the implemented count is
now **35** (it rose by one for the newly-verified `0x012C`, and fell by three
here; before that from 42 to 40 for
`0x0503`/`0x0516`): the per-manager table and the total row above are the
authority, not any prose figure.

Live-verified device→app requests (Air7 Pro, 2026-10-20): `0x0500` (time),
`0x0504` (pushed on every `0x0406`), `0x0506` (pushed on the `0x0418 [05]`
query), and `0x0508` (pushed on `0x041B`). The `0x0508` decoder was corrected
to read its range as **signed** (`fb 05` = `-5..+5`, not `251..5`).

The bass-engine write `0x041B` (`payload_set_bass_engine`) is live-tested:
`0x041B [min][max][current]` is accepted and the device answers with `0x0508`;
the device keeps its own fixed `-5..+5` range and only echoes `current`. Both
forms were driven on 2026-10-21 — `0x041B fb 05 00` → `0x0508 fb 05 00 00` and
`0x041B fb 05 00 01` → `0x0508 fb 05 00 01` — which also shows the optional
4th byte really is a band selector.

Everything else implemented — the `0x01xx` reads, the noise / EQ-mode /
key-function / high-volume / compactness / AI-status writes, `0x0204`, `0x0500` — has been
exercised on hardware.

## Safety

Three recovered families are **documented but not implemented** — no builder,
decoder or CLI command is provided, and their IDs are recorded only for
identification:

* `0x011E`, `0x011F`, `0x040E`, `0x0415` (the hearing-test / ear-scan family)
  drive **loud, repeated test tones**. They are live on this unit, and `send`
  refuses them without `--force`.
* `0x0300`–`0x0308` (OTA) is a firmware **push** state machine with **no
  read-back** path; it is ungated, so a stray write could begin a flash. Never
  driven on hardware; `send` refuses it without `--force`.
* `0x0F00`–`0x0F02` (debug) is a device → app log stream that toggles real
  firmware verbosity; a retail Buds Air7 Pro refuses it. Never driven on
  hardware; `send` refuses it without `--force`.

Do not sweep unknown command space.

## Backup and restore coverage

`tlcore.backup.capture` reads every supported poll command above and stores the
raw reply. `tlcore.backup.restore` writes back only the settings that are both
recovered and symmetric end to end — `tlcore.backup.RESTORABLE` is exactly these
four:

| setting | read | write |
| --- | --- | --- |
| noise mode | `0x010C` sel `1` | `0x0404` action `1` |
| key function | `0x0108` | `0x0401` |
| EQ mode | `0x010F` | `0x0406` |
| high volume | `0x0110` | `0x0407` |

Everything else is captured but reported `not_restored` (payload value
semantics not recovered). In particular the `0x0122` EQ list is captured but
**not** written back: the `0x0418` encoder refuses a zero-band record, and the
read byte is `isSelected` while the write's first byte is `action`, so the bytes
cannot be replayed verbatim (see `docs/protocol.md` §8).

Two settings used to be restorable and were dropped after live testing:
`switch_features` and `bass_engine`. The switch-feature write (`0x0403`) is
accepted but a provable no-op, so nothing verifies a restore; the bass-engine
write (`0x041B`) **is** verified, but there is no working **read** to capture a
value with, because `0x0124` is never answered (the multi-band read is `0x012C`,
a different command, and the value also arrives on the `0x0508` push).
