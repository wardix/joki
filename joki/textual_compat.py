"""Compat patch untuk Textual 8.2.8.

Memperbaiki parsing Alt+Enter yang rusak di Textual 8.2.8:
  1. `\\x1b\\r` (legacy Alt+Enter) dilaporkan sebagai `enter` (modifier alt
     hilang) sehingga men-trigger submit, bukan baris baru.
  2. `\\x1b[13;2u` (kitty protocol Alt+Enter) dilaporkan sebagai `shift+enter`
     karena bit modifier dihitung dengan cara salah.

Patch ini hanya dipasang jika implementasi asli masih punya bug tsb, jadi aman
jika Textual di-upgrade ke versi yang sudah memperbaikinya.
"""

from __future__ import annotations

from textual import constants, events
from textual._ansi_sequences import ANSI_SEQUENCES_KEYS, IGNORE_SEQUENCE
from textual._keyboard_protocol import FUNCTIONAL_KEYS, MODIFIER_FUNCTIONAL_KEYS
from textual._xterm_parser import (
    SPECIAL_KEY_TO_CHARACTER,
    XTermParser,
    _re_extended_key,
)
from textual.keys import KEY_NAME_REPLACEMENTS, Keys, _character_to_key


def _patched_sequence_to_key_events(self, sequence, alt=False):
    """Salinan `XTermParser._sequence_to_key_events` dengan fix: prefix `alt+`
    juga ditambahkan untuk nama key multi-karakter (enter, tab, backspace, dll)."""
    if (
        not constants.DISABLE_KITTY_KEY
        and (keys := self._parse_extended_key(sequence)) is not None
    ):
        for key in keys:
            yield key.copy()
        return

    keys = ANSI_SEQUENCES_KEYS.get(sequence)
    if keys is IGNORE_SEQUENCE:
        yield events.Key(Keys.Ignore, sequence)
        return
    if isinstance(keys, tuple):
        for key in keys:
            key_name = key.value
            if alt and "+" not in key_name:
                key_name = f"alt+{key_name}"
            yield events.Key(key_name, sequence if len(sequence) == 1 else None)
        return
    if isinstance(keys, str):
        sequence = keys

    if len(sequence) == 1:
        try:
            if not sequence.isalnum():
                name = _character_to_key(sequence)
            else:
                name = sequence

            name = KEY_NAME_REPLACEMENTS.get(name, name)
            if alt:
                if len(name) == 1 and name.isupper():
                    name = f"shift+{name.lower()}"
                name = f"alt+{name}"
            yield events.Key(name, sequence)
        except Exception:  # noqa: BLE001
            yield events.Key(sequence, sequence)


def _patched_parse_extended_key(self, sequence):
    """Salinan `XTermParser._parse_extended_key` dengan fix bit modifier kitty."""
    if (match := _re_extended_key.fullmatch(sequence)) is None:
        return None

    key_events = []
    codes, end = match.groups(default="")
    codepoint_str, modifiers_str, text_str, *_ = codes.split(";") + ["", "", ""]
    codepoint = int(codepoint_str or "1")
    modifiers = int(modifiers_str or "0")

    for text in self._parse_colon_codepoints(text_str):
        if not (key := FUNCTIONAL_KEYS.get(f"{codepoint}{end}", "")):
            key = _character_to_key(text if text else chr(codepoint))

        key_tokens = []
        if modifiers and key not in MODIFIER_FUNCTIONAL_KEYS and text_str is not None:
            modifier_bits = int(modifiers)
            MODIFIERS = ("alt", "ctrl", "super", "hyper", "meta")
            if modifier_bits & 1 and (text is None or text.isspace()):
                key_tokens.append("shift")
            for bit, modifier in enumerate(MODIFIERS, 1):
                if modifier == "alt" and text is not None:
                    continue
                if modifier_bits & (1 << bit):
                    key_tokens.append(modifier)

        key_tokens.sort()
        if key is not None:
            key_tokens.append(key)
        key_events.append(
            events.Key(
                "+".join(key_tokens),
                text or (None if modifiers else SPECIAL_KEY_TO_CHARACTER.get(key, None)),
            )
        )
    return key_events


def apply_textual_compat_patch() -> None:
    """Pasang patch ke XTermParser bila Textual 8.2.8 (bug masih ada)."""
    if XTermParser._sequence_to_key_events is _patched_sequence_to_key_events:
        return
    src = getattr(XTermParser._sequence_to_key_events, "__qualname__", "")
    if src != "XTermParser._sequence_to_key_events":
        return
    XTermParser._sequence_to_key_events = _patched_sequence_to_key_events  # type: ignore[method-assign,assignment]
    XTermParser._parse_extended_key = _patched_parse_extended_key  # type: ignore[method-assign,assignment]
