import struct
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias, Any

from tiptoi_tools.binary import OID, BinaryReader, BinaryWriter, hi_u8, lo_u8


@dataclass(frozen=True)
class ScriptValue:
    """A value in a script: either a register reference or a literal constant."""

    is_register: bool
    raw: int

    def __str__(self) -> str:
        return f"${self.raw}" if self.is_register else str(self.raw)


class CompareOp(Enum):
    """Comparison operators used in script conditions."""

    EQ = "=="
    GT = ">"
    LT = "<"
    GE = ">="
    LE = "<="
    NE = "!="
    UNKNOWN = "??"


@dataclass(frozen=True)
class Condition:
    """A condition that must be met for a script line to execute."""

    left: ScriptValue
    op: CompareOp
    right: ScriptValue

    def __str__(self) -> str:
        return f"{self.left}{self.op.value}{self.right}"


class ActionKind(Enum):
    """Types of actions that can be executed by scripts."""

    PLAY_MEDIA = "PlayMedia"
    PLAY_MEDIA_RANGE = "PlayMediaRange"
    PLAY_RANDOM_IN_RANGE = "PlayRandomInRange"
    PLAY_VARIANT_RANDOM = "PlayVariantRandom"
    PLAY_VARIANT_ALL = "PlayVariantAll"
    JUMP = "Jump"
    START_GAME = "StartGame"
    CANCEL = "Cancel"
    SET_TIMER = "SetTimer"
    NEGATE_REGISTER = "NegateRegister"
    ARITHMETIC = "Arithmetic"
    UNKNOWN = "Unknown"


# TODO: ARITHMETIC should not be Any
ActionPayload: TypeAlias = (
    int | ScriptValue | tuple[int, int] | tuple[str, int, ScriptValue] | None | Any
)


@dataclass(frozen=True)
class Action:
    """An action to execute when a script line's conditions are met."""

    kind: ActionKind
    register: int = 0
    payload: ActionPayload = None


@dataclass(frozen=True)
class ScriptLine:
    """A single line in a script: conditions, actions, and audio links."""

    offset: int = 0
    conditions: list[Condition] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    audio_links: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class ScriptTable:
    """Decoded script table with OID range and scripts."""

    first_oid: OID
    last_oid: OID
    scripts: dict[OID, list[ScriptLine] | None]
    active_oids: list[OID]
    game_starters: list[tuple[OID, int]]  # (script_oid, game_id)


_COMPARE_DEFS: list[tuple[bytes, CompareOp]] = [
    (b"\xf9\xff", CompareOp.EQ),
    (b"\xfa\xff", CompareOp.GT),
    (b"\xfb\xff", CompareOp.LT),
    (b"\xfd\xff", CompareOp.GE),
    (b"\xfe\xff", CompareOp.LE),
    (b"\xff\xff", CompareOp.NE),
]

_ACTION_DEFS: list[tuple[bytes, ActionKind]] = [
    (b"\xe8\xff", ActionKind.PLAY_MEDIA),
    (b"\x00\xfc", ActionKind.PLAY_MEDIA_RANGE),
    (b"\x00\xfb", ActionKind.PLAY_RANDOM_IN_RANGE),
    (b"\xe0\xff", ActionKind.PLAY_VARIANT_RANDOM),
    (b"\xe1\xff", ActionKind.PLAY_VARIANT_ALL),
    (b"\xff\xf8", ActionKind.JUMP),
    (b"\x00\xfd", ActionKind.START_GAME),
    (b"\xff\xfa", ActionKind.CANCEL),
    (b"\x00\xff", ActionKind.SET_TIMER),
    (b"\xf8\xff", ActionKind.NEGATE_REGISTER),
]

# Arithmetic ops: (bytes, internal_name, yaml_symbol)
_ARITH_DEFS: list[tuple[bytes, str, str]] = [
    (b"\xf0\xff", "INC", "+="),
    (b"\xf1\xff", "DEC", "-="),
    (b"\xf2\xff", "MUL", "*="),
    (b"\xf3\xff", "DIV", "/="),
    (b"\xf4\xff", "MOD", "%="),
    (b"\xf5\xff", "AND", "&="),
    (b"\xf6\xff", "OR", "|="),
    (b"\xf7\xff", "XOR", "^="),
    (b"\xf9\xff", "SET", ":="),
]

# Derived decode mappings (bytes -> value)
_COMPARE_OPS: dict[bytes, CompareOp] = {code: op for code, op in _COMPARE_DEFS}
_ACTION_OPS: dict[bytes, ActionKind] = {code: kind for code, kind in _ACTION_DEFS}
_ARITH_OPS: dict[bytes, str] = {code: name for code, name, _ in _ARITH_DEFS}
_ARITH_TO_SYM: dict[str, str] = {name: sym for _, name, sym in _ARITH_DEFS}

# Derived encode mappings (value -> bytes)
COMPARE_OP_ENCODE: dict[str, bytes] = {op.value: code for code, op in _COMPARE_DEFS}
ACTION_OP_ENCODE: dict[ActionKind, bytes] = {kind: code for code, kind in _ACTION_DEFS}
ARITH_OP_ENCODE: dict[str, bytes] = {sym: code for code, _, sym in _ARITH_DEFS}

# TODO: Integrate within CompareOp logic
# Map string operators to CompareOp enum
STR_TO_COMPARE_OP: dict[str, CompareOp] = {op.value: op for op in CompareOp}

# Map command kind strings to ActionKind enum
ACTION_KIND_MAP: dict[str, ActionKind] = {
    "Play": ActionKind.PLAY_MEDIA,
    "Random": ActionKind.PLAY_RANDOM_IN_RANGE,
    "PlayAll": ActionKind.PLAY_MEDIA_RANGE,
    "Game": ActionKind.START_GAME,
    "Jump": ActionKind.JUMP,
    "Cancel": ActionKind.CANCEL,
    "Timer": ActionKind.SET_TIMER,
    "Neg": ActionKind.NEGATE_REGISTER,
    "Arithmetic": ActionKind.ARITHMETIC,
    "PlayVariantRandom": ActionKind.PLAY_VARIANT_RANDOM,
    "PlayVariantAll": ActionKind.PLAY_VARIANT_ALL,
}

# Map operator symbols to internal names for arithmetic actions
SYM_TO_OP_NAME: dict[str, str] = {
    ":=": "SET",
    "+=": "INC",
    "-=": "DEC",
    "*=": "MUL",
    "/=": "DIV",
    "%=": "MOD",
    "&=": "AND",
    "|=": "OR",
    "^=": "XOR",
}


class ScriptReader(BinaryReader):
    """BinaryReader extended with script-specific parsing methods."""

    def script_value(self) -> ScriptValue:
        """Read a script value (register reference or constant)."""
        tag = self.u8()
        if tag not in (0, 1):
            raise ValueError(f"Unknown ScriptValue tag {tag} at 0x{self.offset:08X}")
        value = self.u16()
        return ScriptValue(is_register=(tag == 0), raw=value)

    def const_value(self) -> int:
        """Read a value that must be a constant (not a register reference)."""
        start = self.offset
        value = self.script_value()
        if value.is_register:
            raise ValueError(f"Expected Const, got Reg at 0x{start:08X}")
        return value.raw


def decode(data: bytes, offset: int) -> ScriptTable:
    """
    Decode the script table from GME binary data.

    Returns a ScriptTable containing the OID range and a mapping from
    OID (object identifier) to its script lines.
    OIDs with no script (null pointer) map to None.
    """
    if offset <= 0 or offset + 8 > len(data):
        return ScriptTable(
            first_oid=OID(0),
            last_oid=OID(0),
            scripts={},
            active_oids=[],
            game_starters=[],
        )

    r = ScriptReader(data, offset)
    last_code = r.u16()
    r.u16()  # skip padding
    first_code = r.u16()
    r.u16()  # skip padding

    count = last_code - first_code + 1
    if r.offset + 4 * count > len(data):
        count = max(0, (len(data) - r.offset) // 4)

    scripts: dict[OID, list[ScriptLine] | None] = {}
    for i, ptr in enumerate(r.u32_array(count)):
        oid = OID(first_code + i)
        if ptr in (0x00000000, 0xFFFFFFFF) or ptr >= len(data):
            scripts[oid] = None
        else:
            scripts[oid] = _decode_script(data, ptr)

    # Precompute active_oids and game_starters
    active_oids: list[OID] = []
    game_starters: list[tuple[OID, int]] = []
    for oid, lines in scripts.items():
        if lines:
            active_oids.append(oid)
            for line in lines:
                for act in line.actions:
                    if act.kind == ActionKind.START_GAME:
                        game_starters.append((oid, int(act.payload)))

    return ScriptTable(
        first_oid=OID(first_code),
        last_oid=OID(last_code),
        scripts=scripts,
        active_oids=active_oids,
        game_starters=game_starters,
    )


def encode(w: BinaryWriter, script_table: ScriptTable) -> None:
    """
    Encode a ScriptTable to binary format.

    Args:
        w: BinaryWriter to write to
        script_table: ScriptTable containing scripts to encode
    """
    first_oid = script_table.first_oid
    last_oid = script_table.last_oid

    # Header: last_oid, padding, first_oid, padding
    w.u16(last_oid)
    w.u16(0)
    w.u16(first_oid)
    w.u16(0)

    # Write pointer placeholders for each OID
    pointer_base = w.offset
    count = last_oid - first_oid + 1
    for _ in range(count):
        w.u32(0)

    # Write each script and patch its pointer
    for i in range(count):
        oid = OID(first_oid + i)
        lines = script_table.scripts.get(oid)
        if lines:
            w.u32_at(pointer_base + i * 4, w.offset)
            _encode_script(w, lines)
        else:
            w.u32_at(pointer_base + i * 4, 0xFFFFFFFF)


def serialize(
    scripts: dict[int, list[ScriptLine] | None],
) -> dict[str, str | list[str]]:
    """
    Serialize decoded scripts to YAML format.

    Single-line scripts are output as strings, multi-line as lists.
    OIDs are sorted as strings (matching tttool behavior).
    """
    out: dict[str, str | list[str]] = {}

    for oid in sorted(scripts.keys(), key=lambda k: str(k)):
        lines = scripts[oid]
        if lines is None or lines == []:
            continue
        serialized = [_serialize_line(line) for line in lines]
        out[str(oid)] = serialized[0] if len(serialized) == 1 else serialized

    return out


def deserialize(scripts: dict) -> dict[OID, list[ScriptLine]]:
    """Parse scripts from YAML."""
    result = {}
    for oid_str, lines in scripts.items():
        oid = OID(oid_str)
        if isinstance(lines, str):
            if lines.strip():
                result[oid] = [_deserialize_line(lines)]
            else:
                result[oid] = []
        elif isinstance(lines, list):
            parsed = [
                _deserialize_line(line) for line in lines if line and str(line).strip()
            ]
            result[oid] = parsed
        else:
            result[oid] = []
    return result


def _decode_script(data: bytes, offset: int) -> list[ScriptLine]:
    """Decode a single script (list of lines) from its offset."""
    if offset <= 0 or offset + 2 > len(data):
        return []

    r = ScriptReader(data, offset)
    n_lines = r.u16()

    lines: list[ScriptLine] = []
    for line_off in r.u32_array(n_lines):
        if 0 < line_off < len(data):
            lines.append(_decode_line(data, line_off))
    return lines


def _decode_line(data: bytes, offset: int) -> ScriptLine:
    """Decode a single script line: conditions, actions, and audio links."""
    line_offset = offset
    r = ScriptReader(data, offset)

    # Decode conditions
    conditions: list[Condition] = []
    for _ in range(r.u16()):
        left = r.script_value()
        op = _COMPARE_OPS.get(r.bytes(2), CompareOp.UNKNOWN)
        right = r.script_value()
        conditions.append(Condition(left=left, op=op, right=right))

    # Decode actions
    actions: list[Action] = []
    for _ in range(r.u16()):
        reg_index = r.u16()
        opcode = r.bytes(2)

        if (kind := _ACTION_OPS.get(opcode)) is not None:
            actions.append(_decode_action(r, kind, reg_index, opcode))
        elif (arith_op := _ARITH_OPS.get(opcode)) is not None:
            rhs = r.script_value()
            actions.append(
                Action(
                    kind=ActionKind.ARITHMETIC,
                    register=reg_index,
                    payload=(arith_op, reg_index, rhs),
                )
            )
        else:
            rhs = r.script_value()
            actions.append(
                Action(
                    kind=ActionKind.UNKNOWN,
                    register=reg_index,
                    payload=(opcode.hex(), reg_index, rhs),
                )
            )

    # Decode audio links
    audio_links = r.u16_list()

    return ScriptLine(
        offset=line_offset,
        conditions=conditions,
        actions=actions,
        audio_links=audio_links,
    )


def _decode_action(
    r: ScriptReader,
    kind: ActionKind,
    reg_index: int,
    opcode: bytes,
) -> Action:
    """Decode a single action based on its kind."""
    if kind in (ActionKind.PLAY_VARIANT_RANDOM, ActionKind.PLAY_VARIANT_ALL):
        return Action(kind=kind, payload=r.script_value())

    if kind == ActionKind.PLAY_MEDIA:
        return Action(kind=kind, payload=r.const_value())

    if kind in (ActionKind.PLAY_RANDOM_IN_RANGE, ActionKind.PLAY_MEDIA_RANGE):
        packed = r.const_value()
        # Packed format: hi=start, lo=end, so return (start, end)
        return Action(kind=kind, payload=(hi_u8(packed), lo_u8(packed)))

    if kind == ActionKind.CANCEL:
        arg = r.const_value()
        if arg != 0xFFFF:
            raise ValueError(f"Non-0xFFFF argument to Cancel at 0x{r.offset:08X}")
        return Action(kind=kind)

    if kind == ActionKind.START_GAME:
        return Action(kind=kind, payload=r.const_value())

    if kind == ActionKind.JUMP:
        return Action(kind=kind, payload=r.script_value())

    if kind == ActionKind.SET_TIMER:
        return Action(kind=kind, register=reg_index, payload=r.script_value())

    if kind == ActionKind.NEGATE_REGISTER:
        r.script_value()  # format consumes one value (ignored)
        return Action(kind=kind, register=reg_index)

    # Unknown action type
    rhs = r.script_value()
    return Action(
        kind=ActionKind.UNKNOWN,
        register=reg_index,
        payload=(opcode.hex(), reg_index, rhs),
    )


def _serialize_line(line: ScriptLine) -> str:
    """Serialize a script line: conditions followed by actions."""
    head = " ".join(_serialize_condition(c) for c in line.conditions)
    if not line.actions:
        return head
    acts = " ".join(_serialize_action(line, a) for a in line.actions)
    return acts if not head else f"{head} {acts}"


def _serialize_condition(condition: Condition) -> str:
    """Serialize a condition as 'left op right?' syntax."""
    left = _serialize_value(condition.left)
    right = _serialize_value(condition.right)
    op = condition.op.value
    # Add a space after single-character operators (< and >) for readability
    if op in ("<", ">"):
        return f"{left}{op} {right}?"
    return f"{left}{op}{right}?"


def _serialize_action(line: ScriptLine, action: Action) -> str:
    """Serialize an action to script syntax."""
    kind, reg, payload = action.kind, action.register, action.payload

    if kind == ActionKind.CANCEL:
        return "C"
    if kind == ActionKind.PLAY_MEDIA:
        idx = int(payload)
        if idx < len(line.audio_links):
            return f"P({line.audio_links[idx]})"
        return ""  # Invalid index - omit
    if kind == ActionKind.START_GAME:
        return f"G({int(payload)})"
    if kind == ActionKind.JUMP:
        return f"J({_serialize_value(payload)})"
    if kind == ActionKind.PLAY_MEDIA_RANGE:
        start, end = payload
        if end < len(line.audio_links):
            return f"P({','.join(str(m) for m in line.audio_links[start : end + 1])})"
        return f"P*({start},{end})"  # Fallback: show raw range
    if kind == ActionKind.PLAY_RANDOM_IN_RANGE:
        start, end = payload
        if end < len(line.audio_links):
            return f"PA({','.join(str(m) for m in line.audio_links[start : end + 1])})"
        return f"PA*({start},{end})"  # Fallback: show raw range
    if kind == ActionKind.NEGATE_REGISTER:
        return f"${reg}:=-${reg}"
    if kind == ActionKind.SET_TIMER:
        return f"T(${reg},{_serialize_value(payload)})"
    if kind == ActionKind.ARITHMETIC:
        opname, reg_idx, val = payload
        sym = _ARITH_TO_SYM.get(str(opname), f"{opname}:")
        return f"${reg_idx}{sym}{_serialize_value(val)}"
    if kind == ActionKind.PLAY_VARIANT_RANDOM:
        return _serialize_play_variant(line, payload, "P*")
    if kind == ActionKind.PLAY_VARIANT_ALL:
        return _serialize_play_variant(line, payload, "PA*")
    if kind == ActionKind.UNKNOWN:
        hexcode, reg_idx, val = payload
        hexbytes = bytes.fromhex(hexcode)
        hex_str = " ".join(f"{b:02X}" for b in hexbytes)
        return f"?(${reg_idx},{_serialize_value(val)}) ({hex_str})"

    return kind.value


def _serialize_play_variant(
    line: ScriptLine,
    payload: ScriptValue,
    prefix: str,
) -> str:
    """Serialize PlayVariantRandom/All using audio links from the line."""
    # If payload is a register, format as register reference
    if payload.is_register:
        return f"{prefix}(${payload.raw})"

    # Use all audio links from the line
    links = line.audio_links
    if not links:
        # Fallback if no audio links available
        return f"{prefix}({payload.raw})"

    links_str = ",".join(str(m) for m in links)
    # Only show payload suffix if payload != 0
    if payload.raw == 0:
        return f"{prefix}({links_str})"
    return f"{prefix}({links_str})({payload.raw})"


def _serialize_value(value: ScriptValue) -> str:
    """Format a script value as either a register reference ($N) or literal."""
    return f"${value.raw}" if value.is_register else str(value.raw)


def _encode_script(w: BinaryWriter, lines: list[ScriptLine]) -> None:
    """Encode a single script (list of ScriptLines)."""
    w.u16(len(lines))

    # Write pointer placeholders
    pointer_base = w.offset
    for _ in lines:
        w.u32(0)

    # Write each line and patch its pointer
    for i, line in enumerate(lines):
        w.u32_at(pointer_base + i * 4, w.offset)
        w.bytes(_encode_line(line))


def _encode_line(line: ScriptLine) -> bytes:
    """Encode a ScriptLine to binary format."""
    parts = []

    # Conditions: u16 count + encoded conditions
    parts.append(struct.pack("<H", len(line.conditions)))
    for cond in line.conditions:
        parts.append(
            _encode_value(cond.left)
            + COMPARE_OP_ENCODE[cond.op.value]
            + _encode_value(cond.right)
        )

    # Actions: u16 count + encoded actions
    parts.append(struct.pack("<H", len(line.actions)))
    for action in line.actions:
        parts.append(_encode_action(action))

    # Audio links: u16 count + u16 indices
    parts.append(struct.pack("<H", len(line.audio_links)))
    for idx in line.audio_links:
        parts.append(struct.pack("<H", idx))

    return b"".join(parts)


def _encode_action(action: Action) -> bytes:
    """Encode an Action to binary format."""
    kind = action.kind
    reg = action.register
    payload = action.payload

    if kind == ActionKind.PLAY_MEDIA:
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", 0) + opcode + _encode_const(payload)

    if kind == ActionKind.PLAY_MEDIA_RANGE:
        start, end = payload
        packed = (start << 8) | end
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", 0) + opcode + _encode_const(packed)

    if kind == ActionKind.PLAY_RANDOM_IN_RANGE:
        start, end = payload
        packed = (start << 8) | end
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", 0) + opcode + _encode_const(packed)

    if kind == ActionKind.PLAY_VARIANT_RANDOM:
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", 0) + opcode + _encode_value(payload)

    if kind == ActionKind.PLAY_VARIANT_ALL:
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", 0) + opcode + _encode_value(payload)

    if kind == ActionKind.JUMP:
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", 0) + opcode + _encode_value(payload)

    if kind == ActionKind.START_GAME:
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", 0) + opcode + _encode_const(payload)

    if kind == ActionKind.CANCEL:
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", 0) + opcode + _encode_const(0xFFFF)

    if kind == ActionKind.SET_TIMER:
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", reg) + opcode + _encode_value(payload)

    if kind == ActionKind.NEGATE_REGISTER:
        opcode = ACTION_OP_ENCODE[kind]
        return struct.pack("<H", reg) + opcode + _encode_const(0)

    if kind == ActionKind.ARITHMETIC:
        op_name, _, rhs = payload
        sym = _ARITH_TO_SYM.get(op_name, op_name)
        return struct.pack("<H", reg) + ARITH_OP_ENCODE[sym] + _encode_value(rhs)

    # Unknown action - try to reconstruct from payload
    if kind == ActionKind.UNKNOWN and payload:
        hexcode, reg_idx, val = payload
        opcode = bytes.fromhex(hexcode)
        return struct.pack("<H", reg_idx) + opcode + _encode_value(val)

    raise ValueError(f"Cannot encode action kind: {kind}")


def _encode_value(sv: ScriptValue) -> bytes:
    """Encode a ScriptValue as 3 bytes: tag (0=reg, 1=const) + u16le value."""
    tag = 0 if sv.is_register else 1
    return struct.pack("<BH", tag, sv.raw)


def _encode_const(value: int) -> bytes:
    """Encode a constant value."""
    return _encode_value(ScriptValue(is_register=False, raw=value))


def _deserialize_line(line_str: str) -> ScriptLine:
    """Parse a single script line."""
    line = ScriptLine()
    parts = line_str.split()

    i = 0
    while i < len(parts):
        part = parts[i]

        # Condition (ends with ?)
        if part.endswith("?"):
            cond = _deserialize_condition(part[:-1])
            if cond:
                line.conditions.append(cond)
            i += 1
            continue

        # Multi-token condition (e.g., "$0<" "5?")
        if i + 1 < len(parts) and parts[i + 1].endswith("?"):
            combined = part + parts[i + 1][:-1]
            cond = _deserialize_condition(combined)
            if cond:
                line.conditions.append(cond)
                i += 2
                continue

        # Command
        result = _deserialize_action(part, parts, i, line)
        if result:
            cmd, consumed = result
            line.actions.append(cmd)
            i += consumed
            continue

        i += 1

    return line


def _deserialize_action(
    part: str, parts: list[str], i: int, line: ScriptLine
) -> tuple[Action, int] | None:
    """Parse a command, returning (Command, tokens_consumed) or None."""
    if part.startswith("P*("):
        return _deserialize_play_variant(
            part, parts, i, line, ActionKind.PLAY_VARIANT_RANDOM
        )

    if part.startswith("PA*("):
        return _deserialize_play_variant(
            part, parts, i, line, ActionKind.PLAY_VARIANT_ALL
        )

    if part.startswith("P(") and part.endswith(")"):
        content = part[2:-1]
        indices = [int(x.strip()) for x in content.split(",")]
        start_idx = len(line.audio_links)
        line.audio_links.extend(indices)
        if len(indices) == 1:
            return Action(ActionKind.PLAY_MEDIA, payload=start_idx), 1
        else:
            end_idx = len(line.audio_links) - 1
            return Action(ActionKind.PLAY_MEDIA_RANGE, payload=(start_idx, end_idx)), 1

    if part.startswith("PA(") and part.endswith(")"):
        content = part[3:-1]
        indices = [int(x.strip()) for x in content.split(",")]
        start_idx = len(line.audio_links)
        line.audio_links.extend(indices)
        end_idx = len(line.audio_links) - 1
        return Action(ActionKind.PLAY_RANDOM_IN_RANGE, payload=(start_idx, end_idx)), 1

    if part.startswith("G(") and part.endswith(")"):
        game_id = int(part[2:-1])
        return Action(ActionKind.START_GAME, payload=game_id), 1

    if part.startswith("J(") and part.endswith(")"):
        target = _deserialize_script_value(part[2:-1])
        return Action(ActionKind.JUMP, payload=target), 1

    if part == "C":
        return Action(ActionKind.CANCEL), 1

    if part.startswith("T(") and part.endswith(")"):
        content = part[2:-1]
        reg_str, val_str = content.split(",")
        reg = int(reg_str.strip()[1:])
        val = _deserialize_script_value(val_str.strip())
        return Action(ActionKind.SET_TIMER, register=reg, payload=val), 1

    for op in [":=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="]:
        if op in part and part.startswith("$"):
            left, right = part.split(op, 1)
            reg = int(left[1:])
            val = _deserialize_script_value(right)
            op_name = SYM_TO_OP_NAME.get(op, op)
            return Action(
                ActionKind.ARITHMETIC, register=reg, payload=(op_name, reg, val)
            ), 1

    # TODO: Handle ActionKind.NEGATE_REGISTER

    return None


def _deserialize_play_variant(
    part: str,
    parts: list[str],
    i: int,
    line: ScriptLine,
    kind: ActionKind,
) -> tuple[Action, int] | None:
    """Parse P*(audio)(offset) or P*($reg) commands."""
    pattern = r"P[A]?\*\(([^)]*)\)(?:\(([^)]*)\))?"
    match = re.match(pattern, part)

    if not match:
        return None

    content = match.group(1)
    offset_str = match.group(2)

    tokens_consumed = 1
    if offset_str is None and i + 1 < len(parts):
        next_part = parts[i + 1]
        if next_part.startswith("(") and next_part.endswith(")"):
            offset_str = next_part[1:-1]
            tokens_consumed = 2

    if offset_str:
        offset_val = _deserialize_script_value(offset_str)
    else:
        offset_val = ScriptValue(is_register=False, raw=0)

    if content.startswith("$"):
        val = _deserialize_script_value(content)
        return Action(kind, payload=val), tokens_consumed

    indices = [int(x.strip()) for x in content.split(",") if x.strip()]
    line.audio_links.extend(indices)
    return Action(kind, payload=offset_val), tokens_consumed


def _deserialize_condition(cond_str: str) -> Condition | None:
    """Parse a condition like '$0==5' or '$1> 10'."""
    for op_str in ["==", "!=", ">=", "<=", ">", "<"]:
        if op_str in cond_str:
            left_str, right_str = cond_str.split(op_str, 1)
            left = _deserialize_script_value(left_str.strip())
            right = _deserialize_script_value(right_str.strip())
            return Condition(left, STR_TO_COMPARE_OP[op_str], right)
    return None


def _deserialize_script_value(s: str) -> ScriptValue:
    """Parse a value like '$0' (register) or '5' (constant)."""
    s = s.strip()
    if s.startswith("$"):
        return ScriptValue(is_register=True, raw=int(s[1:]))
    return ScriptValue(is_register=False, raw=int(s))
