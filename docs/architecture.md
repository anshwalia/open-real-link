# OpenRealLink — architecture: **core library + app**

Status: **decided.** Both the shape of the code and the stack are settled. This
document covers the shape; [`stack.md`](stack.md) covers the stack.

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
│  tltransport  macos.py · linux.py · windows.py ·         │
│  per-platform android/ · replay.py                       │
└─────────────────────────────────────────────────────────┘
```

**`tlcore` imports nothing platform-specific.** No `IOBluetooth`, no `socket`,
no `serial`, no Qt. It takes a `Transport` object and produces/consumes bytes.

> Only `macos.py` and `replay.py` exist today; `linux.py`, `windows.py` and
> `android/` in the diagram are the planned per-platform shims (see §9).

That rule is the whole point of the split, so it is *enforced* rather than
intended: the test suite imports `tlcore` in a clean subprocess and fails if any
platform binding ends up in `sys.modules`. The rule is easy to break by accident
— one convenient `import serial` deep inside a decoder — and impossible to notice
by reading.

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
  "watch until the buds raise the ACL" pattern depends on being able to poll link
  state without putting anything on the radio.
* **`read(timeout)` must pump the platform run loop**, not sleep. On macOS a
  bare `sleep` starves `IOBluetoothRFCOMMChannel` and you get zero bytes — this
  cost real debugging time.
* **`open()` must be able to fail with a *refusal* distinct from a *timeout*.**
  `0xE00002BC` (channel refused — recoverable) and `0xE00002D6` (page timeout —
  buds asleep) mean opposite things; collapsing them loses the diagnosis. Note
  that macOS returns these as *signed* IOReturns, so compare against
  `& 0xFFFFFFFF`.

---

## 4. Why this pays off: the replay transport

`tltransport/replay.py` implements `Transport` over a recorded capture
(`fixtures/*.jsonl`). **The entire app — protocol, state machine, UI — can then
run with no hardware present.**

That buys three things:

1. **Byte-exact regression tests** in CI, on any machine.
2. **Contributors without the buds** can work on the app.
3. **UI development stops depending on a device being worn and awake** — which,
   given the wear-gating (`0x8404 0e`) and the case/sleep behaviour, is a real
   tax on development speed.

The golden fixtures already exist: `fixtures/mode-cycle.jsonl` and
`fixtures/spp-smoketest.jsonl`, recorded from a real Buds Air7 Pro. They carry
only a timestamp, a direction and hex, so they contain no device address and are
safe to publish.

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
| **how bytes physically move** | `tltransport` |
| layout, widgets, wording, accessibility | `app` |

---

## 6. What we deliberately do **not** do

* **One distribution, published late.** The repository is a monorepo, but only
  the core is a package: `open-real-link`, which is `tlcore`. The app ships from
  the same repository and is not published separately, so installing the library
  never drags in a UI toolkit. Release when the API stops moving — a published
  API you then change is worse than no package.
* **No plugin architecture.** One transport is selected at runtime by platform;
  there is no registry, no entry points, no dynamic loading.
* **No "extract the library later".** Retrofitting the boundary is exactly the
  work this avoids.
* **No vendor server, no account, no telemetry** — and that is a property of the
  core, not a UI setting.

---

## 7. The stack

**Python 3.9+ throughout, PySide6 (Qt 6) for the UI, built CLI-first.** The
reasoning, the constraint that forced it and the alternatives that lost are in
[`stack.md`](stack.md).

The layering above is language-agnostic, which is what keeps this decision cheap
if it ever needs revisiting. It is nevertheless not expected to change, and
`tlcore` is imported directly rather than ported — the protocol work is reused
verbatim.

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

The distribution and import names are meant to differ once the package is
published (it is not on PyPI yet; see the README for the source install):

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

### 8.3 The core publishes alone

`open-real-link` is the core. The app ships from the same repository but is not a
separate distribution, so `pip install open-real-link` gives you the protocol and
nothing else. If the protocol is ever wanted as a name in its own right, the
alternative worth considering is `tl-headset-protocol`, which trades
discoverability by project name for discoverability by protocol name.

---

## 9. Build order

1. **`tlcore`** — **done.** Ported from the validated reference by range
   extraction, verified byte-for-byte against it (23,162 checks, `PARITY: OK`),
   no dependencies, no platform imports. `session.py` adds the request/response
   state machine, and `backup.py` adds settings backup/restore on top of the
   read-side decoders.
2. **`tltransport`** — **done.** `macos.py` fitted to the six-method contract in
   §3, and `replay.py` over `fixtures/*.jsonl`, which needs no hardware at all.
   The macOS transport has been run end to end from this codebase on a device
   (read-only `smoke` plus the six-mode write, see [`live-test.md`](live-test.md)).
3. **`tlcli`** — **done.** A CLI over `tlcore` + `tltransport`; it doubles as the
   hardware test harness. Run it from a checkout with `python -m tlcli`
   (`info`, `eq`, `mode`, `smoke`, `send`, and `backup`/`restore`).
4. **PySide6 app** — **next.** scan/pair, connect, noise selector, battery/wear,
   capability view, EQ, paired list, raw console.

Steps 1–3 are the protocol being made usable. Step 4 is a shell over them, and it
is the only step that gets thrown away if the stack ever changes.
