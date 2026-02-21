# Monkey-patch for Pyrogram to support Bot API 9.4+ colored inline buttons.
#
# TL layer 224 adds an optional ``style`` field (flags.10) to every
# KeyboardButton* constructor, backed by a new ``KeyboardButtonStyle``
# TL type.  Because Pyrogram negotiates an older layer, we selectively
# emit the *new* constructor IDs only when a style is actually set;
# buttons without style keep the original (layer 195) IDs so nothing
# breaks for existing code.
#
# Usage – after importing this module every ``InlineKeyboardButton``
# accepts an optional ``style`` parameter:
#
#     InlineKeyboardButton("Click me", callback_data="x", style="green")
#
# Valid style names: blue/primary/accent, red/danger/destructive,
#                    green/success/positive, default/none.
# An optional ``icon`` (custom-emoji document id) is also supported.

from __future__ import annotations

from io import BytesIO
from typing import Optional

from pyrogram.raw.core.primitives import Int, Long, String, Bytes, Vector
from pyrogram.raw.core import TLObject
from pyrogram import raw, types
import pyrogram

# ── KeyboardButtonStyle TL type ────────────────────────────────────
# keyboardButtonStyle#4fdd3430 flags:#
#   bg_primary:flags.0?true  bg_danger:flags.1?true
#   bg_success:flags.2?true  icon:flags.3?long
#   = KeyboardButtonStyle;

_STYLE_ID = 0x4fdd3430

_STYLE_MAP = {
    "blue": "bg_primary", "primary": "bg_primary", "accent": "bg_primary",
    "red": "bg_danger", "danger": "bg_danger", "destructive": "bg_danger",
    "green": "bg_success", "success": "bg_success", "positive": "bg_success",
}


def _write_style_obj(bg_primary=False, bg_danger=False, bg_success=False,
                     icon=None) -> bytes:
    """Serialize a KeyboardButtonStyle TL object."""
    b = BytesIO()
    b.write(Int(_STYLE_ID, False))
    flags = 0
    flags |= (1 << 0) if bg_primary else 0
    flags |= (1 << 1) if bg_danger else 0
    flags |= (1 << 2) if bg_success else 0
    flags |= (1 << 3) if icon is not None else 0
    b.write(Int(flags))
    if icon is not None:
        b.write(Long(icon))
    return b.getvalue()


def _make_style_bytes(style_name: Optional[str],
                      icon: Optional[int] = None) -> Optional[bytes]:
    """Convert a human-friendly style name to serialized TL bytes."""
    if not style_name:
        return None
    key = _STYLE_MAP.get(style_name.strip().lower())
    if key is None:
        return None
    return _write_style_obj(**{key: True}, icon=icon)


# ── New constructor IDs (layer 224) ────────────────────────────────
# In layer 224 every KeyboardButton* gained a ``flags`` int and an
# optional ``style:flags.10?KeyboardButtonStyle``.  The constructor
# IDs therefore changed.
_NEW = {
    "callback":       0xe62bc960,   # keyboardButtonCallback
    "url":            0xd80c25ec,   # keyboardButtonUrl
    "user_profile":   0x7d5e07c7,   # inputKeyboardButtonUserProfile
    "switch_inline":  0x991399fc,   # keyboardButtonSwitchInline
    "game":           0x89c590f9,   # keyboardButtonGame
    "web_view":       0xe846b1a0,   # keyboardButtonWebView
    "buy":            0x3fa53905,   # keyboardButtonBuy
    "copy":           0xbcc4af10,   # keyboardButtonCopy
}


# ── Low-level writer ────────────────────────────────────────────
# TL serialisation follows field-declaration order; ``?true`` fields
# are only bits in the flags int and produce no output bytes.  The
# ``style`` object sits between ``flags`` and the first real field
# (``text``).
#
# General pattern (layer 224):
#   constructor_id | flags(int) | [style(obj)] | text(str) | …
#
# ``requires_password`` and ``same_peer`` re-use flag-bit 0.

def _build_styled_button(cid: int, style_bytes: bytes,
                         extra_flag_bits: int,
                         *field_bytes_list: bytes) -> "_StyledRaw":
    """Return a lightweight TLObject whose write() emits the correct bytes.

    *extra_flag_bits* – additional flag bits beyond ``(1 << 10)``
    (which is always set for style).
    """
    flags = (1 << 10) | extra_flag_bits
    return _StyledRaw(cid, flags, style_bytes, field_bytes_list)


class _StyledRaw(TLObject):
    """Minimal TLObject that serialises a styled keyboard button."""

    __slots__ = ["_cid", "_flags", "_style", "_fields"]
    ID = 0
    QUALNAME = "types._StyledRaw"

    def __init__(self, cid, flags, style_bytes, fields):
        self._cid = cid
        self._flags = flags
        self._style = style_bytes
        self._fields = fields

    def write(self, *args) -> bytes:
        b = BytesIO()
        b.write(Int(self._cid, False))
        b.write(Int(self._flags))
        b.write(self._style)           # KeyboardButtonStyle object
        for chunk in self._fields:
            b.write(chunk)
        return b.getvalue()

    @staticmethod
    def read(b, *a):
        raise NotImplementedError


# ── Serialise helpers ────────────────────────────────────────────

def _ser_string(s: str) -> bytes:
    b = BytesIO(); String.write_to(b, s) if hasattr(String, "write_to") else b.write(String(s)); return b.getvalue()

def _ser_bytes(data: bytes) -> bytes:
    b = BytesIO(); b.write(Bytes(data)); return b.getvalue()


# ── Patch InlineKeyboardButton ─────────────────────────────────────

_OrigIKB = types.InlineKeyboardButton
_orig_init = _OrigIKB.__init__
_orig_write = _OrigIKB.write


def _patched_init(self, text, callback_data=None, url=None, web_app=None,
                  login_url=None, user_id=None, switch_inline_query=None,
                  switch_inline_query_current_chat=None, callback_game=None,
                  requires_password=None, pay=None, copy_text=None,
                  style=None, icon=None):
    _orig_init(
        self, text=text, callback_data=callback_data, url=url,
        web_app=web_app, login_url=login_url, user_id=user_id,
        switch_inline_query=switch_inline_query,
        switch_inline_query_current_chat=switch_inline_query_current_chat,
        callback_game=callback_game, requires_password=requires_password,
        pay=pay, copy_text=copy_text,
    )
    self.style = style
    self.icon = icon


async def _patched_write(self, client: "pyrogram.Client"):
    style_bytes = _make_style_bytes(getattr(self, "style", None),
                                    getattr(self, "icon", None))

    # No style → delegate to the original write (old constructor IDs).
    if style_bytes is None:
        return await _orig_write(self, client)

    # ── Serialize text once (shared by every branch) ──────────
    text_b = BytesIO()
    text_b.write(String(self.text))
    text_ser = text_b.getvalue()

    # ── callback_data ─────────────────────────────────────────
    if self.callback_data is not None:
        data = (self.callback_data.encode("utf-8")
                if isinstance(self.callback_data, str)
                else self.callback_data)
        data_b = BytesIO(); data_b.write(Bytes(data))
        extra = (1 << 0) if self.requires_password else 0
        return _build_styled_button(
            _NEW["callback"], style_bytes, extra,
            text_ser, data_b.getvalue(),
        )

    # ── url ───────────────────────────────────────────────────
    if self.url is not None:
        url_b = BytesIO(); url_b.write(String(self.url))
        return _build_styled_button(
            _NEW["url"], style_bytes, 0,
            text_ser, url_b.getvalue(),
        )

    # ── user_id ───────────────────────────────────────────────
    if self.user_id is not None:
        resolved = await client.resolve_peer(self.user_id)
        return _build_styled_button(
            _NEW["user_profile"], style_bytes, 0,
            text_ser, resolved.write(),
        )

    # ── switch_inline_query ───────────────────────────────────
    if self.switch_inline_query is not None:
        q_b = BytesIO(); q_b.write(String(self.switch_inline_query))
        return _build_styled_button(
            _NEW["switch_inline"], style_bytes, 0,
            text_ser, q_b.getvalue(),
        )

    # ── switch_inline_query_current_chat ──────────────────────
    if self.switch_inline_query_current_chat is not None:
        q_b = BytesIO(); q_b.write(String(self.switch_inline_query_current_chat))
        return _build_styled_button(
            _NEW["switch_inline"], style_bytes, (1 << 0),  # same_peer
            text_ser, q_b.getvalue(),
        )

    # ── callback_game ─────────────────────────────────────────
    if self.callback_game is not None:
        return _build_styled_button(
            _NEW["game"], style_bytes, 0,
            text_ser,
        )

    # ── web_app ───────────────────────────────────────────────
    if self.web_app is not None:
        url_b = BytesIO(); url_b.write(String(self.web_app.url))
        return _build_styled_button(
            _NEW["web_view"], style_bytes, 0,
            text_ser, url_b.getvalue(),
        )

    # ── pay ───────────────────────────────────────────────────
    if self.pay is not None:
        return _build_styled_button(
            _NEW["buy"], style_bytes, 0,
            text_ser,
        )

    # ── copy_text ─────────────────────────────────────────────
    if self.copy_text is not None:
        ct_b = BytesIO(); ct_b.write(String(self.copy_text))
        return _build_styled_button(
            _NEW["copy"], style_bytes, 0,
            text_ser, ct_b.getvalue(),
        )

    # Fallback – no action field matched; drop style silently.
    return await _orig_write(self, client)


# ── Apply the patches ────────────────────────────────────────────

_OrigIKB.__init__ = _patched_init
_OrigIKB.write = _patched_write
