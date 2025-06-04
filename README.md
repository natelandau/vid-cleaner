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
-   Search for video files under a directory that match specific criteria

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

Defaults for vid-cleaner are set in the configuration file located at `~/.config/vid-cleaner/config.toml`. When vid-cleaner is run, it will create this file if it does not exist. All options can be overridden on the command line.

If you've updated your user config file, the flags for the cli will work in reverse order. For example, if you've set `downmix_stereo = true` in your user config file, the flag `--downmix` will actually disable downmixing.

**Important:** Vid-cleaner makes decisions about which audio and subtitle tracks to keep based on the original language of the video. This is determined by querying the TMDb, Radarr, andSonarr APIs. To use this functionality, you must add the appropriate API keys to the configuration file.

```toml
# Languages to keep (list of ISO 639-1 codes)
langs_to_keep = ["en"]

# Keep subtitles matching the local language(s) even when the audio is not in the local language(s)
keep_local_subtitles = false

# Keep commentary audio
keep_commentary = false

# Force dropping local subtitles even if audio is not default language
drop_local_subs = false

# Keep all subtitles
keep_all_subtitles = false

# Drop original language audio if not specified in langs_to_keep
drop_original_audio = false

# Always create a stereo track
downmix_stereo = false

# Save the video after each step (default is to save after all steps are completed)
save_each_step = false

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for more information.
