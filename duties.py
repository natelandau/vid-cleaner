"""Duty tasks for the project."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from duty import duty, tools
from nclutils import console

if TYPE_CHECKING:
    from duty.context import Context

PY_SRC_PATHS = (Path(_) for _ in ("src/", "tests/", "duties.py", "scripts/") if Path(_).exists())
PY_SRC_LIST = tuple(str(_) for _ in PY_SRC_PATHS)
CI = os.environ.get("CI", "0") in {"1", "true", "yes", ""}
DEV_DIR = Path(__file__).parent.absolute() / ".dev"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string.

    Args:
        text (str): String to remove ANSI escape sequences from.

    Returns:
        str: String without ANSI escape sequences.
    """
    ansi_chars = re.compile(r"(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]")

    # Replace [ with \[ so rich doesn't interpret output as style tags
    return ansi_chars.sub("", text).replace("[", r"\[")


def pyprefix(title: str) -> str:
    """Add a prefix to the title if CI is true.

    Returns:
        str: Title with prefix if CI is true.
    """
    if CI:
        prefix = f"(python{sys.version_info.major}.{sys.version_info.minor})"
        return f"{prefix:14}{title}"
    return title


@duty(silent=True)
def clean(ctx: Context) -> None:
    """Clean the project."""
    ctx.run("rm -rf .cache")
    ctx.run("rm -rf build")
    ctx.run("rm -rf dist")
    ctx.run("rm -rf pip-wheel-metadata")
    ctx.run("find . -type d -name __pycache__ | xargs rm -rf")
    ctx.run("find . -name '.DS_Store' -delete")


@duty
def ruff(ctx: Context) -> None:
    """Check the code quality with ruff."""
    ctx.run(
        tools.ruff.check(*PY_SRC_LIST, fix=False, config="pyproject.toml"),
        title=pyprefix("code quality check"),
        command="ruff check --config pyproject.toml --no-fix src/",
    )


@duty
def format(ctx: Context) -> None:  # noqa: A001
    """Format the code with ruff."""
    ctx.run(
        tools.ruff.format(*PY_SRC_LIST, check=True, config="pyproject.toml"),
        title=pyprefix("code formatting"),
        command="ruff format --check --config pyproject.toml src/",
    )


@duty
def mypy(ctx: Context) -> None:
    """Check the code with mypy."""
    os.environ["FORCE_COLOR"] = "1"
    ctx.run(
        tools.mypy("src/", config_file="pyproject.toml"),
        title=pyprefix("mypy check"),
        command="mypy --config-file pyproject.toml src/",
    )


@duty
def typos(ctx: Context) -> None:
    """Check the code with typos."""
    ctx.run(
        ["typos", "--config", ".typos.toml"],
        title=pyprefix("typos check"),
        command="typos --config .typos.toml",
    )


@duty(skip_if=CI, skip_reason="skip pre-commit in CI environments")
def precommit(ctx: Context) -> None:
    """Run pre-commit hooks."""
    ctx.run(
        "SKIP=mypy,pytest,ruff pre-commit run --all-files",
        title=pyprefix("pre-commit hooks"),
    )


@duty(pre=[ruff, mypy, typos, precommit], capture=CI)
def lint(ctx: Context) -> None:
    """Run all linting duties."""


@duty(capture=CI)
def update(ctx: Context) -> None:
    """Update the project."""
    ctx.run(["uv", "lock", "--upgrade"], title="update uv lock")
    ctx.run(["pre-commit", "autoupdate"], title="pre-commit autoupdate")


@duty()
def test(ctx: Context, *cli_args: str) -> None:
    """Test package and generate coverage reports."""
    ctx.run(
        tools.pytest(
            "tests",
            config_file="pyproject.toml",
            color="yes",
        ).add_args(
            "--cov",
            "--cov-config=pyproject.toml",
            "--cov-report=xml",
            "--cov-report=term",
            *cli_args,
        ),
        title=pyprefix("Running tests"),
        capture=CI,
    )


@duty()
def dev_clean(ctx: Context) -> None:
    """Clean the development environment."""
    # We import these here to avoid importing code before pytest-cov is initialized

    if DEV_DIR.exists():
        ctx.run(["rm", "-rf", str(DEV_DIR)])


@duty(pre=[dev_clean])
def dev_setup(ctx: Context) -> None:
    """Provision a mock development environment."""
    DEV_DIR.mkdir(parents=True, exist_ok=True)
    _create_subtitles()
    ctx.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=1920x1080:d=5:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5:sample_rate=48000",
            "-c:v",
            "libx264",
            "-vf",
            "format=yuv420p",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            f"{DEV_DIR}/1080p.mkv",
        ]
    )

    ctx.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=1920x1080:d=5:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5:sample_rate=48000",
            "-map",
            "0:v:0",  # Map video from first input
            "-map",
            "1:a:0",  # Map audio from second input (first track)
            "-map",
            "1:a:0",  # Map same audio from second input (second track)
            "-c:v",
            "libx264",
            "-vf",
            "format=yuv420p",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-metadata:s:a:0",
            "title=2.0",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:1",
            "title=commentary",
            "-metadata:s:a:1",
            "language=eng",
            "-shortest",
            f"{DEV_DIR}/1080p_commentary.mkv",
        ]
    )

    ctx.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=1920x1080:d=5:r=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000:d=5",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000:d=5",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000:d=5",
            "-i",
            f"{DEV_DIR}/subtitles/english.srt",
            "-i",
            f"{DEV_DIR}/subtitles/dutch.srt",
            "-i",
            f"{DEV_DIR}/subtitles/spanish.srt",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:0",
            "title=English Audio",
            "-map",
            "2:a:0",
            "-metadata:s:a:1",
            "language=dut",
            "-metadata:s:a:1",
            "title=Dutch Audio",
            "-map",
            "3:a:0",
            "-metadata:s:a:2",
            "language=spa",
            "-metadata:s:a:2",
            "title=Spanish Audio",
            "-map",
            "4:s:0",
            "-metadata:s:s:0",
            "language=eng",
            "-metadata:s:s:0",
            "title=English Subtitles",
            "-map",
            "5:s:0",
            "-metadata:s:s:1",
            "language=dut",
            "-metadata:s:s:1",
            "title=Dutch Subtitles",
            "-map",
            "6:s:0",
            "-metadata:s:s:2",
            "language=spa",
            "-metadata:s:s:2",
            "title=Spanish Subtitles",
            "-c:v",
            "libx264",
            "-vf",
            "format=yuv420p",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-c:s",
            "copy",
            "-shortest",
            f"{DEV_DIR}/multilingual_1080p.mkv",
        ]
    )


def _create_subtitles() -> None:
    """Create subtitles for the development environment."""

    def _create_srt_file(filename, line1_text, line2_text) -> None:
        """Creates an SRT file with two subtitle entries.

        Args:
            filename (str): The name of the SRT file to create (e.g., "english.srt").
            lang_code (str): A short code for the language (e.g., "en" for English).
                            This is just for internal reference in this script,
                            the actual language metadata is set in ffmpeg.
            line1_text (str): The text for the first subtitle entry.
            line2_text (str): The text for the second subtitle entry.
        """
        content = f"""1
00:00:01,000 --> 00:00:04,000
{line1_text}

2
00:00:04,500 --> 00:00:05,000
{line2_text}
    """
        subtitle_folder = DEV_DIR / "subtitles"
        subtitle_folder.mkdir(parents=True, exist_ok=True)
        filename = subtitle_folder / filename
        try:
            with filename.open("w", encoding="utf-8") as f:
                f.write(content)
            console.print(f"Successfully created '{filename}'")
        except OSError as e:
            console.print(f"Error creating file '{filename}': {e}")

    subtitles_data = [
        {
            "filename": "english.srt",
            "line1": "This is an English subtitle.",
            "line2": "End.",
        },
        {
            "filename": "dutch.srt",
            "line1": "Dit is een Nederlandse ondertitel.",
            "line2": "Einde.",
        },
        {
            "filename": "spanish.srt",
            "line1": "Este es un subtítulo en español.",
            "line2": "Fin.",
        },
    ]

    for sub_info in subtitles_data:
        _create_srt_file(sub_info["filename"], sub_info["line1"], sub_info["line2"])
