"""Tests for console output helpers."""

from vid_cleaner.constants import TREE_BRANCH, TREE_LAST
from vid_cleaner.models import PlanAction
from vid_cleaner.utils import render_operations, stop_run


def _plain(capsys) -> str:
    return capsys.readouterr().out


def test_normal_mode_shows_only_applied(capsys):
    """Verify normal mode renders only applied operations."""
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


def test_debug_mode_shows_tree_connectors(capsys):
    """Verify each rendered line is prefixed with a tree connector, last line using TREE_LAST."""
    actions = [
        PlanAction(label="Reorder streams", applied=True),
        PlanAction(
            label="Convert to H.265", applied=False, reason="already H.265/VP9; use --force"
        ),
    ]
    render_operations(actions, debug=True)
    lines = _plain(capsys).splitlines()
    assert TREE_BRANCH in lines[0]
    assert TREE_LAST in lines[-1]


def test_no_applied_actions_shows_no_changes(capsys):
    """Verify no applied actions in normal mode renders 'No changes needed' message."""
    actions = [
        PlanAction(label="Reorder streams", applied=False, reason="streams already in order")
    ]
    render_operations(actions, debug=False)
    assert "No changes needed" in _plain(capsys)


def test_open_tree_branches_every_line(capsys):
    """Verify an unclosed tree never emits TREE_LAST, so later lines extend the same tree."""
    actions = [
        PlanAction(label="Reorder streams", applied=True),
        PlanAction(label="Convert to H.265", applied=True),
    ]
    render_operations(actions, debug=True, closes_tree=False)
    out = _plain(capsys)
    assert TREE_BRANCH in out
    assert TREE_LAST not in out


def test_stop_run_prints_reason_and_returns_exception(capsys):
    """Verify stop_run shows the reason and returns an exit carrying it."""
    stopped = stop_run("Disk is full [x]", details=["Free 1 GB"])

    assert stopped.reason == "Disk is full [x]"
    assert stopped.code == 1
    error_output = capsys.readouterr().err
    assert "Disk is full [x]" in error_output
    assert "Free 1 GB" in error_output
