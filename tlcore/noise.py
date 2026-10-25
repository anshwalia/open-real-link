"""The noise-control vocabulary: the six mode values, and the read selectors.

``0x0404 action=1`` sets the **active mode**; the device echoes the same value
back on ``0x010C 01 01 -> 0x810C 00 01 01 <V>``.  The value->effect mapping was
confirmed on a real Air7 Pro **by ear** and is not a static inference.

Writes are gated on in-ear detection: when the buds are not worn the device
answers ``0x8404 0e``.  Surface that to the user rather than reporting failure.
"""
from __future__ import annotations

NOISE_MODE_NORMAL = 0x01        # "Normal Mode" / noise off
NOISE_MODE_TRANSPARENCY = 0x02  # transparency / ambient passthrough
NOISE_MODE_ANC = 0x20           # ANC (smart reduction) -- the default ANC level
NOISE_MODE_ANC_LIGHT = 0x04     # ANC level: light reduction
NOISE_MODE_ANC_DEEP = 0x08      # ANC level: deep reduction
NOISE_MODE_ANC_MEDIUM = 0x10    # ANC level: medium reduction
NOISE_MODE_ANC_SMART = 0x20     # ANC level: smart/adaptive reduction (== NOISE_MODE_ANC)

# Human labels -- from the Air7 Pro RUS config item types and the value
# ordering in ItemFactory.Z (MODE_LIST, settingType 35) / ItemFactory.Y
# (MODE radio, settingType 2), both of which feed the same O5 write.
NOISE_MODE_NAMES = {
    NOISE_MODE_NORMAL: "Normal (noise off)",
    NOISE_MODE_TRANSPARENCY: "Transparency",
    NOISE_MODE_ANC_LIGHT: "ANC light",
    NOISE_MODE_ANC_DEEP: "ANC deep",
    NOISE_MODE_ANC_MEDIUM: "ANC medium",
    NOISE_MODE_ANC_SMART: "ANC smart",
}

# `0x0404` action=2 writes the *cycle* settings (separate field, read via
# `0x010C 02 01`); see docs/protocol.md §8.1.
NOISE_FIELD_MODE = 0x01  # active mode  -> 0x010C 01 01
NOISE_FIELD_CYCLE = 0x02  # mode cycle   -> 0x010C 02 01
