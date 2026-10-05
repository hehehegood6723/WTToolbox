"""War Thunder ``.blk`` configuration parser with byte-exact round-tripping.

The game stores its settings in a small s-expression-ish format::

    language:t="Chinese"
    firstRun:b=no
    ssaa:r=1
    forcedLauncher:i=0

    video{
      mode:t="fullscreen"
      vsync:b=no
    }

Design goals
------------
The game rewrites ``config.blk`` on exit, and users are (rightly) nervous about a
third-party tool mangling it.  This module therefore **never** re-serialises the
document from the parsed tree.  It keeps the original text and records every
change as a splice, so untouched bytes survive verbatim - including comments,
odd whitespace and keys this tool does not understand.

Parsing is a hand written scanner rather than a regex pass, because a value may
contain braces, ``:`` or ``=`` inside a quoted string.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterator, Sequence, Union

__all__ = [
    "BlkParam",
    "BlkBlock",
    "BlkDocument",
    "BlkError",
    "BOOL_TYPES",
    "INT_TYPES",
    "FLOAT_TYPES",
    "STRING_TYPES",
    "VECTOR_TYPES",
    "format_value",
    "format_float",
    "quote",
    "unquote",
]


class BlkError(ValueError):
    """Raised when a document cannot be parsed or saved."""


BOOL_TYPES = frozenset({"b"})
INT_TYPES = frozenset({"i", "l", "u"})
FLOAT_TYPES = frozenset({"r", "f"})
STRING_TYPES = frozenset({"t", "s"})
VECTOR_TYPES = frozenset({"p2", "p3", "p4", "c", "ip2", "ip3", "ip4"})

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TYPE_RE = re.compile(r"(?P<type>[A-Za-z][A-Za-z0-9]*)\s*=")


# --------------------------------------------------------------------------- #
#  Value helpers
# --------------------------------------------------------------------------- #
def unquote(raw: str) -> str:
    """Strip surrounding quotes and resolve backslash escapes."""
    s = raw.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s.replace('\\"', '"').replace("\\\\", "\\")


def quote(text: str) -> str:
    """Render a Python string as a ``t`` value literal."""
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_float(value: float) -> str:
    """Match the game's taste for short decimals (``1`` rather than ``1.0``)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "0"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    text = f"{f:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def format_value(type_code: str | None, value) -> str:
    """Render a Python value as blk source text (without the key)."""
    t = (type_code or "t").lower()
    if t in BOOL_TYPES:
        if isinstance(value, str):
            return "yes" if value.strip().lower() in {"yes", "true", "1", "y"} else "no"
        return "yes" if value else "no"
    if t in INT_TYPES:
        try:
            return str(int(round(float(value))))
        except (TypeError, ValueError):
            return "0"
    if t in FLOAT_TYPES:
        return format_float(value)
    if t in VECTOR_TYPES:
        if isinstance(value, (list, tuple)):
            return ", ".join(format_float(v) for v in value)
        return str(value)
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value
    return quote(value)


# --------------------------------------------------------------------------- #
#  Tree nodes
# --------------------------------------------------------------------------- #
@dataclass
class BlkParam:
    """A single ``key:type=value`` entry."""

    key: str
    type: str | None
    raw_value: str
    """Value exactly as written, including quotes for ``t`` values."""
    key_start: int = -1
    key_end: int = -1
    value_start: int = -1
    value_end: int = -1
    block: "BlkBlock | None" = None

    @property
    def full_type(self) -> str:
        return (self.type or "t").lower()

    @property
    def decoded(self):
        """Decode the raw value into a Python object."""
        t = self.full_type
        raw = self.raw_value
        if t in BOOL_TYPES:
            return raw.strip().lower() in {"yes", "true", "1", "y"}
        if t in INT_TYPES:
            try:
                return int(float(raw.strip()))
            except ValueError:
                return 0
        if t in FLOAT_TYPES:
            try:
                return float(raw.strip())
            except ValueError:
                return 0.0
        if t in VECTOR_TYPES:
            out = []
            for part in raw.split(","):
                try:
                    out.append(float(part.strip()))
                except ValueError:
                    out.append(0.0)
            return out
        return unquote(raw)

    python_value = decoded

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        t = f":{self.type}" if self.type else ""
        return f"<BlkParam {self.key}{t}={self.raw_value!r}>"


@dataclass
class BlkBlock:
    """A named block (``video{ ... }``) or the document root."""

    name: str | None
    parent: "BlkBlock | None" = None
    params: list[BlkParam] = field(default_factory=list)
    blocks: list["BlkBlock"] = field(default_factory=list)
    open_brace: int = -1
    close_brace: int = -1
    key_start: int = -1
    key_end: int = -1
    depth: int = 0
    synthetic: bool = False
    """True for blocks this session created; they exist only in the splice list."""
    synth_lines: list["Union[str, BlkBlock]"] = field(default_factory=list)
    """Ordered body of a synthetic block: raw source lines and nested blocks."""

    # ------------------------------------------------------------------ lookup
    def param(self, key: str) -> BlkParam | None:
        want = key.lower()
        for p in self.params:
            if p.key.lower() == want:
                return p
        return None

    def block(self, name: str) -> "BlkBlock | None":
        want = name.lower()
        for b in self.blocks:
            if (b.name or "").lower() == want:
                return b
        return None

    @property
    def path(self) -> tuple[str, ...]:
        parts: list[str] = []
        node: BlkBlock | None = self
        while node is not None and node.name is not None:
            parts.append(node.name)
            node = node.parent
        return tuple(reversed(parts))

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<BlkBlock {'/'.join(self.path) or '<root>'} "
            f"params={len(self.params)} blocks={len(self.blocks)}>"
        )


# --------------------------------------------------------------------------- #
#  Splice operations
# --------------------------------------------------------------------------- #
def render_synthetic(block: BlkBlock, indent: str) -> str:
    """Render a session-created block and everything nested inside it."""
    body = []
    for item in block.synth_lines:
        if isinstance(item, str):
            body.append(item)
        else:
            body.append(render_synthetic(item, indent + "  "))
    return f"{indent}{block.name}{{\n{''.join(body)}{indent}}}\n"


class _Splice:
    """A pending text edit anchored to offsets in the original document."""

    __slots__ = ("start", "end", "text", "serial")

    def __init__(self, start: int, end: int, text: str, serial: int) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.serial = serial

    def render(self) -> str:
        return self.text


class _NewBlockSplice(_Splice):
    """Insertion point for a session-created block."""

    __slots__ = ("block", "indent", "prefix_newline")

    def __init__(
        self,
        anchor: int,
        indent: str,
        block: BlkBlock,
        serial: int,
        *,
        prefix_newline: bool,
    ) -> None:
        super().__init__(anchor, anchor, "", serial)
        self.block = block
        self.indent = indent
        self.prefix_newline = prefix_newline

    def render(self) -> str:
        lead = "\n" if self.prefix_newline else ""
        return lead + render_synthetic(self.block, self.indent)


# --------------------------------------------------------------------------- #
#  Document
# --------------------------------------------------------------------------- #
class BlkDocument:
    """A parsed ``.blk`` file that can be edited and written back losslessly."""

    def __init__(
        self, text: str, *, encoding: str = "utf-8", path: str | None = None
    ) -> None:
        self.original_text = text
        self.encoding = encoding
        self.path = path
        self._splices: list[_Splice] = []
        self._serial = 0
        self.root = BlkBlock(name=None, depth=0, open_brace=-1, close_brace=len(text))
        self._parse()

    # ------------------------------------------------------------------- load
    @classmethod
    def load(cls, path, *, encoding: str = "utf-8") -> "BlkDocument":
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return cls(
            raw.decode(encoding, errors="replace"),
            encoding=encoding,
            path=os.fspath(path),
        )

    # ------------------------------------------------------------------ parse
    def _parse(self) -> None:
        text = self.original_text
        n = len(text)
        i = 0
        stack: list[BlkBlock] = [self.root]

        while i < n:
            ch = text[i]

            if ch in " \t\r\n":
                i += 1
                continue

            if text.startswith("//", i):
                nl = text.find("\n", i)
                i = n if nl < 0 else nl + 1
                continue
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                i = n if end < 0 else end + 2
                continue

            if ch == "}":
                if len(stack) > 1:
                    stack.pop().close_brace = i
                i += 1
                continue

            m = _IDENT_RE.match(text, i)
            if not m:
                i += 1
                continue

            ident = m.group(0)
            ident_start, ident_end = m.start(), m.end()
            j = ident_end
            while j < n and text[j] in " \t":
                j += 1

            if j < n and text[j] == "{":
                block = BlkBlock(
                    name=ident,
                    parent=stack[-1],
                    open_brace=j,
                    close_brace=-1,
                    key_start=ident_start,
                    key_end=ident_end,
                    depth=len(stack),
                )
                stack[-1].blocks.append(block)
                stack.append(block)
                i = j + 1
                continue

            if j < n and text[j] == ":":
                tm = _TYPE_RE.match(text, j + 1)
                if tm:
                    i = self._read_param(
                        text, stack[-1], ident, tm.group("type"),
                        ident_start, ident_end, tm.end(), n,
                    )
                    continue

            if j < n and text[j] == "=":
                i = self._read_param(
                    text, stack[-1], ident, None,
                    ident_start, ident_end, j + 1, n,
                )
                continue

            nl = text.find("\n", i)
            i = n if nl < 0 else nl + 1

    @staticmethod
    def _read_param(
        text: str,
        block: BlkBlock,
        ident: str,
        type_code: str | None,
        key_start: int,
        key_end: int,
        value_start: int,
        n: int,
    ) -> int:
        k = value_start
        while k < n and text[k] in " \t":
            k += 1

        value_begin = k

        if k < n and text[k] == '"':
            k += 1
            while k < n:
                if text[k] == "\\":
                    k += 2
                    continue
                if text[k] == '"':
                    k += 1
                    break
                if text[k] == "\n":
                    break
                k += 1
            value_end = k
            i = k
        else:
            end = k
            while end < n and text[end] not in "\r\n":
                end += 1
            line = text[k:end]
            cut = line.find("//")
            if cut >= 0:
                line = line[:cut]
            value_end = k + len(line.rstrip())
            i = end

        block.params.append(
            BlkParam(
                key=ident,
                type=type_code,
                raw_value=text[value_begin:value_end].strip(),
                key_start=key_start,
                key_end=key_end,
                value_start=value_begin,
                value_end=value_end,
                block=block,
            )
        )
        return i

    # ----------------------------------------------------------------- lookup
    def block(self, path: str | Sequence[str]) -> BlkBlock | None:
        parts = [path] if isinstance(path, str) else list(path)
        node: BlkBlock | None = self.root
        for part in parts:
            if node is None:
                return None
            node = node.block(part)
        return node

    def param(self, path: str | Sequence[str], key: str) -> BlkParam | None:
        block = self.block(path)
        return block.param(key) if block else None

    def value(self, path: str | Sequence[str], key: str, default=None):
        p = self.param(path, key)
        return default if p is None else p.decoded

    def walk_params(self) -> Iterator[BlkParam]:
        def rec(block: BlkBlock) -> Iterator[BlkParam]:
            yield from block.params
            for child in block.blocks:
                yield from rec(child)

        yield from rec(self.root)

    def walk_blocks(self) -> Iterator[BlkBlock]:
        def rec(block: BlkBlock) -> Iterator[BlkBlock]:
            for child in block.blocks:
                yield child
                yield from rec(child)

        yield from rec(self.root)

    # ------------------------------------------------------------------- edit
    @property
    def dirty(self) -> bool:
        return bool(self._splices)

    def set_raw(self, path: str | Sequence[str], key: str, raw_value: str) -> bool:
        """Splice a new literal in place of an existing value."""
        p = self.param(path, key)
        if p is None or p.raw_value == raw_value:
            return False
        self._serial += 1
        self._splices.append(_Splice(p.value_start, p.value_end, raw_value, self._serial))
        p.raw_value = raw_value
        return True

    def set_value(self, path: str | Sequence[str], key: str, value) -> bool:
        p = self.param(path, key)
        if p is None:
            return False
        return self.set_raw(path, key, format_value(p.type, value))

    def ensure(
        self,
        path: str | Sequence[str],
        key: str,
        type_code: str,
        value,
        *,
        comment: str | None = None,
    ) -> bool:
        """Set a parameter, creating it (and any missing block) when absent."""
        block_path = [path] if isinstance(path, str) else list(path)
        raw = format_value(type_code, value)

        block = self.block(block_path)
        if block is None:
            block = self._make_block(block_path)

        if block.param(key) is not None:
            return self.set_raw(block_path, key, raw)

        if block.synthetic:
            indent = "  " * block.depth
        else:
            indent = self._indent_for(block)

        line = f"{indent}{key}:{type_code}={raw}"
        if comment:
            line += f"   // {comment}"
        line += "\n"

        block.params.append(BlkParam(key=key, type=type_code, raw_value=raw, block=block))

        if block.synthetic:
            block.synth_lines.append(line)
            return True

        anchor = self._anchor_for(block)
        if anchor > 0 and self.original_text[anchor - 1] not in "\r\n":
            line = "\n" + line
        self._serial += 1
        self._splices.append(_Splice(anchor, anchor, line, self._serial))
        return True

    # ----------------------------------------------------------- edit plumbing
    def _anchor_for(self, block: BlkBlock) -> int:
        return len(self.original_text) if block.close_brace < 0 else block.close_brace

    def _indent_for(self, block: BlkBlock) -> str:
        """Reproduce the indentation used by a block's existing children."""
        text = self.original_text
        for p in block.params:
            if p.key_start < 0:
                continue
            ls = text.rfind("\n", 0, p.key_start) + 1
            indent = text[ls:p.key_start]
            if indent.strip() == "":
                return indent
        for b in block.blocks:
            if b.key_start >= 0:
                ls = text.rfind("\n", 0, b.key_start) + 1
                indent = text[ls:b.key_start]
                if indent.strip() == "":
                    return indent
        return "  " * block.depth

    def _make_block(self, path: Sequence[str]) -> BlkBlock:
        parent = self.root
        for name in path:
            nxt = parent.block(name)
            if nxt is None:
                nxt = self._append_block(parent, name)
            parent = nxt
        return parent

    def _append_block(self, parent: BlkBlock, name: str) -> BlkBlock:
        block = BlkBlock(
            name=name,
            parent=parent,
            depth=parent.depth + 1,
            open_brace=-1,
            close_brace=-1,
            synthetic=True,
        )
        parent.blocks.append(block)

        if parent.synthetic:
            parent.synth_lines.append(block)
            return block

        anchor = self._anchor_for(parent)
        block.open_brace = anchor
        prefix_newline = anchor > 0 and self.original_text[anchor - 1] not in "\r\n"
        self._serial += 1
        self._splices.append(
            _NewBlockSplice(
                anchor,
                "  " * parent.depth,
                block,
                self._serial,
                prefix_newline=prefix_newline,
            )
        )
        return block

    # -------------------------------------------------------------- serialize
    def rerender(self) -> str:
        """Original text with every pending splice applied."""
        if not self._splices:
            return self.original_text
        out = self.original_text
        ordered = sorted(
            self._splices, key=lambda s: (s.start, s.end, s.serial), reverse=True
        )
        for sp in ordered:
            out = out[: sp.start] + sp.render() + out[sp.end :]
        return out

    to_text = rerender

    def save(self, path=None, *, backup: bool = True) -> str | None:
        """Write the document atomically.  Returns the backup path, if any."""
        target = os.fspath(path) if path is not None else self.path
        if not target:
            raise BlkError("no target path to save to")

        data = self.rerender().encode(self.encoding)

        backup_path = None
        if backup and os.path.exists(target):
            backup_path = f"{target}.bak"
            try:
                with open(target, "rb") as src, open(backup_path, "wb") as dst:
                    dst.write(src.read())
            except OSError:
                backup_path = None

        tmp = f"{target}.tk-tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)

        self.original_text = self.rerender()
        self._splices.clear()
        self.path = target
        self.root = BlkBlock(
            name=None, depth=0, open_brace=-1, close_brace=len(self.original_text)
        )
        self._parse()
        return backup_path

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<BlkDocument params={sum(1 for _ in self.walk_params())} "
            f"blocks={sum(1 for _ in self.walk_blocks())} dirty={self.dirty}>"
        )
