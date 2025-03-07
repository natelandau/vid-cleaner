# Vid Cleaner

[![Changelog](https://img.shields.io/github/v/release/natelandau/vid-cleaner?include_prereleases&label=changelog)](https://github.com/natelandau/vid-cleaner/releases) [![PyPI version](https://badge.fury.io/py/vid-cleaner.svg)](https://badge.fury.io/py/vid-cleaner) ![PyPI - Python Version](https://img.shields.io/pypi/pyversions/vid-cleaner) [![Tests](https://github.com/natelandau/vid-cleaner/actions/workflows/automated-tests.yml/badge.svg)](https://github.com/natelandau/vid-cleaner/actions/workflows/automated-tests.yml) [![codecov](https://codecov.io/gh/natelandau/vid-cleaner/graph/badge.svg?token=NHBKL0B6CL)](https://codecov.io/gh/natelandau/vid-cleaner)

Tools to transcode, inspect and convert videos. This package provides convenience wrappers around [ffmpeg](https://ffmpeg.org/) and [ffprobe](https://ffmpeg.org/ffprobe.html) to make it easier to work with video files. The functionality is highly customized to my personal workflows and needs. I am sharing it in case it is useful to others.

## Features

-   Remove commentary tracks and subtitles
-   Remove unwanted audio and subtitle tracks
-   Integrate with TMDb and Radarr/Sonarr to determine languages of videos
-   Convert to H.265 or VP9
-   Convert 4k to 1080p
-   Downmix from surround to create missing stereo streams with custom filters to improve quality
-   Remove unwanted audio and subtitle tracks, optionally keeping the original language audio track
-   Create clips from a video file

## Install

Before installing vid-cleaner, the following dependencies must be installed:

-   [ffmpeg](https://ffmpeg.org/)
-   [ffprobe](https://ffmpeg.org/ffprobe.html)
-   python 3.11+

To install vid-cleaner, run:

```bash
# With uv
uv tool install vid-cleaner

# With pip
python -m pip install --user vid-cleaner
```

## Usage

Run `vidcleaner --help` to see the available commands and options.

### Configuration

Vid-cleaner does not require a configuration file to run. However, the integration with tmd and other tools to determine languages of videos does require a config file. Create a configuration file at `~/.config/vid-cleaner/config.toml` and add the following:

```toml
# Languages to keep (list of ISO 639-1 codes)
keep_languages = ["en"]

# External services used to determine the original language of a movie or TV show
radarr_api_key = ""
radarr_url     = ""
sonarr_api_key = ""
sonarr_url     = ""
tmdb_api_key   = ""
```

### File Locations

Vid-cleaner uses the [XDG specification](https://specifications.freedesktop.org/basedir-spec/latest/) for determining the locations of configuration files, logs, and caches.

-   Configuration file: `~/.config/vid-cleaner/config.toml`
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
-   Run `duty --list` from within the development environment to print a list of tasks available to run on this project. Common commands:
    -   `duty lint` runs all linters
    -   `duty test` runs all tests with Pytest
-   Run `uv add {package}` from within the development environment to install a run time dependency and add it to `pyproject.toml` and `uv.lock`.
-   Run `uv remove {package}` from within the development environment to uninstall a run time dependency and remove it from `pyproject.toml` and `uv.lock`.
-   Run `uv lock --upgrade` from within the development environment to update all dependencies in `pyproject.toml`.
