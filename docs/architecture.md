# OpenRealLink — architecture: **core library + app**

Status: **decided**. The stack (§7) is still open; this document is about the
shape of the code, which is independent of it.

---

## 1. The decision

**Build a standalone protocol core library, and put the app on top of it.**
Not one application with the protocol buried inside it.

The reason is asymmetry of value and lifetime:

| layer | size | how hard was it to get? | how long will it live? |
|---|---|---|---|
| **protocol core** | ~600 lines | **very** — it is the entire reverse-engineering result | long; every future TL headset reuses it |
| **transport** | ~150–250 lines **per platform** | medium; platform API archaeology | long, but per-platform and independent |
| **app / UI** | the most code | routine | **disposable** — you will rewrite the UI at least once |

The protocol core is the asset. The UI is a shell. If the protocol lives inside
the app, every UI rewrite drags it along, and nobody else can use it.

---

## 2. The three layers

```
┌─────────────────────────────────────────────────────────┐
│  app          scan/pair · connect · noise selector ·     │
│               battery/wear · capability view · EQ ·      │
│               paired list · raw console                  │
└──────────────────────────┬──────────────────────────────┘
                           │ depends on
┌──────────────────────────▼──────────────────────────────┐
│  tlcore       framing (OPPOv1) · packet build/parse ·    │
│  PURE         capability bitmap · command tables ·       │
│  no I/O       decoders · noise vocabulary · state machine│
└──────────────────────────┬──────────────────────────────┘
                           │ talks to an abstract Transport
┌──────────────────────────▼──────────────────────────────┐
│  transport    macos.py · linux.py · windows.py ·         │
│  per-platform android/ · replay.py                       │
└─────────────────────────────────────────────────────────┘
```

**`tlcore` imports nothing platform-specific.** No `IOBluetooth`, no `socket`,
no `serial`, no Qt. It takes a `Transport` object and produces/consumes bytes.
That rule is the whole point of the split, and it is worth enforcing with a test
(`--selftest` asserts that `tlcore` imports nothing outside the stdlib).

---

## 3. The transport contract

The interface is deliberately tiny — six methods. Everything platform-specific
is behind it:

```python
class Transport(Protocol):
    def discover_channel(self, address: str, uuid: str) -> int | None: ...
    def open(self, address: str, channel: int) -> None: ...
    def write(self, data: bytes) -> None: ...
    def read(self, timeout: float) -> bytes: ...
    def close(self) -> None: ...
    def is_connected(self) -> bool: ...   # local property read, NO radio traffic
```

Three details that are protocol requirements, not style:

* **`is_connected()` must be a local property read.** The passive
  "watch until the buds raise the ACL" pattern (docs/16) depends on being able
  to poll link state without putting anything on the radio.
* **`read(timeout)` must pump the platform run loop**, not sleep. On macOS a
  bare `sleep` starves `IOBluetoothRFCOMMChannel` and you get zero bytes
  (this cost real debugging time).
* **`open()` must be able to fail with a *refusal* distinct from a *timeout*.**
  `0xE00002BC` (channel refused — recoverable) and `0xE00002D6` (page timeout —
  buds asleep) mean opposite things; collapsing them loses the diagnosis.

---

## 4. Why this pays off: the replay transport

`transport/replay.py` implements `Transport` over a recorded capture
(`captures/*.jsonl`). **The entire app — protocol, state machine, UI — can then
run with no hardware present.**

That buys three things:

1. **Byte-exact regression tests** in CI, on any machine.
2. **Contributors without the buds** can work on the app.
3. **UI development stops depending on a device being worn and awake** — which,
   given the wear-gating (`0x8404 0e`) and the case/sleep behaviour, is a real
   tax on development speed.

The golden fixtures already exist: `captures/read-poll-sweep.txt`,
`captures/live-caps-fixed.jsonl`, `captures/mode-cycle.jsonl`,
`captures/spp-smoketest.jsonl`.

---

## 5. What goes where

| concern | layer |
|---|---|
| OPPOv1 framing, LEB128 length, FSN, reassembly, the 2-byte length quirk | `tlcore` |
| 5-byte LE packet header, response bit, manager dispatch | `tlcore` |
| capability bitmap → which commands this unit accepts | `tlcore` |
| battery / wear / mode / EQ / use-time decoders | `tlcore` |
| noise vocabulary + the UI↔wire permutation (`O5`) | `tlcore` |
| connection state machine, write gating, retry/backoff | `tlcore` |
| **which bytes go on the wire** | `tlcore` |
| **how bytes physically move** | `transport` |
| layout, widgets, wording, accessibility | `app` |

---

## 6. What we deliberately do **not** do

* **No publishing yet.** Monorepo, two packages, one repo. Publish the core
  separately only once its API stops moving — a published API you then change is
  worse than no package.
* **No plugin architecture.** One transport is selected at runtime by platform;
  there is no registry, no entry points, no dynamic loading.
* **No "extract the library later".** Retrofitting the boundary is exactly the
  work this avoids.
* **No vendor server, no account, no telemetry** — and that is a property of the
  core, not a UI setting.

---

## 7. How this interacts with the stack choice

The layering is language-agnostic and holds for every option in
[`stack-options.md`](stack-options.md). What changes is **only the port cost of
`tlcore`** — because the transport is written per-platform in every stack
anyway, it is never "reused", so it never drives the decision.

| stack | `tlcore` | port cost | verdict |
|---|---|---|---|
| **A** Python + PySide6 | `tl_frame.py` imported as-is | **zero** | fastest to a real app |
| **B** Flutter + plugin | ported to Dart, ~600 lines, mechanical | ~1–2 days, fixture-verified | the only route to Android **and** desktop |
| **C** Rust + Tauri | ported to Rust | ~3 days + `serde` types | best long-term library artifact |
| **D** Qt 6 / C++ | ported to C++ | ~3 days | one RFCOMM API for all platforms |
| **E** .NET + Avalonia | ported to C# | ~3 days | macOS transport missing — backwards |
| **F** KMP + Compose | ported to Kotlin | ~3 days | worst desktop transport |

**Key consequence:** the port is bounded and mechanical, and it is verified
against fixtures that already exist. So "which language" is *not* a bet on
rewriting the project — it is a 1–3 day cost on one layer, paid once.

This means the stack decision reduces to a single product question: **does
Android matter?** (§8).

---

## 8. Naming

**Distribution `open-real-link`. Import `tlcore`.** They differ deliberately, and
the split is load-bearing rather than cosmetic:

* the **distribution** name is the public, searchable identity. It has to be free
  and unambiguous on PyPI, it must not present a vendor's trademark as if it were
  ours, and it has to be recognisable to somebody looking for "the realme Link
  replacement".
* the **import** name is an internal namespace. It should be short, stable and sit
  as close as possible to the vendor's own layout, so that a line in the port can
  be traced back to the class it came from without a lookup table.

`com.realme.iot.headset.tl` is the vendor package this protocol came out of, so
`tlcore` keeps the vendor's own two letters and adds only what it is. That also
names the sibling layers: `tltransport`, `tlcli`.

Verified end to end — installed distribution and import name really do differ:

```console
$ pip install open-real-link
$ python -c "import tlcore; tlcore.build_frames(tlcore.build_packet(0x0404, b'\x01\x01\x01'))"
[b'\xaa\n\x00\x00\x04\x04\x00\x03\x00\x01\x01\x01']
```

### 8.1 Why not the obvious alternatives

| candidate | why not |
|---|---|
| `tl` | two letters; unsearchable and unregisterable in practice |
| `tlv` | **already taken inside this very codebase** — `TLVProtocol`, `TLVStruct`, `TLVDataProcesser{Sender,Receiver}`, `TLVException`, `TLVTemp`. Reusing the prefix would make the port's `tlv.*` collide conceptually with the vendor's TLV layer, which is a *different* thing from the TL protocol |
| `tls` | reads as Transport Layer Security, and is also already present (`TLSKDF`, `TLSKeyMaterialFactory`, `TLSKeyMaterialSpec`, `TLSSocketFactory`) |
| `pytl` | taken on PyPI |
| `oppov1` | names the framing layer only: that is one module (`tlcore.wire`), not the library |
| `buds-link` / `ear-link` | capability-first, but wrong the moment a non-buds device is supported |

`tlcore`, `tlproto`, `libtl`, `tlsdk`, `oppov1`, `open-real-link`, `buds-link`,
`ear-link` and `tl-headset` were all free on PyPI; `pytl` was not.

### 8.2 "TL" stays unexplained

`TL` is **never expanded anywhere in the DEX**. The only identifiers carrying it
are `TLConstant` and `TLHeadsetConnectionImp`, and both use it as a bare prefix,
exactly like the package name. There is no `TLConstant.txt`, no English gloss and
no protocol document in the APK.

So the port does not invent one. Docstrings say "the TL headset protocol" and
leave it there. A made-up expansion would be a claim the evidence does not
support, and it would be the one thing a reader trusts first and is misled by
first. The vendor name belongs in the **description** field, where it is a
factual statement about provenance:

> Python implementation of the TL headset protocol used by realme Link / OPPO
> headset apps

### 8.3 If it is ever published protocol-only

If `tlcore` turns out to be a deliverable in its own right (open question 2
below), the distribution name is still `open-real-link`; the alternative worth
considering then is `tl-headset-protocol`, which trades discoverability by project
name for discoverability by protocol name. There is no reason to decide that now.

---

## 9. Open questions

1. **Does Android matter?**
   * **Yes, now** → Option **B** (Flutter): port `tlcore` to Dart once, get
     Android + macOS + Windows + Linux from one UI codebase.
   * **Yes, eventually** → Option **A** now (ship a desktop app in ~a week),
     structured so `tlcore` is a clean, fixture-tested Python package; port it
     to Dart when Android actually becomes a task. The UI is the part you throw
     away, and the UI is the cheapest part.
   * **No, desktop is enough** → Option **A**, and the question is closed.
2. ~~**Is the library itself a deliverable** (something others are expected to
   consume), or is it a private boundary?~~ **Answered** — see §8. It is a public
   deliverable: `pip install open-real-link` → `import tlcore`. The remaining
   question is only how much API surface to *stabilise*, which is a matter of
   when, not whether.
3. **CLI/TUI first, or GUI first?** A CLI over `tlcore` is a few hours and
   doubles as the hardware test harness; it is worth having regardless of the
   eventual UI.
4. **Do we want the core usable from *other* languages** via FFI? If yes, that
   is the argument for **C** (Rust) specifically.
