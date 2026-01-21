from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from tiptoi_tools.binary import BinaryReader, hi_u8, lo_u8


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


ActionPayload: TypeAlias = (
    int | ScriptValue | tuple[int, int] | tuple[str, int, ScriptValue] | None
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

    offset: int
    conditions: list[Condition]
    actions: list[Action]
    audio_links: list[int]


@dataclass(frozen=True)
class ScriptTable:
    """Decoded script table with OID range and scripts."""

    first_oid: int
    last_oid: int
    scripts: dict[int, list[ScriptLine] | None]
    active_oids: list[int]
    game_starters: list[tuple[int, int]]  # (script_oid, game_id)


# Binary opcodes for comparison operators
_COMPARE_OPS: dict[bytes, CompareOp] = {
    b"\xf9\xff": CompareOp.EQ,
    b"\xfa\xff": CompareOp.GT,
    b"\xfb\xff": CompareOp.LT,
    b"\xfd\xff": CompareOp.GE,
    b"\xfe\xff": CompareOp.LE,
    b"\xff\xff": CompareOp.NE,
}

# Binary opcodes for action types
_ACTION_OPS: dict[bytes, ActionKind] = {
    b"\xe8\xff": ActionKind.PLAY_MEDIA,
    b"\x00\xfc": ActionKind.PLAY_MEDIA_RANGE,
    b"\x00\xfb": ActionKind.PLAY_RANDOM_IN_RANGE,
    b"\xe0\xff": ActionKind.PLAY_VARIANT_RANDOM,
    b"\xe1\xff": ActionKind.PLAY_VARIANT_ALL,
    b"\xff\xf8": ActionKind.JUMP,
    b"\x00\xfd": ActionKind.START_GAME,
    b"\xff\xfa": ActionKind.CANCEL,
    b"\x00\xff": ActionKind.SET_TIMER,
    b"\xf8\xff": ActionKind.NEGATE_REGISTER,
}

# Binary opcodes for arithmetic operations
_ARITH_OPS: dict[bytes, str] = {
    b"\xf0\xff": "INC",
    b"\xf1\xff": "DEC",
    b"\xf2\xff": "MUL",
    b"\xf3\xff": "DIV",
    b"\xf4\xff": "MOD",
    b"\xf5\xff": "AND",
    b"\xf6\xff": "OR",
    b"\xf7\xff": "XOR",
    b"\xf9\xff": "SET",
}

# Mapping from arithmetic operation names to YAML output symbols
_ARITH_TO_SYM: dict[str, str] = {
    "INC": "+=",
    "DEC": "-=",
    "MUL": "*=",
    "DIV": "/=",
    "MOD": "%=",
    "AND": "&=",
    "OR": "|=",
    "XOR": "^=",
    "SET": ":=",
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
            first_oid=0, last_oid=0, scripts={}, active_oids=[], game_starters=[]
        )

    r = ScriptReader(data, offset)
    last_code = r.u16()
    r.u16()  # skip padding
    first_code = r.u16()
    r.u16()  # skip padding

    count = last_code - first_code + 1
    if r.offset + 4 * count > len(data):
        count = max(0, (len(data) - r.offset) // 4)

    scripts: dict[int, list[ScriptLine] | None] = {}
    for i, ptr in enumerate(r.u32_array(count)):
        oid = first_code + i
        if ptr in (0x00000000, 0xFFFFFFFF) or ptr >= len(data):
            scripts[oid] = None
        else:
            scripts[oid] = _decode_script(data, ptr)

    # Precompute active_oids and game_starters
    active_oids: list[int] = []
    game_starters: list[tuple[int, int]] = []
    for oid, lines in scripts.items():
        if lines:
            active_oids.append(oid)
            for line in lines:
                for act in line.actions:
                    if act.kind == ActionKind.START_GAME:
                        game_starters.append((oid, int(act.payload)))

    return ScriptTable(
        first_oid=first_code,
        last_oid=last_code,
        scripts=scripts,
        active_oids=active_oids,
        game_starters=game_starters,
    )


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
        return f"?(${reg_idx},{_serialize_value(val)}) ({' '.join(f'{b:02X}' for b in hexbytes)})"

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
