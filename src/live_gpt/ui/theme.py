"""Color tokens shared by every LiveAI window.

Stylesheets reference tokens as ``$name`` and are expanded with :func:`themed`.
Base hues are defined once; semantic tokens pick a hue and an opacity, so a
palette change only needs edits here.
"""
from __future__ import annotations

from string import Template


def _rgba(rgb: tuple[int, int, int], alpha: int) -> str:
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {alpha})"


def _rgb(rgb: tuple[int, int, int]) -> str:
    return f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"


# Base hues.
INK = (5, 10, 28)                 # Darkest field background.
NAVY = (12, 20, 48)               # Overlay panel.
STEEL = (130, 165, 230)           # Hairline borders.
SLATE = (70, 88, 140)             # Neutral buttons.
ACCENT = (76, 201, 240)           # Cyan accent: focus, hover, selection.
ACCENT_DEEP = (38, 112, 145)      # Secondary actions, selected items.
GLOW = (66, 220, 255)             # Overlay outline.
SUCCESS = (35, 155, 116)          # Send, toggled-on buttons.
SUCCESS_LIGHT = (130, 255, 195)
DANGER = (239, 68, 88)            # Close / exit hover.
WARNING = (210, 116, 34)          # Clear, microphone error.


PALETTE: dict[str, str] = {
    # Text.
    "text": "#f5f7ff",
    "text_card": "#e9edff",
    "text_group": "#dce7ff",
    "text_nav": "#cbd4ed",
    "text_glyph": "#cbd5e1",
    "text_quote": "#c1cbe0",
    "text_muted": "#aeb9d5",
    "text_status": "#9eacce",
    "text_subtle": "#8795b8",
    "text_log": "#7182aa",
    "text_faint": "#7180a4",
    "text_disabled": "#6f7893",
    "text_combo_disabled": "rgba(228, 235, 255, 155)",
    "link": "#66dcff",
    "accent_text": "#4cc9f0",
    "error_text": "#ff8999",
    "error_hint": "#ff667a",
    "error_status": "#ff8d9b",
    "danger_text": "#ffd8de",
    "danger_text_disabled": "#7f6670",
    # Surfaces.
    "overlay_panel": _rgba(NAVY, 224),
    "overlay_border": _rgba(GLOW, 150),
    "field": _rgba(INK, 145),
    "field_strong": _rgba(INK, 165),
    "scroll_track": _rgba(INK, 130),
    "popup": _rgb((18, 28, 58)),
    "popup_settings": "#182342",
    "settings_shell": "#0c1430",
    "settings_title": "#10182e",
    "settings_nav": _rgba((8, 14, 34), 120),
    "settings_card": "#121c3a",
    "dialog": "#0c1224",
    "code_card": "#17223b",
    "table_header": "#243352",
    "spin": "#18213b",
    "list_hover": "#202d4b",
    "list_selected": "#20445b",
    # Borders.
    "border": _rgba(STEEL, 75),
    "border_strong": _rgba(STEEL, 85),
    "border_card": _rgba(STEEL, 70),
    "divider": _rgba(STEEL, 45),
    "border_spin": "#536080",
    "focus": _rgba(ACCENT, 190),
    # Selection and accents.
    "selection": _rgba(ACCENT, 130),
    "selection_solid": _rgb(ACCENT_DEEP),
    "accent": _rgb(ACCENT),
    "scroll_handle": _rgba(ACCENT, 120),
    # Buttons.
    "button": _rgba(SLATE, 125),
    "button_border": _rgba((170, 195, 255), 90),
    "button_hover": _rgba(ACCENT, 150),
    "button_flat_hover": "#344768",
    "nav_hover": _rgba(SLATE, 80),
    "nav_checked": _rgba(ACCENT_DEEP, 115),
    "nav_checked_border": _rgba(ACCENT, 80),
    "secondary": _rgba(ACCENT_DEEP, 190),
    "secondary_hover": _rgba((48, 145, 185), 220),
    "action": _rgba(ACCENT_DEEP, 125),
    "action_border": _rgba(ACCENT, 95),
    "action_hover": _rgba((44, 140, 175), 170),
    "action_disabled": _rgba((40, 50, 80), 80),
    "action_disabled_border": _rgba((100, 115, 150), 45),
    "success": _rgba(SUCCESS, 190),
    "success_border": _rgba(SUCCESS_LIGHT, 190),
    "success_hover": _rgba((40, 190, 140), 220),
    "danger_hover": _rgba(DANGER, 190),
    "danger": _rgba((135, 45, 65), 135),
    "danger_border": _rgba((255, 120, 145), 105),
    "danger_bg_hover": _rgba((175, 50, 75), 180),
    "danger_disabled": _rgba((70, 38, 48), 80),
    "danger_disabled_border": _rgba((120, 75, 85), 45),
    "warning_hover": _rgba(WARNING, 190),
    "error_banner": _rgba((120, 30, 48), 110),
    "error_border": _rgba((255, 102, 122), 120),
    # Microphone states.
    "mic": _rgba((48, 72, 128), 175),
    "mic_recording": _rgba((220, 48, 72), 220),
    "mic_recording_border": _rgba((255, 150, 165), 220),
    "mic_saved": _rgba((34, 160, 105), 210),
    "mic_error": _rgba(WARNING, 215),
    "mic_error_border": _rgba((255, 205, 130), 200),
}


def themed(stylesheet: str) -> str:
    """Expand ``$token`` references in a Qt stylesheet or HTML snippet."""
    return Template(stylesheet).substitute(PALETTE)


SPINBOX_STYLE = themed(
    "QSpinBox { color: $text; background: $spin; padding: 8px; "
    "border: 1px solid $border_spin; border-radius: 6px; }"
)


__all__ = ["PALETTE", "SPINBOX_STYLE", "themed"]
