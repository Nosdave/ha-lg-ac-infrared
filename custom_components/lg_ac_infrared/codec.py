"""LG split-AC IR codec (classic 28-bit protocol).

Encodes and decodes state frames for the 6711A20*** / 6711AR2*** remote
families and compatible LG splits.

Target: LG A12AHD (AS-H126PDL1), remote 6711A20073Z. Verified against
real captures (see scripts/capture_ir.py). No external dependencies.

Frame layout (28 bits, MSB first on the air):
  27..20  Sign         constant 0x88
  19..18  Power        00 = ON, 11 = OFF (sent as hardcoded OFF_FRAME)
  17..15  Function     0 = state frame (our encoder always sets 0).
                       Bit 15 alone = "manual override" set by the remote
                       on modifications — accepted equally on receive.
                       Bits 16/17 set => swing/timer/sub-OFF frame.
  14..12  Mode         0=cool, 1=dry, 2=fan_only, 3=auto, 4=heat
  11..8   Temperature  (degC - 15), valid 1..15  (16..30 degC)
  7..4    Fan          0=auto, 2=low, 4=high (constant), 5=powerful (jet/auto-high)
                       Note: Arduino-IRremote AC_FAN_WALL labels 5 as "high",
                       but live-tested on A12AHD: 4 is constant-full, 5 is the
                       variable "Powerful"/Jet mode. Tower-mount models use
                       AC_FAN_TOWER {0,4,6,6} — not supported by this codec.
  3..0    Checksum     sum of nibbles over bits 4..19 (4 nibbles), & 0xF

Special frames recognised as "not a state change":
  Power-bits 01 / 10  -> timer / function modes
  0x88C0051           -> canonical power-off
  Other 0x88C00__     -> display/light/beep sub-toggles (treated as OFF)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

SIGNATURE = 0x88
OFF_FRAME = 0x88C0051
TEMP_MIN = 16
TEMP_MAX = 30
TEMP_OFFSET = 15

CARRIER_HZ = 38000

HEADER_MARK = 8500
HEADER_SPACE = 4250
BIT_MARK = 560
ONE_SPACE = 1600
ZERO_SPACE = 560
TRAILING_MARK = 560


class Mode(IntEnum):
    COOL = 0
    DRY = 1
    FAN_ONLY = 2
    AUTO = 3
    HEAT = 4


class Fan(IntEnum):
    AUTO = 0      # variable, adapts to load (the "schwankende" speed)
    LOW = 2
    MEDIUM = 3    # tentative — captured cycle on A12AHD skips this value;
                  # may not exist on this model. UI exposes it; falls back
                  # to nearest level on hardware that ignores it.
    HIGH = 4      # constant full speed (verified on A12AHD)
    POWERFUL = 5  # "Jet" mode — closes side vents, full-blast downward
                  # (Arduino-IRremote calls this "high" in AC_FAN_WALL)


@dataclass(frozen=True)
class LgAcState:
    power_on: bool
    mode: Mode | None = None
    temp: int | None = None
    fan: Fan | None = None


def _checksum(frame_without_csum: int) -> int:
    body = (frame_without_csum >> 4) & 0xFFFF
    s = 0
    for _ in range(4):
        s += body & 0xF
        body >>= 4
    return s & 0xF


def encode(state: LgAcState) -> int:
    if not state.power_on:
        return OFF_FRAME
    if state.mode is None or state.temp is None or state.fan is None:
        raise ValueError("on-frame requires mode, temp, fan")
    if not TEMP_MIN <= state.temp <= TEMP_MAX:
        raise ValueError(f"temp {state.temp} out of range {TEMP_MIN}..{TEMP_MAX}")

    code = (SIGNATURE & 0xFF) << 20
    code |= (int(state.mode) & 0b111) << 12
    code |= ((state.temp - TEMP_OFFSET) & 0xF) << 8
    code |= (int(state.fan) & 0xF) << 4
    code |= _checksum(code)
    return code & 0x0FFFFFFF


def frame_to_raw_timings(frame28: int) -> list[int]:
    """Convert a 28-bit frame to ESPHome raw timings.

    Positive = mark (LED on, microseconds).
    Negative = space (LED off, microseconds).
    """
    out: list[int] = [HEADER_MARK, -HEADER_SPACE]
    for i in range(27, -1, -1):
        bit = (frame28 >> i) & 1
        out.append(BIT_MARK)
        out.append(-(ONE_SPACE if bit else ZERO_SPACE))
    out.append(TRAILING_MARK)
    return out


def decode_frame(timings: list[int]) -> int | None:
    """Decode raw timings to a 28-bit frame integer, or None.

    Tolerant of ~25% timing jitter. Ignores anything past the 28th bit.
    """
    if len(timings) < 2 + 28 * 2:
        return None
    if not (7000 <= timings[0] <= 10000):
        return None
    if not (-5500 <= timings[1] <= -3500):
        return None
    bits = 0
    for i in range(28):
        space_us = -timings[2 + 2 * i + 1]
        bits = (bits << 1) | (1 if space_us > 1000 else 0)
    return bits


def decode_state(frame28: int) -> LgAcState | None:
    """Interpret a 28-bit frame as an AC state.

    Returns None for timer/swing/function frames that don't change
    the main state. Returns LgAcState(power_on=False) for any OFF
    or sub-OFF code (display toggle, beep, etc.).
    """
    if ((frame28 >> 20) & 0xFF) != SIGNATURE:
        return None
    if _checksum(frame28) != (frame28 & 0xF):
        return None

    power_bits = (frame28 >> 18) & 0b11
    if power_bits == 0b11:
        # Only the canonical OFF frame really turns the AC off.
        # Other 0x88C00XX codes are display/light/beep/sleep toggles
        # that share the power-OFF bit pattern but do not change power
        # state — ignore so we do not falsely report OFF in HA.
        if frame28 == OFF_FRAME:
            return LgAcState(power_on=False)
        return None
    if power_bits != 0b00:
        return None

    pad_high = (frame28 >> 16) & 0b11
    if pad_high:
        return None

    mode_raw = (frame28 >> 12) & 0b111
    try:
        mode = Mode(mode_raw)
    except ValueError:
        return None

    temp = ((frame28 >> 8) & 0xF) + TEMP_OFFSET
    if not TEMP_MIN <= temp <= TEMP_MAX:
        return None

    fan_raw = (frame28 >> 4) & 0xF
    try:
        fan = Fan(fan_raw)
    except ValueError:
        return None

    return LgAcState(power_on=True, mode=mode, temp=temp, fan=fan)


if __name__ == "__main__":
    # With bit15=0 (clean state frame): Cool/22C/Auto -> 0x8800707
    # The remote sends 0x880870F (bit15=1, "manual override").
    # AC accepts both — captured frame #22 (0x8800347) shows bit15=0 in real use.
    state = LgAcState(power_on=True, mode=Mode.COOL, temp=22, fan=Fan.AUTO)
    f = encode(state)
    assert f == 0x8800707, f"got 0x{f:07X}"
    print(f"encode(ON Cool 22C Auto)     = 0x{f:07X}  OK")

    f = encode(LgAcState(power_on=True, mode=Mode.COOL, temp=22, fan=Fan.HIGH))
    assert f == 0x880074B, f"got 0x{f:07X}"
    print(f"encode(ON Cool 22C High)     = 0x{f:07X}  OK (constant-full, verified)")

    f = encode(LgAcState(power_on=True, mode=Mode.COOL, temp=22, fan=Fan.POWERFUL))
    assert f == 0x880075C, f"got 0x{f:07X}"
    print(f"encode(ON Cool 22C Powerful) = 0x{f:07X}  OK (variable Jet mode)")

    assert encode(LgAcState(power_on=False)) == OFF_FRAME
    print(f"encode(OFF)              = 0x{OFF_FRAME:07X}  OK")

    rt = frame_to_raw_timings(f)
    assert decode_frame(rt) == f, "round-trip failed"
    print(f"round-trip frame <-> raw timings  OK ({len(rt)} entries)")

    # Sanity-check against real captures.
    samples = [
        (0x88C0051, "OFF (canonical)"),
        (0x8800347, "ON Cool 18C fan=4 (real capture, bit15=0)"),
        (0x8800707, "ON Cool 22C Auto (encoder output)"),
        (0x880870F, "ON Cool 22C Auto (remote-sent, bit15=1)"),
        (0x880B746, "ON Auto 22C fan=4 (real capture)"),
        (0x8810001, "Swing-V Toggle  -> should be None"),
        (0x88A03C9, "Timer-Mode      -> should be None"),
        (0x88C00C8, "Sub-OFF (display) -> ignored (None, not OFF)"),
    ]
    for frame, label in samples:
        s = decode_state(frame)
        print(f"decode(0x{frame:07X})  {label:50s}  -> {s}")

    # Sub-OFF codes must NOT be reported as off
    assert decode_state(0x88C00C8) is None, "sub-OFF code leaks as power-off"
    assert decode_state(OFF_FRAME) == LgAcState(power_on=False)
