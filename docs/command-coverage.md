# Command coverage — every known TL ID vs. what `open-real-link` does

The full recovered TL command catalogue, cross-referenced against the
`open-real-link` implementation. It answers one question per row: *is this
ID implemented here, and does an Air7 Pro accept it?*

The command catalogue was recovered by static analysis of the realme Link Android
app and confirmed against a real Buds Air7 Pro. **`tlcore` is the authority** for
what is implemented; `docs/protocol.md` describes that surface.

**79 request command IDs**, **29 implemented** in `open-real-link`,
**50 not implemented**; an Air7 Pro advertises **49** of them
(capability bitmap `00bf4f5ae665dc4004`).

## Legend

| Column | Meaning |
| --- | --- |
| **open-real-link** | `build` = payload builder, `send` = sent by `Session`/`tlcli`, `decode` = reply/push decoder; `—` = not implemented |
| **Air7 Pro** | `live` = enabled by this unit's capability bitmap; `—` = not enabled |
| **gating** | `always-on`, `cap-bit N` (enabled when capability bit N is set), or `ungated` |

## Full catalogue

| cmd | dec | family | gating | open-real-link | Air7 Pro | observed name |
|---|---:|---|---|---|---|:--:|---|
| `0x0100` | 256 | poll | always-on | send + decode | live | getRemoteCapability/getStatusFeature |
| `0x0101` | 257 | poll | always-on | send | live | getDeviceVersion, getProperty |
| `0x0102` | 258 | poll | always-on | — | live | — |
| `0x0103` | 259 | poll | always-on | send + decode | live | — |
| `0x0104` | 260 | poll | always-on | — | live | getDeviceVersion, getProperty |
| `0x0105` | 261 | poll | cap-bit 0 | send + decode | live | — |
| `0x0106` | 262 | poll | cap-bit 1 | send + decode | live | — |
| `0x0107` | 263 | poll | cap-bit 2 | — | live | — |
| `0x0108` | 264 | poll | cap-bit 3 | send + decode | live | getKeyFunction |
| `0x0109` | 265 | poll | cap-bit 4 | send + decode | live | — |
| `0x010b` | 267 | poll | always-on | — | live | — |
| `0x010c` | 268 | poll | cap-bit 8 | send + decode | live | — |
| `0x010d` | 269 | poll | cap-bit 7 | send + decode | live | — |
| `0x010f` | 271 | poll | cap-bit 10 | send + decode | live | getEQStatus |
| `0x0110` | 272 | poll | cap-bit 11 | — | live | — |
| `0x0111` | 273 | poll | cap-bit 15 | — | — | — |
| `0x0112` | 274 | poll | cap-bit 29 | — | live | — |
| `0x0115` | 277 | poll | cap-bit 19 | — | live | — |
| `0x0116` | 278 | poll | cap-bit 19 | — | live | — |
| `0x0118` | 280 | poll | cap-bit 25 | — | live | getEarRestoreData |
| `0x011a` | 282 | poll | cap-bit 26 | — | live | — |
| `0x011e` | 286 | poll | cap-bit 30 | — | live | getEarScanData |
| `0x011f` | 287 | poll | cap-bit 30 | — | live | getEarScanFilterData |
| `0x0120` | 288 | poll | cap-bit 32 | — | live | getEarLanguageData |
| `0x0122` | 290 | poll | cap-bit 34 | send + decode | live | getAllEqInfo |
| `0x0124` | 292 | poll | cap-bit 38 | send + decode | live | — |
| `0x0128` | 296 | poll | cap-bit 46 | send + decode | live | — |
| `0x0129` | 297 | poll | ungated | — | — | getStatusFeature |
| `0x012a` | 298 | poll | ungated | decode | — | detectFeature |
| `0x012c` | 300 | poll | cap-bit 54 | — | live | startSocketConnect |
| `0x0131` | 305 | poll | cap-bit 58 | — | live | — |
| `0x0200` | 512 | notify | ungated | — | — | getNotificationCapabilities |
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
| `0x0400` | 1024 | set | cap-bit 5 | — | live | — |
| `0x0401` | 1025 | set | cap-bit 3 | build | live | setKeyFunction |
| `0x0402` | 1026 | set | cap-bit 6 | — | — | — |
| `0x0403` | 1027 | set | cap-bit 7 | build + send | live | setSwitchFeature |
| `0x0404` | 1028 | set | cap-bit 8 | build + send | live | SetNoiseReduction |
| `0x0405` | 1029 | set | cap-bit 9 | — | live | — |
| `0x0406` | 1030 | set | cap-bit 10 | build + send | live | setEQStatus |
| `0x0407` | 1031 | set | cap-bit 11 | — | live | setHighVolume |
| `0x0408` | 1032 | set | cap-bit 13 | — | — | — |
| `0x0409` | 1033 | set | cap-bit 14 | — | live | — |
| `0x040a` | 1034 | set | cap-bit 15 | — | — | setAutoPowerOffTime |
| `0x040b` | 1035 | set | cap-bit 29 | — | live | setMultiConnectAction |
| `0x040d` | 1037 | set | cap-bit 19 | — | live | — |
| `0x040e` | 1038 | set | cap-bit 19 | — | live | switchProcessHearingDetectionParams |
| `0x0411` | 1041 | set | cap-bit 25 | — | live | setRestoreData |
| `0x0412` | 1042 | set | cap-bit 26 | — | live | setPersonalizedNoiseReduction |
| `0x0415` | 1045 | set | cap-bit 30 | — | live | sendProcessEarScanData |
| `0x0418` | 1048 | set | cap-bit 34 | build | live | getDbValue, getFrequency, setEqInfo |
| `0x041b` | 1051 | set | cap-bit 38 | build + send | live | setBassEngineValue |
| `0x041e` | 1054 | set | cap-bit 43 | — | live | setSpatialAudioType |
| `0x0428` | 1064 | set | cap-bit 58 | — | live | setTranslationAppStatus |
| `0x042c` | 1068 | set | cap-bit 60 | — | — | setHeadsetMicStatus |
| `0x0500` | 1280 | device->app | ungated | build + decode | — | requestHostTime |
| `0x0502` | 1282 | device->app | ungated | decode | — | requestOperation |
| `0x0503` | 1283 | device->app | ungated | decode | — | featureSwitchStatus |
| `0x0504` | 1284 | device->app | ungated | decode | — | equalizerMode |
| `0x0506` | 1286 | device->app | ungated | decode | — | customEq |
| `0x0508` | 1288 | device->app | ungated | decode | — | bassEngineStatus |
| `0x050e` | 1294 | device->app | cap-bit 46 | decode | live | useTime |
| `0x0516` | 1302 | device->app | ungated | decode | — | oneshotAudioData |
| `0x0f00` | 3840 | debug | always-on | — | live | switchDebugStatus |
| `0x0f01` | 3841 | debug | ungated | — | — | — |
| `0x0f02` | 3842 | debug | ungated | — | — | — |
| `0x079a` | 1946 | misc | ungated | — | — | — |
| `0x7f00` | 32512 | misc | ungated | — | — | — |

Two of the 81 rows are **not** TL commands and are not counted above: `0x7F00`
is the manager-class mask (`cmd & 0x7F00`, `Protocol.b`) and `0x079A` is the
OPPO interaction service's 16-bit UUID (`0000079A-D102-…`), not a request the
app sends. That is why the catalogue has 81 rows but only **79 command IDs**.

## Safety

`0x011E`, `0x011F`, `0x040E` and `0x0415` (the hearing-test / ear-scan family)
drive **loud, repeated test tones**. They are live on this unit and are
deliberately left unimplemented. Do not sweep them.

## Backup and restore coverage

`tlcore.backup.capture` reads every supported poll command above and stores the
raw reply. `tlcore.backup.restore` writes back only the settings that are both
recovered and symmetric end to end:

| setting | read | write |
| --- | --- | --- |
| noise mode | `0x010C` sel `1` | `0x0404` action `1` |
| key function | `0x0108` | `0x0401` |
| EQ mode | `0x010F` | `0x0406` |
| switch features | `0x010D` | `0x0403` |
| bass engine | `0x0124` | `0x041B` |

Everything else is captured but reported `not_restored` (payload value
semantics not recovered). In particular the `0x0122` EQ list is captured but
**not** written back: the `0x0418` encoder refuses a zero-band record, and the
read byte is `isSelected` while the write's first byte is `action`, so the bytes
cannot be replayed verbatim (see `docs/protocol.md` §8).

The bass-engine layout is recovered from the decompiler but is **not**
live-verified: the Air7 Pro advertises capability bit 38 yet does not answer
`0x0124`, so no bass value is derived on this unit and no `0x041B` write is sent.
