# Vid Cleaner

[![Changelog](https://img.shields.io/github/v/release/natelandau/vid-cleaner?include_prereleases&label=changelog)](https://github.com/natelandau/vid-cleaner/releases) [![PyPI version](https://badge.fury.io/py/vid-cleaner.svg)](https://badge.fury.io/py/vid-cleaner) ![PyPI - Python Version](https://img.shields.io/pypi/pyversions/vid-cleaner) [![Tests](https://github.com/natelandau/vid-cleaner/actions/workflows/automated-tests.yml/badge.svg)](https://github.com/natelandau/vid-cleaner/actions/workflows/automated-tests.yml) [![codecov](https://codecov.io/gh/natelandau/vid-cleaner/graph/badge.svg?token=NHBKL0B6CL)](https://codecov.io/gh/natelandau/vid-cleaner)

Tools to transcode, inspect and convert videos. This package provides convenience wrappers around [ffmpeg](https://ffmpeg.org/) and [ffprobe](https://ffmpeg.org/ffprobe.html) to make it easier to work with video files. The functionality is highly customized to my personal workflows and needs. I am sharing it in case it is useful to others.

## Features

-   Remove commentary tracks and subtitles
-   Remove unwanted audio and subtitle tracks
-   Convert to H.265 or VP9
-   Convert 4k to 1080p
-   Downmix from surround to create missing stereo streams with custom filters to improve quality
-   Removes unwanted audio and subtitle tracks, optionally keeping the original language audio track
-   Create clips from a video file

## Install

Before installing vid-cleaner, the following dependencies must be installed:

-   [ffmpeg](https://ffmpeg.org/)
-   [ffprobe](https://ffmpeg.org/ffprobe.html)
-   python 3.11+

To install vid-cleaner, run:

```bash
pip install vid-cleaner
```

Running `vidcleaner` for the first time will create a default configuration file in `~/.config/vid-cleaner/config.toml`. Edit this file to configure your default settings.

## Usage

Run `vidcleaner --help` to see the available commands and options.

### File Locations

Vid-cleaner uses the [XDG specification](https://specifications.freedesktop.org/basedir-spec/latest/) for determining the locations of configuration files, logs, and caches.

-   Configuration file: `~/.config/vid-cleaner/config.toml`
-   Logs: `~/.local/state/vid-cleaner/vid-cleaner.log`
-   Cache: `~/.cache/vid-cleaner`

## Contributing

## Setup: Once per project

1. Install Python 3.11 and [uv](https://docs.astral.sh/uv/)
2. Clone this repository. `git clone https://github.com/natelandau/vid-cleaner`
3. Install the virtual environment with `uv sync`.
4. Activate your virtual environment with `source .venv/bin/activate`
5. Install the pre-commit hooks with `pre-commit install --install-hooks`.

## Developing

-   This project follows the [Conventional Commits](https://www.conventionalcommits.org/) standard to automate [Semantic Versioning](https://semver.org/) and [Keep A Changelog](https://keepachangelog.com/) with [Commitizen](https://github.com/commitizen-tools/commitizen).
    -   When you're ready to commit changes run `cz c`
-   Run `poe` from within the development environment to print a list of [Poe the Poet](https://github.com/nat-n/poethepoet) tasks available to run on this project. Common commands:
    -   `poe lint` runs all linters
    -   `poe test` runs all tests with Pytest
-   Run `uv add {package}` from within the development environment to install a run time dependency and add it to `pyproject.toml` and `uv.lock`.
-   Run `uv remove {package}` from within the development environment to uninstall a run time dependency and remove it from `pyproject.toml` and `uv.lock`.
-   Run `uv lock --upgrade` from within the development environment to update all dependencies in `pyproject.toml`.
