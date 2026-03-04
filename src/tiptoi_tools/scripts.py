import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias

from tiptoi_tools.binary import (
    OID,
    BinaryReader,
    BinaryWriter,
    hi_u8,
    lo_u8,
    pack_u8,
    pack_u16le,
)


class CompareOp(Enum):
    """Comparison operators used in script conditions."""

    EQ = ("==", b"\xf9\xff")
    NE = ("!=", b"\xff\xff")
    LT = ("<", b"\xfb\xff")
    LE = ("<=", b"\xfe\xff")
    GT = (">", b"\xfa\xff")
    GE = (">=", b"\xfd\xff")
    UNKNOWN = ("??", b"\x00\x00")

    def __new__(cls, symbol: str, opcode: bytes):
        obj = object.__new__(cls)
        obj._value_ = symbol
        obj._opcode = opcode
        return obj

    @property
    def opcode(self) -> bytes:
        return self._opcode

    @classmethod
    def decode(cls, opcode: bytes) -> "CompareOp":
        """Decode a comparison operator from its opcode bytes."""
        for member in cls:
            if member._opcode == opcode:
                return member
        return cls.UNKNOWN

    @classmethod
    def from_symbol(cls, symbol: str) -> "CompareOp":
        """Get CompareOp from its string symbol (e.g., '==' -> EQ)."""
        for member in cls:
            if member.value == symbol:
                return member
        return cls.UNKNOWN


class ArithOp(Enum):
    """Arithmetic operations for script actions."""

    SET = (":=", "SET", b"\xf9\xff")
    INC = ("+=", "INC", b"\xf0\xff")
    DEC = ("-=", "DEC", b"\xf1\xff")
    MUL = ("*=", "MUL", b"\xf2\xff")
    DIV = ("/=", "DIV", b"\xf3\xff")
    MOD = ("%=", "MOD", b"\xf4\xff")
    AND = ("&=", "AND", b"\xf5\xff")
    OR = ("|=", "OR", b"\xf6\xff")
    XOR = ("^=", "XOR", b"\xf7\xff")

    def __new__(cls, symbol: str, name: str, opcode: bytes):
        obj = object.__new__(cls)
        obj._value_ = symbol  # .value returns the symbol (e.g., "+=")
        obj._name_str = name
        obj._opcode = opcode
        return obj

    @property
    def symbol(self) -> str:
        return self._value_

    @property
    def op_name(self) -> str:
        return self._name_str

    @property
    def opcode(self) -> bytes:
        return self._opcode

    @classmethod
    def decode(cls, opcode: bytes) -> "ArithOp | None":
        """Decode arithmetic op from opcode bytes. Returns None if not found."""
        for member in cls:
            if member._opcode == opcode:
                return member
        return None

    @classmethod
    def from_symbol(cls, symbol: str) -> "ArithOp | None":
        """Get ArithOp from its symbol (e.g., '+=' -> INC)."""
        for member in cls:
            if member.value == symbol:
                return member
        return None

    @classmethod
    def from_name(cls, name: str) -> "ArithOp | None":
        """Get ArithOp from its internal name (e.g., 'INC' -> INC)."""
        for member in cls:
            if member._name_str == name:
                return member
        return None


class ActionKind(Enum):
    """Types of actions that can be executed by scripts."""

    # Audio playback
    PLAY_MEDIA = ("PlayMedia", b"\xe8\xff")
    PLAY_MEDIA_RANGE = ("PlayMediaRange", b"\x00\xfc")
    PLAY_RANDOM_IN_RANGE = ("PlayRandomInRange", b"\x00\xfb")
    PLAY_VARIANT_RANDOM = ("PlayVariantRandom", b"\xe0\xff")
    PLAY_VARIANT_ALL = ("PlayVariantAll", b"\xe1\xff")

    # Control flow
    JUMP = ("Jump", b"\xff\xf8")
    START_GAME = ("StartGame", b"\x00\xfd")
    CANCEL = ("Cancel", b"\xff\xfa")

    # Registers and timers
    SET_TIMER = ("SetTimer", b"\x00\xff")
    NEGATE_REGISTER = ("NegateRegister", b"\xf8\xff")
    ARITHMETIC = ("Arithmetic", None)

    # Fallback
    UNKNOWN = ("Unknown", None)

    def __new__(cls, name: str, opcode: bytes | None):
        obj = object.__new__(cls)
        obj._value_ = name
        obj._opcode = opcode
        return obj

    @property
    def opcode(self) -> bytes | None:
        return self._opcode

    @classmethod
    def decode(cls, opcode: bytes) -> "ActionKind | None":
        """Decode an action kind from its opcode bytes. Returns None if not found."""
        for member in cls:
            if member._opcode == opcode:
                return member
        return None


@dataclass(frozen=True)
class ScriptValue:
    """A value in a script: either a register reference or a literal constant."""

    is_register: bool
    raw: int

    def __str__(self) -> str:
        return f"${self.raw}" if self.is_register else str(self.raw)

    def serialize(self) -> str:
        """Format as either a register reference ($N) or literal."""
        return f"${self.raw}" if self.is_register else str(self.raw)

    @classmethod
    def deserialize(cls, s: str) -> "ScriptValue":
        """Parse a value like '$0' (register) or '5' (constant)."""
        s = s.strip()
        if s.startswith("$"):
            return cls(is_register=True, raw=int(s[1:]))
        return cls(is_register=False, raw=int(s))

    def encode(self) -> bytes:
        """Encode as 3 bytes: tag (0=reg, 1=const) + u16le value."""
        tag = 0 if self.is_register else 1
        return pack_u8(tag) + pack_u16le(self.raw)

    @classmethod
    def encode_const(cls, value: int) -> bytes:
        """Encode a constant value."""
        return cls(is_register=False, raw=value).encode()


# Type alias for Action payloads (defined after ScriptValue for forward reference)
ActionPayload: TypeAlias = (
    int | ScriptValue | tuple[int, int] | tuple[ArithOp, int, ScriptValue] | None | Any
)


@dataclass(frozen=True)
class Condition:
    """A condition that must be met for a script line to execute."""

    left: ScriptValue
    op: CompareOp
    right: ScriptValue

    def __str__(self) -> str:
        return f"{self.left}{self.op.value}{self.right}"

    def serialize(self) -> str:
        """Serialize as 'left op right?' syntax."""
        left = self.left.serialize()
        right = self.right.serialize()
        op = self.op.value
        # Add a space after single-character operators (< and >) for readability
        if op in ("<", ">"):
            return f"{left}{op} {right}?"
        return f"{left}{op}{right}?"

    @classmethod
    def deserialize(cls, cond_str: str) -> "Condition | None":
        """Parse a condition like '$0==5' or '$1> 10'."""
        for op_str in ["==", "!=", ">=", "<=", ">", "<"]:
            if op_str in cond_str:
                left_str, right_str = cond_str.split(op_str, 1)
                left = ScriptValue.deserialize(left_str.strip())
                right = ScriptValue.deserialize(right_str.strip())
                return cls(left, CompareOp.from_symbol(op_str), right)
        return None


@dataclass(frozen=True)
class Action:
    """An action to execute when a script line's conditions are met."""

    kind: ActionKind
    register: int = 0
    payload: ActionPayload = None

    def encode(self) -> bytes:
        """Encode this Action to binary format."""
        kind = self.kind
        reg = self.register
        payload = self.payload

        if kind == ActionKind.PLAY_MEDIA:
            val = ScriptValue.encode_const(payload)
            return pack_u16le(0) + kind.opcode + val

        if kind == ActionKind.PLAY_MEDIA_RANGE:
            start, end = payload
            val = ScriptValue.encode_const((start << 8) | end)
            return pack_u16le(0) + kind.opcode + val

        if kind == ActionKind.PLAY_RANDOM_IN_RANGE:
            start, end = payload
            val = ScriptValue.encode_const((start << 8) | end)
            return pack_u16le(0) + kind.opcode + val

        if kind == ActionKind.PLAY_VARIANT_RANDOM:
            return pack_u16le(0) + kind.opcode + payload.encode()

        if kind == ActionKind.PLAY_VARIANT_ALL:
            return pack_u16le(0) + kind.opcode + payload.encode()

        if kind == ActionKind.JUMP:
            return pack_u16le(0) + kind.opcode + payload.encode()

        if kind == ActionKind.START_GAME:
            val = ScriptValue.encode_const(payload)
            return pack_u16le(0) + kind.opcode + val

        if kind == ActionKind.CANCEL:
            val = ScriptValue.encode_const(0xFFFF)
            return pack_u16le(0) + kind.opcode + val

        if kind == ActionKind.SET_TIMER:
            return pack_u16le(reg) + kind.opcode + payload.encode()

        if kind == ActionKind.NEGATE_REGISTER:
            val = ScriptValue.encode_const(0)
            return pack_u16le(reg) + kind.opcode + val

        if kind == ActionKind.ARITHMETIC:
            arith_op, _, rhs = payload
            return pack_u16le(reg) + arith_op.opcode + rhs.encode()

        # Unknown action - try to reconstruct from payload
        if kind == ActionKind.UNKNOWN and payload:
            hexcode, reg_idx, val = payload
            opcode = bytes.fromhex(hexcode)
            return pack_u16le(reg_idx) + opcode + val.encode()

        raise ValueError(f"Cannot encode action kind: {kind}")

    @classmethod
    def decode(
        cls,
        r: "ScriptReader",
        kind: ActionKind,
        reg_index: int,
        opcode: bytes,
    ) -> "Action":
        """Decode an action from binary data."""
        if kind in (ActionKind.PLAY_VARIANT_RANDOM, ActionKind.PLAY_VARIANT_ALL):
            return cls(kind=kind, payload=r.script_value())

        if kind == ActionKind.PLAY_MEDIA:
            return cls(kind=kind, payload=r.const_value())

        if kind in (ActionKind.PLAY_RANDOM_IN_RANGE, ActionKind.PLAY_MEDIA_RANGE):
            packed = r.const_value()
            return cls(kind=kind, payload=(hi_u8(packed), lo_u8(packed)))

        if kind == ActionKind.CANCEL:
            arg = r.const_value()
            if arg != 0xFFFF:
                raise ValueError(f"Non-0xFFFF argument to Cancel at 0x{r.offset:08X}")
            return cls(kind=kind)

        if kind == ActionKind.START_GAME:
            return cls(kind=kind, payload=r.const_value())

        if kind == ActionKind.JUMP:
            return cls(kind=kind, payload=r.script_value())

        if kind == ActionKind.SET_TIMER:
            return cls(kind=kind, register=reg_index, payload=r.script_value())

        if kind == ActionKind.NEGATE_REGISTER:
            r.script_value()  # format consumes one value (ignored)
            return cls(kind=kind, register=reg_index)

        # Unknown action type
        rhs = r.script_value()
        return cls(
            kind=ActionKind.UNKNOWN,
            register=reg_index,
            payload=(opcode.hex(), reg_index, rhs),
        )


@dataclass(frozen=True)
class ScriptLine:
    """A single line in a script: conditions, actions, and audio links."""

    offset: int = 0
    conditions: tuple[Condition, ...] = ()
    actions: tuple[Action, ...] = ()
    audio_links: tuple[int, ...] = ()

    def serialize(self) -> str:
        """Serialize this script line: conditions followed by actions."""
        head = " ".join(c.serialize() for c in self.conditions)
        if not self.actions:
            return head
        acts = " ".join(self._serialize_action(a) for a in self.actions)
        return acts if not head else f"{head} {acts}"

    def _serialize_action(self, action: Action) -> str:
        """Serialize an action to script syntax."""
        kind, reg, payload = action.kind, action.register, action.payload

        if kind == ActionKind.CANCEL:
            return "C"
        if kind == ActionKind.PLAY_MEDIA:
            idx = int(payload)
            if idx < len(self.audio_links):
                return f"P({self.audio_links[idx]})"
            return ""  # Invalid index - omit
        if kind == ActionKind.START_GAME:
            return f"G({int(payload)})"
        if kind == ActionKind.JUMP:
            return f"J({payload.serialize()})"
        if kind == ActionKind.PLAY_MEDIA_RANGE:
            start, end = payload
            if end < len(self.audio_links):
                media = ",".join(str(m) for m in self.audio_links[start : end + 1])
                return f"P({media})"
            return f"P*({start},{end})"  # Fallback: show raw range
        if kind == ActionKind.PLAY_RANDOM_IN_RANGE:
            start, end = payload
            if end < len(self.audio_links):
                media = ",".join(str(m) for m in self.audio_links[start : end + 1])
                return f"PA({media})"
            return f"PA*({start},{end})"  # Fallback: show raw range
        if kind == ActionKind.NEGATE_REGISTER:
            return f"${reg}:=-${reg}"
        if kind == ActionKind.SET_TIMER:
            return f"T(${reg},{payload.serialize()})"
        if kind == ActionKind.ARITHMETIC:
            arith_op, reg_idx, val = payload
            return f"${reg_idx}{arith_op.symbol}{val.serialize()}"
        if kind == ActionKind.PLAY_VARIANT_RANDOM:
            return self._serialize_play_variant(payload, "P*")
        if kind == ActionKind.PLAY_VARIANT_ALL:
            return self._serialize_play_variant(payload, "PA*")
        if kind == ActionKind.UNKNOWN:
            hexcode, reg_idx, val = payload
            hexbytes = bytes.fromhex(hexcode)
            hex_str = " ".join(f"{b:02X}" for b in hexbytes)
            return f"?(${reg_idx},{val.serialize()}) ({hex_str})"

        return kind.value

    def _serialize_play_variant(self, payload: ScriptValue, prefix: str) -> str:
        """Serialize PlayVariantRandom/All using audio links from the line."""
        if payload.is_register:
            return f"{prefix}(${payload.raw})"

        links = self.audio_links
        if not links:
            return f"{prefix}({payload.raw})"

        links_str = ",".join(str(m) for m in links)
        if payload.raw == 0:
            return f"{prefix}({links_str})"
        return f"{prefix}({links_str})({payload.raw})"

    @classmethod
    def decode(cls, data: bytes, offset: int) -> "ScriptLine":
        """Decode a script line from binary data."""
        line_offset = offset
        r = ScriptReader(data, offset)

        # Decode conditions
        conditions: list[Condition] = []
        for _ in range(r.u16()):
            left = r.script_value()
            op = CompareOp.decode(r.bytes(2))
            right = r.script_value()
            conditions.append(Condition(left=left, op=op, right=right))

        # Decode actions
        actions: list[Action] = []
        for _ in range(r.u16()):
            reg_index = r.u16()
            opcode = r.bytes(2)

            if (kind := ActionKind.decode(opcode)) is not None:
                actions.append(Action.decode(r, kind, reg_index, opcode))
            elif (arith_op := ArithOp.decode(opcode)) is not None:
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

        return cls(
            offset=line_offset,
            conditions=tuple(conditions),
            actions=tuple(actions),
            audio_links=tuple(audio_links),
        )

    @classmethod
    def deserialize(cls, line_str: str) -> "ScriptLine":
        """Parse a single script line."""
        conditions: list[Condition] = []
        actions: list[Action] = []
        audio_links: list[int] = []
        parts = line_str.split()

        i = 0
        while i < len(parts):
            part = parts[i]

            # Condition (ends with ?)
            if part.endswith("?"):
                cond = Condition.deserialize(part[:-1])
                if cond:
                    conditions.append(cond)
                i += 1
                continue

            # Multi-token condition (e.g., "$0<" "5?")
            if i + 1 < len(parts) and parts[i + 1].endswith("?"):
                combined = part + parts[i + 1][:-1]
                cond = Condition.deserialize(combined)
                if cond:
                    conditions.append(cond)
                    i += 2
                    continue

            # Action
            result = cls._deserialize_action(part, parts, i, audio_links)
            if result:
                action, consumed = result
                actions.append(action)
                i += consumed
                continue

            i += 1

        return cls(
            conditions=tuple(conditions),
            actions=tuple(actions),
            audio_links=tuple(audio_links),
        )

    @classmethod
    def _deserialize_action(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int] | None:
        """Parse an action, returning (Action, tokens_consumed) or None."""
        # Dispatch table: (matcher, handler)
        def paren(prefix: str):
            return lambda p: p.startswith(prefix) and p.endswith(")")

        dispatch = [
            (lambda p: p.startswith("P*("), cls._deserialize_play_variant_random),
            (lambda p: p.startswith("PA*("), cls._deserialize_play_variant_all),
            (paren("P("), cls._deserialize_play_media),
            (paren("PA("), cls._deserialize_play_random),
            (paren("G("), cls._deserialize_start_game),
            (paren("J("), cls._deserialize_jump),
            (lambda p: p == "C", cls._deserialize_cancel),
            (paren("T("), cls._deserialize_set_timer),
            (
                lambda p: p.startswith("$") and any(op.symbol in p for op in ArithOp),
                cls._deserialize_arithmetic,
            ),
        ]

        for matcher, parser in dispatch:
            if matcher(part):
                return parser(part, parts, i, audio_links)

        # TODO: Handle ActionKind.NEGATE_REGISTER
        return None

    @classmethod
    def _deserialize_play_variant_random(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int] | None:
        return cls._deserialize_play_variant(
            part, parts, i, audio_links, ActionKind.PLAY_VARIANT_RANDOM
        )

    @classmethod
    def _deserialize_play_variant_all(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int] | None:
        return cls._deserialize_play_variant(
            part, parts, i, audio_links, ActionKind.PLAY_VARIANT_ALL
        )

    @classmethod
    def _deserialize_play_media(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int]:
        content = part[2:-1]
        indices = [int(x.strip()) for x in content.split(",")]
        start_idx = len(audio_links)
        audio_links.extend(indices)
        if len(indices) == 1:
            return Action(ActionKind.PLAY_MEDIA, payload=start_idx), 1
        end_idx = len(audio_links) - 1
        return Action(ActionKind.PLAY_MEDIA_RANGE, payload=(start_idx, end_idx)), 1

    @classmethod
    def _deserialize_play_random(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int]:
        content = part[3:-1]
        indices = [int(x.strip()) for x in content.split(",")]
        start_idx = len(audio_links)
        audio_links.extend(indices)
        end_idx = len(audio_links) - 1
        return Action(ActionKind.PLAY_RANDOM_IN_RANGE, payload=(start_idx, end_idx)), 1

    @classmethod
    def _deserialize_start_game(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int]:
        game_id = int(part[2:-1])
        return Action(ActionKind.START_GAME, payload=game_id), 1

    @classmethod
    def _deserialize_jump(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int]:
        target = ScriptValue.deserialize(part[2:-1])
        return Action(ActionKind.JUMP, payload=target), 1

    @classmethod
    def _deserialize_cancel(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int]:
        return Action(ActionKind.CANCEL), 1

    @classmethod
    def _deserialize_set_timer(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int]:
        content = part[2:-1]
        reg_str, val_str = content.split(",")
        reg = int(reg_str.strip()[1:])
        val = ScriptValue.deserialize(val_str.strip())
        return Action(ActionKind.SET_TIMER, register=reg, payload=val), 1

    @classmethod
    def _deserialize_arithmetic(
        cls, part: str, parts: list[str], i: int, audio_links: list[int]
    ) -> tuple[Action, int] | None:
        for arith_op in ArithOp:
            if arith_op.symbol in part:
                left, right = part.split(arith_op.symbol, 1)
                reg = int(left[1:])
                val = ScriptValue.deserialize(right)
                payload = (arith_op, reg, val)
                return Action(ActionKind.ARITHMETIC, register=reg, payload=payload), 1
        return None

    @classmethod
    def _deserialize_play_variant(
        cls,
        part: str,
        parts: list[str],
        i: int,
        audio_links: list[int],
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
            offset_val = ScriptValue.deserialize(offset_str)
        else:
            offset_val = ScriptValue(is_register=False, raw=0)

        if content.startswith("$"):
            val = ScriptValue.deserialize(content)
            return Action(kind, payload=val), tokens_consumed

        indices = [int(x.strip()) for x in content.split(",") if x.strip()]
        audio_links.extend(indices)
        return Action(kind, payload=offset_val), tokens_consumed

    def encode(self) -> bytes:
        """Encode this ScriptLine to binary format."""
        parts = []

        # Conditions: u16 count + encoded conditions
        parts.append(pack_u16le(len(self.conditions)))
        for cond in self.conditions:
            parts.append(cond.left.encode() + cond.op.opcode + cond.right.encode())

        # Actions: u16 count + encoded actions
        parts.append(pack_u16le(len(self.actions)))
        for action in self.actions:
            parts.append(action.encode())

        # Audio links: u16 count + u16 indices
        parts.append(pack_u16le(len(self.audio_links)))
        for idx in self.audio_links:
            parts.append(pack_u16le(idx))

        return b"".join(parts)


@dataclass(frozen=True)
class Script:
    """A script containing a sequence of script lines for an OID."""

    lines: tuple[ScriptLine, ...]

    def __iter__(self):
        return iter(self.lines)

    def __len__(self) -> int:
        return len(self.lines)

    def __getitem__(self, index: int) -> ScriptLine:
        return self.lines[index]

    def __bool__(self) -> bool:
        return len(self.lines) > 0

    @classmethod
    def decode(cls, data: bytes, offset: int) -> "Script":
        """Decode a script from binary data at the given offset."""
        if offset <= 0 or offset + 2 > len(data):
            return cls(lines=())

        r = ScriptReader(data, offset)
        n_lines = r.u16()

        lines: list[ScriptLine] = []
        for line_off in r.u32_array(n_lines):
            if 0 < line_off < len(data):
                lines.append(ScriptLine.decode(data, line_off))
        return cls(lines=tuple(lines))

    @classmethod
    def deserialize(cls, data: str | list[str]) -> "Script":
        """Parse a script from YAML format (string or list of strings)."""
        if isinstance(data, str):
            if data.strip():
                return cls(lines=(ScriptLine.deserialize(data),))
            return cls(lines=())
        elif isinstance(data, list):
            lines = [
                ScriptLine.deserialize(str(line) if line else "")
                for line in data
            ]
            return cls(lines=tuple(lines))
        return cls(lines=())

    def serialize(self) -> str | list[str]:
        """Serialize to YAML format: single line as string, multiple as list."""
        if not self.lines:
            return []
        serialized = [line.serialize() for line in self.lines]
        return serialized[0] if len(serialized) == 1 else serialized

    def encode(self, w: BinaryWriter) -> None:
        """Encode this script to binary format."""
        w.u16(len(self.lines))

        # Write pointer placeholders
        pointer_base = w.offset
        for _ in self.lines:
            w.u32(0)

        # Write each line and patch its pointer
        for i, line in enumerate(self.lines):
            w.u32_at(pointer_base + i * 4, w.offset)
            w.bytes(line.encode())


@dataclass(frozen=True)
class ScriptTable:
    """Decoded script table with OID range and scripts."""

    first_oid: OID
    last_oid: OID
    scripts: dict[OID, Script | None]
    active_oids: list[OID]
    game_starters: list[tuple[OID, int]]  # (script_oid, game_id)

    @classmethod
    def decode(cls, data: bytes, offset: int) -> "ScriptTable":
        """
        Decode the script table from GME binary data.

        Returns a ScriptTable containing the OID range and a mapping from
        OID (object identifier) to its script.
        OIDs with no script (null pointer) map to None.
        """
        if offset <= 0 or offset + 8 > len(data):
            return cls(
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

        scripts: dict[OID, Script | None] = {}
        for i, ptr in enumerate(r.u32_array(count)):
            oid = OID(first_code + i)
            if ptr in (0x00000000, 0xFFFFFFFF) or ptr >= len(data):
                scripts[oid] = None
            else:
                scripts[oid] = Script.decode(data, ptr)

        return cls._from_scripts(OID(first_code), OID(last_code), scripts)

    @classmethod
    def deserialize(cls, scripts_data: dict) -> "ScriptTable":
        """Parse scripts from YAML and create a ScriptTable."""
        scripts: dict[OID, Script | None] = {}
        for oid_str, data in scripts_data.items():
            oid = OID(oid_str)
            scripts[oid] = Script.deserialize(data)

        first_oid = OID(min(scripts.keys())) if scripts else OID(0)
        last_oid = OID(max(scripts.keys())) if scripts else OID(0)

        return cls._from_scripts(first_oid, last_oid, scripts)

    @classmethod
    def _from_scripts(
        cls,
        first_oid: OID,
        last_oid: OID,
        scripts: dict[OID, Script | None],
    ) -> "ScriptTable":
        """Create a ScriptTable, computing active_oids and game_starters."""
        active_oids: list[OID] = []
        game_starters: list[tuple[OID, int]] = []
        for oid, script in scripts.items():
            if script:
                active_oids.append(oid)
                for line in script.lines:
                    for act in line.actions:
                        if act.kind == ActionKind.START_GAME:
                            game_starters.append((oid, int(act.payload)))

        return cls(
            first_oid=first_oid,
            last_oid=last_oid,
            scripts=scripts,
            active_oids=active_oids,
            game_starters=game_starters,
        )

    def encode(self, w: BinaryWriter) -> None:
        """Encode this ScriptTable to binary format."""
        # Header: last_oid, padding, first_oid, padding
        w.u16(self.last_oid)
        w.u16(0)
        w.u16(self.first_oid)
        w.u16(0)

        # Write pointer placeholders for each OID
        pointer_base = w.offset
        count = self.last_oid - self.first_oid + 1
        for _ in range(count):
            w.u32(0)

        # Write each script and patch its pointer
        for i in range(count):
            oid = OID(self.first_oid + i)
            script = self.scripts.get(oid)
            if script:
                w.u32_at(pointer_base + i * 4, w.offset)
                script.encode(w)
            else:
                w.u32_at(pointer_base + i * 4, 0xFFFFFFFF)

    def serialize(self) -> dict[str, str | list[str]]:
        """
        Serialize scripts to YAML format.

        Single-line scripts are output as strings, multi-line as lists.
        OIDs are sorted as strings (matching tttool behavior).
        """
        out: dict[str, str | list[str]] = {}

        for oid in sorted(self.scripts.keys(), key=lambda k: str(k)):
            script = self.scripts[oid]
            if not script:
                continue
            serialized = script.serialize()
            if serialized:
                out[str(oid)] = serialized

        return out


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
