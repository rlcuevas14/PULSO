"""accent_fg: derived foreground for any user-picked accent color (trust-boundary check)."""
from app.templates_config import accent_fg


def test_accent_fg_dark_text_on_light_colors():
    assert accent_fg("#e8b94a") == "#0a0a0a"   # ochre
    assert accent_fg("#ffb084") == "#0a0a0a"   # peach
    assert accent_fg("#ffffff") == "#0a0a0a"


def test_accent_fg_light_text_on_dark_colors():
    assert accent_fg("#1a3a3a") == "#ffffff"   # teal
    assert accent_fg("#6366f1") == "#ffffff"   # indigo default
    assert accent_fg("#ff4d8b") == "#ffffff"   # brand pink


def test_accent_fg_tolerates_junk():
    assert accent_fg(None) == "#ffffff"        # default indigo → white
    assert accent_fg("") == "#ffffff"
    assert accent_fg("#fff") == "#0a0a0a"      # 3-digit form
    assert accent_fg("nonsense") == "#ffffff"  # unparseable → safe default
