# OpenRealLink — tech stack options

Status: **decision pending**. Nothing here is committed to yet.

Goal: a cross-platform app that controls a **realme Buds Air7 Pro** (and,
later, sibling realme/OPPO TL headsets) **without the official realme Link
app and without an account**.

The protocol work is already done and verified on air —
see [`the analysis repository`](https://github.com/anshwalia/the analysis repository):
`docs/05` (wire format), `docs/09` (capability bitmap), `docs/14` §7–§8
(noise control + read-only inventory).

---

## 1. The constraint that picks the stack

Everything else is negotiable. **The transport is Bluetooth Classic
RFCOMM/SPP** — UUID `00001101-0000-1000-8000-00805F9B34FB`, SDP channel 12 on
the Air7 Pro, OPPOv1 framing, ~10 ms command spacing.

This was confirmed, not assumed:

* the Air7 Pro RUS config (`dn4PRxdiX3hnEQdmbRkdQE5cEEFkWFRTZWlOAg==`) has
  **no BLE/GATT key** — only `"sppUUIDType": "TYPE_DEFAULT"`;
* the live SDP record exposes Classic channels 12 / 15 / 17;
* the working controller in this project drives `IOBluetoothRFCOMMChannel`.

The GATT UUIDs in `UUIDStorage` (`…-irohaBLE`) belong to **other** TL models,
not this one. *(Worth a 10-minute probe later: the buds may still advertise a
BLE GATT service the app never uses. If they do, Web Bluetooth and iOS become
possible. Until then, assume Classic only.)*

### Platform viability for Classic RFCOMM

| platform | API | viable? |
|---|---|---|
| **macOS** | `IOBluetooth.framework` (`IOBluetoothRFCOMMChannel`) | ✅ proven in this project |
| **Windows** | WinSock `AF_BLUETOOTH` / `BTHPROTO_RFCOMM` | ✅ |
| **Linux** | BlueZ `AF_BLUETOOTH` / `BTPROTO_RFCOMM` socket | ✅ (pure `socket` module in Python) |
| **Android** | `BluetoothSocket` | ✅ |
| **iOS** | `ExternalAccessory` — **MFi accessories only** | ❌ not MFi; `CoreBluetooth` is BLE-only |
| **Web** | Web Bluetooth — **BLE GATT only** | ❌ |

**So "cross-platform" means macOS + Windows + Linux + Android.** iOS and
browser are off the table for this device unless the BLE probe above changes
the picture.

That single fact eliminates several otherwise-attractive stacks and is the
main thing to weigh when choosing.

---

## 2. What the app actually has to do

Useful for judging "how much do I have to rewrite".

| layer | work | already exists? |
|---|---|---|
| **transport** | SDP/connect, read/write bytes, reconnect, discover channel | ~200 lines per platform (`tools/rfcomm_link.py` for macOS) |
| **framing** | OPPOv1: `0xAA`, LEB128 len, control byte, FSN, multi-frame reassembly | ✅ `tl_frame.py` (~250 lines) |
| **packet** | 5-byte LE header, response bit, manager dispatch | ✅ `tl_frame.py` |
| **command table** | 79 IDs, 61 capability groups, always-on set | ✅ `tl_frame.py` (tables) |
| **capability gate** | decode `0x8100` bitmap → which commands this unit accepts | ✅ |
| **device model** | RUS config, `settingType` map, noise vocabulary, feature gating | ✅ `tl_frame.py` + `docs/14` |
| **UI** | scan/pair, connect, capability view, noise selector, firmware, paired list, EQ, battery/wear, raw console | ❌ |

**The protocol core is ~600 lines of real logic and is already written,
self-tested (`--selftest`, ALL PASS) and verified on air.** Every stack below
either reuses it or ports it once.

Two assets make any port cheap and safe:

* `captures/*.jsonl` and `captures/read-poll-sweep.txt` — **golden fixtures**
  for byte-exact regression tests in any language;
* `tl_frame.py --selftest` — the reference behaviour to port against.

### Design constraints the app must honour

1. **Never expose the hearing/ear-scan commands** (`0x040E`, `0x0415`,
   capability bits 19/30) in the UI without a hard warning — they drive
   **loud test tones**. Only `0x0404 action=1` (noise mode) is safe.
2. **Noise writes are gated on in-ear detection** — the UI must explain
   `0x8404 0e` ("put the buds in") rather than look broken.
3. **No account, no telemetry, no vendor server.**
4. GPL-3.0 (already chosen) — all stacks below are licence-compatible.

---

## 3. Options

Effort is for a working app on macOS + one other platform. "Reuse" is how much
of the existing Python protocol work survives.

### Option A — Python core + PySide6 (Qt 6)

| | |
|---|---|
| Languages | Python 3.11+ (protocol + UI), ~0 native code |
| RFCOMM | macOS: **pyobjc/IOBluetooth (already working)** · Linux: stdlib `socket` (`AF_BLUETOOTH`) · Windows: `ctypes` → WinSock `AF_BLUETOOTH` (~80 lines) · Android: *not viable* |
| UI | Qt Quick / QML or QWidgets — one codebase, mac/Win/Linux |
| Reuse | **~100%** — `tl_frame.py` imported directly |
| Packaging | PyInstaller / Briefcase (fiddly, ~100 MB) |
| Risk | **Low** |
| Effort | **~1 week** to a real app |
| Good for | desktop-only, fastest path, keeping one language with the RE work |

**For:** reuses the verified protocol verbatim; only 3 small transport shims;
you can ship something real almost immediately.
**Against:** no Android; Qt/PyInstaller packaging is the annoying part.

### Option B — Flutter + Dart protocol + native SPP plugin

| | |
|---|---|
| Languages | Dart (UI + protocol), Kotlin / Swift-ObjC / C++ / C# shims |
| RFCOMM | **write a plugin per platform**: Android `BluetoothSocket` (Kotlin, ~120 lines), macOS `IOBluetooth` (ObjC/Swift, ~120), Windows WinSock (C++, ~180), Linux BlueZ (C++, ~80) |
| UI | Flutter — best-in-class, one codebase, **incl. Android** |
| Reuse | protocol **ported** to Dart (~600 lines, mechanical, fixture-tested) |
| Packaging | excellent (`flutter build`) |
| Risk | Medium — 4 native shims, but each is small and well-trodden |
| Effort | **~3–4 weeks** |
| Good for | the actual goal: a real phone app (Android) **plus** desktop |

**For:** the only option that gives a genuinely good **Android** app *and*
desktop from one UI codebase; huge ecosystem; crib from
`flutter_bluetooth_serial` / `flutter_bluetooth_classic` for the Android shim.
**Against:** most total code (4 transports); Dart protocol port must be kept in
sync with `tl_frame.py`.

### Option C — Rust core + Tauri v2 (web UI)

| | |
|---|---|
| Languages | Rust (protocol + transport), TypeScript/HTML (UI) |
| RFCOMM | Linux: `libc` raw socket ✅ · Windows: `windows` crate ✅ · **macOS: the blocker** — `IOBluetooth` has no maintained `objc2` binding, so hand-rolled `objc2` msgSends or a C shim |
| UI | Tauri (web) — mac/Win/Linux; Android via Tauri v2 + a Kotlin transport |
| Reuse | protocol **ported** to Rust (~700 lines, plus `serde` types) |
| Packaging | good, small binaries |
| Risk | Medium-high (macOS RFCOMM) |
| Effort | **~4–6 weeks** |
| Good for | a reusable, FFI-able `open-real-link-core` other projects can embed |

**For:** the best long-term artifact — one core crate usable from Python,
Swift, Kotlin, JS via FFI; small, fast binaries.
**Against:** macOS RFCOMM in Rust is real, unglamorous work; Rust RFCOMM
tooling is immature (`btleplug` is BLE-only).

### Option D — Qt 6 / C++ (`QBluetoothSocket`)

| | |
|---|---|
| Languages | C++ / QML |
| RFCOMM | **one API for all four platforms** — `QBluetoothSocket(QBluetoothServiceInfo::RfcommProtocol)`; Linux/macOS/Android solid, Windows support exists in Qt 6 but is less battle-tested (**verify first**) |
| UI | Qt Quick / QML |
| Reuse | protocol **ported** to C++ (~700 lines) |
| Packaging | Qt deploy tooling, works well |
| Risk | Medium-low technically; heavy toolchain |
| Effort | **~2–3 weeks** (after `brew install qt cmake`, ~1 GB) |
| Good for | one language, one API, all four platforms, no native glue |

**For:** the only stack where RFCOMM is a **first-class cross-platform
abstraction** — you write the transport once.
**Against:** nothing is installed; C++ ergonomics for a hobby app; Qt's Windows
Bluetooth backend needs a spike before committing.

### Option E — .NET 9 + Avalonia + 32feet.NET

| | |
|---|---|
| Languages | C# |
| RFCOMM | 32feet (`InTheHand.Net.Bluetooth`): Windows ✅, Linux ✅, Android ✅, **macOS ❌** (support was dropped years ago; would need P/Invoke → IOBluetooth) |
| UI | Avalonia — excellent, mac/Win/Linux/Android |
| Reuse | protocol **ported** to C# (~600 lines) |
| Risk | Medium — the macOS gap is the whole question |
| Effort | **~3 weeks** (+ a macOS P/Invoke shim) |
| Good for | C# shops, great desktop UI |

**For:** nicest desktop UI story after Flutter; 32feet is mature where it works.
**Against:** **macOS RFCOMM is missing** — for a project whose proven platform
*is* macOS, that's backwards.

### Option F — Kotlin Multiplatform + Compose Multiplatform

| | |
|---|---|
| Languages | Kotlin (shared), thin platform code |
| RFCOMM | Android ✅ native; **JVM desktop is the problem** — BlueCove is dead, so JNA → IOBluetooth / WinSock / BlueZ by hand |
| UI | Compose Multiplatform (Android/desktop/iOS stable) |
| Risk | High (desktop transport) |
| Effort | ~4 weeks |
| Good for | Android-first products |

**For:** best if Android is the *only* real target and desktop is a nice-to-have.
**Against:** desktop RFCOMM is the worst of any option here.

---

## 4. Comparison

| option | languages | Android | macOS | Windows | Linux | reuse | risk | effort |
|---|---|---|---|---|---|---|---|---|
| **A** Python + PySide6 | Python | ❌ | ✅ proven | ✅ | ✅ | **~100 %** | **low** | **1 wk** |
| **B** Flutter + plugin | Dart + 4 native | ✅ | ✅ | ✅ | ✅ | port | med | 3–4 wk |
| **C** Rust + Tauri | Rust + TS | ✅* | ⚠️ hand-rolled | ✅ | ✅ | port | med-high | 4–6 wk |
| **D** Qt 6 / C++ | C++/QML | ✅ | ✅ | ⚠️ verify | ✅ | port | med-low | 2–3 wk |
| **E** .NET + Avalonia | C# | ✅ | ❌ | ✅ | ✅ | port | med | 3 wk |
| **F** KMP + Compose | Kotlin | ✅ | ⚠️ JNA | ⚠️ JNA | ⚠️ JNA | port | high | 4 wk |

\* via Tauri v2 Android + a Kotlin transport.

**Also considered and rejected:** Electron/Node (`bluetooth-serial-port` is
unmaintained and won't build on Node 26); Go + Fyne (no usable RFCOMM);
Swift/SwiftUI (not cross-platform); a local-daemon + browser UI (nice for a
control panel, but not a real app on Android).

---

## 5. Recommendation

**If desktop is enough → Option A (Python + PySide6).** It reuses the verified
protocol verbatim, the macOS transport is already written, and you get a
genuine mac/Win/Linux app in about a week. The Android gap is the only real
cost.

**If Android matters (it probably does — it's where the official app lives) →
Option B (Flutter).** Most total code, but the only route to a good Android app
*and* desktop from one UI codebase, and the four transport shims are each small
and can be cribbed from existing packages.

**If you want a reusable protocol library others can build on → Option C
(Rust core)**, accepting that macOS RFCOMM is the hard part.

**Option D (Qt)** is the dark horse: the only stack with one cross-platform
RFCOMM API. Worth a spike if you're comfortable in C++.

**Before committing, two cheap experiments are worth doing:**

1. **BLE probe** — scan the buds for a GATT service. If one exists, the whole
   calculus changes (Web Bluetooth + iOS + Flutter BLE open up).
2. **Qt Windows spike** (only if choosing D) — confirm `QBluetoothSocket`
   RFCOMM actually works on Windows 11 before betting on it.

---

## 6. Questions to settle

1. **Which platforms are must-haves?** macOS only / + Windows / + Linux / +
   Android / iOS?
2. **Does Android matter?** (It's the official app's platform, so an open
   alternative is arguably most valuable there.)
3. **Is a reusable protocol library a goal**, or is the app the product?
4. **Distribution**: personal use / GitHub releases / app stores?
5. **Language preference** — stay in Python with the RE work, or move to a
   compiled stack for a "real" app feel?
