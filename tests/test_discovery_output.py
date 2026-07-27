# type: ignore
"""Test the shared discovery presenter."""

from pathlib import Path

import cappa
import pytest

from vid_cleaner import settings
from vid_cleaner.cli.discovery_output import confirm_selection, present_discovery
from vid_cleaner.constants import SortOrder, VideoTrait
from vid_cleaner.controllers.discovery import DiscoveryReport
from vid_cleaner.exceptions import VideoProbeError


def make_report(**overrides) -> DiscoveryReport:
    """Build a DiscoveryReport with test-friendly defaults.

    Returns:
        DiscoveryReport: A report with every field defaulted unless overridden.
    """
    defaults = {
        "results": [],
        "total": 0,
        "skipped": [],
        "truncated": 0,
        "sort": SortOrder.ALPHA,
        "descending": False,
        "filters": set(),
        "depth": 0,
    }
    return DiscoveryReport(**{**defaults, **overrides})


def test_present_exits_zero_when_nothing_found(capsys, tmp_path):
    """Verify an empty directory warns and exits 0, since finding nothing is not an error."""
    # Given: A report from a directory holding no video files
    report = make_report(total=0)

    # When: Presenting it
    with pytest.raises(cappa.Exit) as exc_info:
        present_discovery(report, root=tmp_path)

    # Then: The command warns and exits successfully
    assert exc_info.value.code == 0
    assert "No video files found" in capsys.readouterr().err


def test_present_exits_one_when_nothing_matched(capsys, tmp_path):
    """Verify files found but none matching the filters is an error naming the filters."""
    # Given: A report where candidates existed but none matched
    report = make_report(total=3, filters={VideoTrait.REORDER})

    # When: Presenting it with an active filter
    with pytest.raises(cappa.Exit) as exc_info:
        present_discovery(report, root=tmp_path)

    # Then: The command errors and names the unmatched filter
    assert exc_info.value.code == 1
    assert "No video files found matching 'reorder'" in capsys.readouterr().err


def test_present_empty_match_reports_skipped_count(capsys, tmp_path):
    """Verify the no-match error folds in the skipped count, since no caption will render."""
    # Given: A report where every candidate was unreadable
    report = make_report(
        total=1,
        filters={VideoTrait.REORDER},
        skipped=[VideoProbeError(path=tmp_path / "bad.mkv", reason="Invalid data")],
    )

    # When: Presenting it
    with pytest.raises(cappa.Exit) as exc_info:
        present_discovery(report, root=tmp_path)

    # Then: The error reports the skipped file alongside the mismatch
    error = capsys.readouterr().err
    assert exc_info.value.code == 1
    assert "1 file(s) skipped as unreadable" in error


def test_present_unfiltered_empty_match_says_unreadable(capsys, tmp_path):
    """Verify an unfiltered run that matched nothing blames readability, not the filters."""
    # Given: A report with candidates, no filters, and no results
    report = make_report(total=2)

    # When: Presenting it
    with pytest.raises(cappa.Exit) as exc_info:
        present_discovery(report, root=tmp_path)

    # Then: The error explains that nothing could be read
    assert exc_info.value.code == 1
    assert "No video files could be read" in capsys.readouterr().err


def make_result(mocker, name: str, size: int):
    """Build a stand-in SearchResult carrying only what the prompt reads.

    Returns:
        MagicMock: An object exposing `.size` and `.video_file.path`.
    """
    result = mocker.MagicMock()
    result.size = size
    result.video_file.path = Path(f"/media/{name}")
    return result


def test_confirm_returns_when_assume_yes(mocker, capsys):
    """Verify --yes proceeds without prompting, so unattended runs never block."""
    # Given: A selection and a stubbed prompt that would fail the test if called
    report = make_report(results=[make_result(mocker, "a.mkv", 100)], total=1)
    ask = mocker.patch("vid_cleaner.cli.discovery_output.Confirm.ask")

    # When: Confirming with assume_yes
    confirm_selection(report, assume_yes=True)

    # Then: No prompt was shown
    ask.assert_not_called()


def test_confirm_errors_on_non_tty_without_yes(mocker, capsys, interactive_console):
    """Verify a non-interactive terminal without --yes errors instead of hanging on stdin."""
    # Given: A non-interactive console
    report = make_report(results=[make_result(mocker, "a.mkv", 100)], total=1)
    interactive_console(is_terminal=False)

    # When: Confirming without assume_yes
    with pytest.raises(cappa.Exit) as exc_info:
        confirm_selection(report, assume_yes=False)

    # Then: The command errors and names the flag that would have allowed it
    assert exc_info.value.code == 1
    assert "--yes" in capsys.readouterr().err


def test_confirm_exits_zero_when_declined(mocker, interactive_console):
    """Verify declining is a successful no-op, not a failure."""
    # Given: An interactive console where the user answers no
    report = make_report(results=[make_result(mocker, "a.mkv", 100)], total=1)
    interactive_console(is_terminal=True)
    mocker.patch("vid_cleaner.cli.discovery_output.Confirm.ask", return_value=False)

    # When: Confirming
    with pytest.raises(cappa.Exit) as exc_info:
        confirm_selection(report, assume_yes=False)

    # Then: The command exits successfully without acting
    assert exc_info.value.code == 0


def test_confirm_returns_when_accepted(mocker, interactive_console):
    """Verify accepting prompts once and lets the caller proceed."""
    # Given: An interactive console where the user answers yes
    report = make_report(results=[make_result(mocker, "a.mkv", 100)], total=1)
    interactive_console(is_terminal=True)
    ask = mocker.patch("vid_cleaner.cli.discovery_output.Confirm.ask", return_value=True)

    # When: Confirming
    confirm_selection(report, assume_yes=False)

    # Then: The user was asked exactly once and the call returned rather than exiting
    ask.assert_called_once()


def test_confirm_prompt_reports_count_and_total_size(mocker, interactive_console):
    """Verify the prompt states how many files and how many bytes are at stake."""
    # Given: Two selected files totaling 300 bytes
    report = make_report(
        results=[make_result(mocker, "a.mkv", 100), make_result(mocker, "b.mkv", 200)],
        total=2,
    )
    interactive_console(is_terminal=True)
    ask = mocker.patch("vid_cleaner.cli.discovery_output.Confirm.ask", return_value=True)

    # When: Confirming
    confirm_selection(report, assume_yes=False)

    # Then: The prompt names the count and the combined size
    prompt = ask.call_args.args[0]
    assert "2 files" in prompt
    assert "300" in prompt


@pytest.fixture
def overwrite_setting():
    """Set `settings.overwrite` for one test and restore it afterwards.

    Returns:
        Callable[..., None]: Call with `overwrite=True` or `overwrite=False`.
    """
    original = settings.overwrite

    def _inner(*, overwrite: bool) -> None:
        settings.update({"overwrite": overwrite})

    yield _inner
    settings.update({"overwrite": original})


def test_confirm_prompt_promises_a_backup_by_default(
    mocker, interactive_console, overwrite_setting
):
    """Verify the default prompt says each original is backed up before it is rewritten."""
    # Given: A selection cleaned without --overwrite
    report = make_report(results=[make_result(mocker, "a.mkv", 100)], total=1)
    overwrite_setting(overwrite=False)
    interactive_console(is_terminal=True)
    ask = mocker.patch("vid_cleaner.cli.discovery_output.Confirm.ask", return_value=True)

    # When: Confirming
    confirm_selection(report, assume_yes=False)

    # Then: The prompt promises a backup
    prompt = ask.call_args.args[0]
    assert "backup" in prompt
    assert "no backup" not in prompt


def test_confirm_prompt_warns_when_overwriting_without_backup(
    mocker, interactive_console, overwrite_setting
):
    """Verify --overwrite is disclosed, since it leaves nothing to restore the library from."""
    # Given: A selection cleaned with --overwrite
    report = make_report(results=[make_result(mocker, "a.mkv", 100)], total=1)
    overwrite_setting(overwrite=True)
    interactive_console(is_terminal=True)
    ask = mocker.patch("vid_cleaner.cli.discovery_output.Confirm.ask", return_value=True)

    # When: Confirming
    confirm_selection(report, assume_yes=False)

    # Then: The prompt says the originals are rewritten with no backup
    prompt = ask.call_args.args[0]
    assert "in place with no backup" in prompt
