"""LG split-AC IR codec (classic 28-bit protocol).

Encodes and decodes state frames and special-function frames for the
6711A20*** / 6711AR2*** remote families and compatible LG splits.

Target: LG A12AHD (AS-H126PDL1), remote 6711A20073Z. Verified against
real captures (see scripts/capture_ir.py).

Frame structure (Arduino-IRremote LGProtocol union):
  bits 27..20  Signature   (0x88)
  bits 19..16  Function    (0=state, 1=swing/jet, 8=timer-on, 9=timer-off,
                            A=sleep, B=clear-all, C=sub-toggle)
  bits 15..04  Payload     (depends on function family)
  bits  3..00  Checksum    (sum of nibbles 27..4, mod 16)

State frames (Function=0):
  bits 19..18  Power       00=ON, 11=OFF (canonical OFF = 0x88C0051)
  bits 17..15  Function    state-change flag (bit 15 set on most user
                           actions; AC accepts both)
  bits 14..12  Mode        0=cool, 1=dry, 2=fan_only, 3=auto, 4=heat
  bits 11..08  Temp        degC - 15  (16..30 degC)
  bits  7..04  Fan         0=lowest, 2=medium, 4=max, 5=auto (variable),
                           0xA=high (newer remotes only)

Sources:
  - IRremoteESP8266 src/ir_LG.{h,cpp}
  - frawau/pyhvac plugins/lg.py
  - Arduino-IRremote src/ac_LG.{h,hpp}
  - Live captures against A12AHD
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
    """LG fan speeds — live-verified mapping on A12AHD.

    Note: pyhvac/IRremoteESP8266 historically labelled fan=5 as "AUTO"
    and fan=4 as "MAX" — confirmed correct on this hardware. The button
    cycle on the 6711A20073Z remote produces (in order): 0, 2, 4, 5,
    matching Lowest -> Medium -> Max -> Auto.
    """

    LOWEST = 0
    LOW = 1       # rare on real remotes; not in 6711A20073Z button cycle
    MEDIUM = 2
    MAX = 4       # constant full speed
    AUTO = 5      # variable, "schwankend"
    HIGH = 0xA    # only on newer InverterV/DualInverter remotes


# ---------------------------------------------------------------------------
# Named hardcoded frames (Function != 0). Verified against pyhvac + IResp8266.
# Each is a single-shot command from the remote, no state encoding.
# ---------------------------------------------------------------------------

# Jet (Powerful) — closes side vents, full-blast downward
JET_ON = 0x8810089
# To exit jet: send any normal state frame (no discrete jet-off code)

# Light / display LED toggle
LIGHT_TOGGLE = 0x88C00A6

# Air purify / Plasmaster / Ionizer
PURIFY_ON = 0x88C000C
PURIFY_OFF = 0x88C0084

# Auto-clean / Self-cleaning (the "face with nose" button — dries the
# evaporator after cool mode to prevent mould)
CLEAN_ON = 0x88C00C8
CLEAN_OFF = 0x88C00B7

# Energy-save modes (inverter models only — may be no-op on A12AHD)
ENERGY_SAVE_OFF = 0x88C07F2
ENERGY_SAVE_80 = 0x88C07D0
ENERGY_SAVE_60 = 0x88C07E1
ENERGY_SAVE_40 = 0x88C0804

# Diagnostic / service mode — DO NOT expose to users
DIAGNOSTIC = 0x88C0CE6

# Vertical swing (Function=0x1, sub-family 0x88130** + 0x8813xxx)
SWING_V_TOGGLE = 0x8810001
SWING_V_LOWEST = 0x8813048
SWING_V_LOW = 0x8813059
SWING_V_MIDDLE = 0x881306A
SWING_V_UPPER_MIDDLE = 0x881307B
SWING_V_HIGH = 0x881308C
SWING_V_HIGHEST = 0x881309D
SWING_V_SWING = 0x8813149   # auto-swing across all positions
SWING_V_OFF = 0x881315A

# Horizontal swing (only on DualInverter / newer remotes, may be no-op on A12AHD)
SWING_H_AUTO = 0x881316B
SWING_H_OFF = 0x881317C

# Timer clear-all
TIMER_CLEAR_ALL = 0x88B000B

# Sleep cancel (also covered by encode_sleep_timer(0))
SLEEP_CANCEL = 0x88A000A


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


def _attach_checksum(frame_no_csum: int) -> int:
    """Pack the low-nibble checksum onto a frame whose low nibble is zero."""
    return (frame_no_csum & 0x0FFFFFF0) | _checksum(frame_no_csum)


def encode(state: LgAcState) -> int:
    """Build the 28-bit frame for a desired AC state."""
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


def encode_sleep_timer(minutes: int) -> int:
    """Build a sleep-timer frame. minutes=0 cancels; range 0..1439.

    The remote button only steps in 60-min increments (1..7 h), but the
    on-air protocol accepts arbitrary minute values.
    """
    if not 0 <= minutes <= 1439:
        raise ValueError(f"sleep minutes {minutes} out of range 0..1439")
    body = 0x88A000 | (minutes & 0xFFF)
    return _attach_checksum(body << 4)


def encode_schedule_timer(*, turn_on: bool, minutes: int) -> int:
    """Build a delayed-on (turn_on=True) or delayed-off schedule frame.

    minutes is the delay until the action, in absolute minutes since
    the remote's internal clock 0 — but in practice these are relative
    minutes (0..1439). For minute = m the on-air payload is m itself.
    """
    if not 0 <= minutes <= 1439:
        raise ValueError(f"schedule minutes {minutes} out of range")
    func_nibble = 0x8 if turn_on else 0x9
    body = (SIGNATURE << 16) | (func_nibble << 12) | (minutes & 0xFFF)
    return _attach_checksum(body << 4)


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
    """Decode raw timings to a 28-bit frame integer, or None."""
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
    """Interpret a 28-bit frame as an AC state, or None.

    Returns None for timer/swing/jet/sub-toggle frames; those carry no
    state and must be handled by the higher-level layer via the named
    constants above.
    """
    if ((frame28 >> 20) & 0xFF) != SIGNATURE:
        return None
    if _checksum(frame28) != (frame28 & 0xF):
        return None

    power_bits = (frame28 >> 18) & 0b11
    if power_bits == 0b11:
        if frame28 == OFF_FRAME:
            return LgAcState(power_on=False)
        return None  # sub-OFF toggle (display/clean/purify) — not a state change
    if power_bits != 0b00:
        return None

    pad_high = (frame28 >> 16) & 0b11
    if pad_high:
        return None  # function frame (swing/jet), not a state frame

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


# ---------------------------------------------------------------------------
# Sleep-timer decoder — recognises 0x88Axxxx frames and returns minutes.
# ---------------------------------------------------------------------------

def decode_sleep_timer(frame28: int) -> int | None:
    """If frame is a sleep-timer frame, return minutes (0..1439); else None."""
    # Sleep family: bits 27..16 must equal 0x88A.
    if ((frame28 >> 16) & 0xFFF) != 0x88A:
        return None
    if _checksum(frame28) != (frame28 & 0xF):
        return None
    return (frame28 >> 4) & 0xFFF


def decode_schedule_timer(frame28: int) -> tuple[bool, int] | None:
    """If frame is a schedule-timer (Func=8 on / 9 off), return (on?, minutes)."""
    if ((frame28 >> 20) & 0xFF) != SIGNATURE:
        return None
    if _checksum(frame28) != (frame28 & 0xF):
        return None
    func = (frame28 >> 16) & 0xF
    if func == 0x8:
        return True, (frame28 >> 4) & 0xFFF
    if func == 0x9:
        return False, (frame28 >> 4) & 0xFFF
    return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # State-frame encodes
    state = LgAcState(power_on=True, mode=Mode.COOL, temp=22, fan=Fan.AUTO)
    f = encode(state)
    assert f == 0x880075C, f"got 0x{f:07X}"
    print(f"encode(ON Cool 22C Auto)    = 0x{f:07X}  OK  (fan=5, variable)")

    f = encode(LgAcState(power_on=True, mode=Mode.COOL, temp=22, fan=Fan.MAX))
    assert f == 0x880074B, f"got 0x{f:07X}"
    print(f"encode(ON Cool 22C Max)     = 0x{f:07X}  OK  (fan=4, constant full)")

    f = encode(LgAcState(power_on=True, mode=Mode.COOL, temp=22, fan=Fan.MEDIUM))
    assert f == 0x8800729, f"got 0x{f:07X}"
    print(f"encode(ON Cool 22C Medium)  = 0x{f:07X}  OK  (fan=2)")

    f = encode(LgAcState(power_on=True, mode=Mode.COOL, temp=22, fan=Fan.LOWEST))
    assert f == 0x8800707, f"got 0x{f:07X}"
    print(f"encode(ON Cool 22C Lowest)  = 0x{f:07X}  OK  (fan=0)")

    assert encode(LgAcState(power_on=False)) == OFF_FRAME
    print(f"encode(OFF)                 = 0x{OFF_FRAME:07X}  OK")

    # Sleep timer round-trip — all live captures must encode identically
    sleep_table = {
        60: 0x88A03C9, 120: 0x88A0789, 180: 0x88A0B49, 240: 0x88A0F09,
        300: 0x88A12C9, 360: 0x88A1689, 420: 0x88A1A49, 0: 0x88A000A,
    }
    for minutes, expected in sleep_table.items():
        got = encode_sleep_timer(minutes)
        assert got == expected, f"sleep {minutes}min: got 0x{got:07X}, exp 0x{expected:07X}"
        m_back = decode_sleep_timer(got)
        assert m_back == minutes, f"sleep decode {got:#x}: got {m_back}, exp {minutes}"
    print("encode_sleep_timer/decode  60min..7h + cancel  OK  (matches live captures)")

    # Round-trip frame ↔ timings
    rt = frame_to_raw_timings(encode(state))
    assert decode_frame(rt) == encode(state)
    print(f"round-trip frame <-> raw timings  OK ({len(rt)} entries)")

    # Decode samples
    samples = [
        (0x88C0051, "OFF (canonical)"),
        (0x8800347, "ON Cool 18C fan=4=Max (real capture, bit15=0)"),
        (0x880075C, "ON Cool 22C fan=5=Auto (encoder output)"),
        (0x880870F, "ON Cool 22C fan=0=Lowest (remote-sent, bit15=1)"),
        (0x880B746, "ON Auto 22C fan=4=Max (real capture)"),
        (0x8810089, "Jet ON  -> not a state frame"),
        (0x88A03C9, "Sleep 1h  -> not a state frame"),
        (0x88C00C8, "Clean ON  -> not a state frame"),
        (0x88C0051, "OFF canonical  -> power_on=False"),
    ]
    for frame, label in samples:
        s = decode_state(frame)
        print(f"decode(0x{frame:07X})  {label:50s}  -> {s}")

    # Sub-OFF codes must NOT be reported as off
    assert decode_state(0x88C00C8) is None, "Clean ON leaks as power-off"
    assert decode_state(0x88C000C) is None, "Purify ON leaks as power-off"
    assert decode_state(OFF_FRAME) == LgAcState(power_on=False)

    # Named-frame integrity
    print("\nNamed frames (checksum verification):")
    for name in [
        "JET_ON", "LIGHT_TOGGLE", "PURIFY_ON", "PURIFY_OFF",
        "CLEAN_ON", "CLEAN_OFF", "SWING_V_TOGGLE", "SWING_V_MIDDLE",
        "SWING_V_SWING", "SWING_H_AUTO", "SWING_H_OFF", "TIMER_CLEAR_ALL",
        "SLEEP_CANCEL",
    ]:
        v = globals()[name]
        expected_csum = _checksum(v)
        actual_csum = v & 0xF
        ok = "OK" if expected_csum == actual_csum else "BAD"
        print(f"  {name:22s} = 0x{v:07X}  csum={ok}")
