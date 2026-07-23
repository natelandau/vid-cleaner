"""Tests for console output helpers."""

from nclutils import pp

from vid_cleaner.models.conversion_plan import PlanAction
from vid_cleaner.utils import render_operations


def _plain(capsys) -> str:
    return capsys.readouterr().out


def test_normal_mode_shows_only_applied(capsys):
    """Verify normal mode renders only applied operations."""
    pp.configure(verbosity=0)
    actions = [
        PlanAction(label="Reorder streams", applied=True),
        PlanAction(
            label="Convert to H.265", applied=False, reason="already H.265/VP9; use --force"
        ),
    ]
    render_operations(actions, debug=False)
    out = _plain(capsys)
    assert "✔ Reorder streams" in out
    assert "Convert to H.265" not in out


def test_debug_mode_shows_skipped_with_reason(capsys):
    """Verify debug mode renders all operations including skipped ones with reasons."""
    pp.configure(verbosity=1)
    actions = [
        PlanAction(label="Reorder streams", applied=True),
        PlanAction(
            label="Convert to H.265", applied=False, reason="already H.265/VP9; use --force"
        ),
    ]
    render_operations(actions, debug=True)
    out = _plain(capsys)
    assert "✔ Reorder streams" in out
    assert "✖ Convert to H.265" in out
    assert "already H.265/VP9; use --force" in out


def test_no_applied_actions_shows_no_changes(capsys):
    """Verify no applied actions in normal mode renders 'No changes needed' message."""
    pp.configure(verbosity=0)
    actions = [
        PlanAction(label="Reorder streams", applied=False, reason="streams already in order")
    ]
    render_operations(actions, debug=False)
    assert "No changes needed" in _plain(capsys)
