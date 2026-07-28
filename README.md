# Vid Cleaner

[![Changelog](https://img.shields.io/github/v/release/natelandau/vid-cleaner?include_prereleases&label=changelog)](https://github.com/natelandau/vid-cleaner/releases) [![PyPI version](https://badge.fury.io/py/vid-cleaner.svg)](https://badge.fury.io/py/vid-cleaner) ![PyPI - Python Version](https://img.shields.io/pypi/pyversions/vid-cleaner) [![Tests](https://github.com/natelandau/vid-cleaner/actions/workflows/automated-tests.yml/badge.svg)](https://github.com/natelandau/vid-cleaner/actions/workflows/automated-tests.yml) [![codecov](https://codecov.io/gh/natelandau/vid-cleaner/graph/badge.svg?token=NHBKL0B6CL)](https://codecov.io/gh/natelandau/vid-cleaner)

Tools to transcode, inspect and convert videos. This package provides convenience wrappers around [ffmpeg](https://ffmpeg.org/) and [ffprobe](https://ffmpeg.org/ffprobe.html) to make it easier to work with video files. The functionality is highly customized to my personal workflows and needs. I am sharing it in case it is useful to others.

## Features

-   Remove commentary tracks and subtitles
-   Remove unwanted audio and subtitle tracks
-   Determine a video's original language from its filename, its container metadata, or the TMDb, Radarr, and Sonarr APIs
-   Convert to H.265 or VP9
-   Convert 4k to 1080p
-   Downmix any surround layout (5.1, 7.1, and Atmos above 7.1) into a stereo track when one is missing, or recreate an existing stereo track with `--force`, using a dialogue-forward filter that keeps speech clear
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

### Searching for video files

`vidcleaner search DIRECTORY` finds video files under a directory and prints a table of matches. Narrow the search with `--filters`, control how deep it recurses with `--depth`, and change the order with `--sort` and `--reverse`. Use `--limit` to keep only the top results on the active sort key:

```shell
# The five largest 4k files, two directories deep
vidcleaner search /media --filters=4k --sort=size --depth=2 --limit=5
```

Listing several filters narrows the search rather than widening it: a file must have every trait you name to be returned.

```shell
# Only 4k files that also have a surround track and no stereo track
vidcleaner search /media --filters=4k,surround5,no_stereo
```

### Cleaning what you searched for

`clean` accepts the same query flags as `search`, so a `search` command is a preview of the `clean` command that acts on it. Swap the positional directory for `--from`:

```shell
# Preview
vidcleaner search /media --filters=4k --sort=size --limit=5

# Act on exactly that selection
vidcleaner clean --from /media --filters=4k --sort=size --limit=5 --h265
```

`clean` renders the same table `search` does, then asks for confirmation before transcoding anything. The prompt states whether each original will be backed up or overwritten in place. Pass `--yes` to skip the prompt in scripts and cron jobs, or `--dryrun` to preview without being asked. Running without a terminal and without `--yes` is an error rather than a hang.

`--from` cannot be combined with explicit file paths or with `--out`, and must name an existing directory. `--yes` and the query flags (`--filters`, `--sort`, `--reverse`, `--depth`, `--limit`) are only meaningful with `--from` and are refused without it. `--limit` must be 1 or greater.

### Where the output goes

`clean` and `clip` replace the file they were given. The original is not deleted: it is renamed alongside the result as a timestamped `.bak` copy. Pass `--overwrite` to skip the backup and rewrite the file in place with no way back, or `--out PATH` (single input file only) to write somewhere else and leave the input untouched.

`--vp9` is the exception, because VP9 has to go in a WebM container. `vidcleaner clean --vp9 movie.mkv` writes `movie.webm` next to the input. Without `--overwrite` the original `movie.mkv` is left where it is; with `--overwrite` it is removed, so the container change does not leave two copies of the same film on disk.

When cleaning several files, a failure on one file does not stop the run. Every remaining file is still processed, failures are listed at the end, and the command exits with a non-zero status.

### Configuration

Defaults for vid-cleaner are set in the configuration file located at `~/.config/vid-cleaner/config.toml`. When vid-cleaner is run, it will create this file if it does not exist. All options can be overridden on the command line.

If you've updated your user config file, the flags for the cli will work in reverse order. For example, if you've set `downmix_stereo = true` in your user config file, the flag `--downmix` will actually disable downmixing.

**Important:** Vid-cleaner makes decisions about which audio and subtitle tracks to keep based on the original language of the video. To find that language, it looks for an IMDb or TMDB id in three places, stopping at the first one that resolves:

1. The filename, matching either a bare `tt0245712` or the `{tmdb-55}` naming convention.
2. The container's own `IMDB` and `TMDB` metadata tags, as written by tools like mkvmerge.
3. A Radarr or Sonarr title search.

The first two need only `tmdb_api_key` in the configuration file. Set the `radarr_` and `sonarr_` keys if you want the title search as a fallback.

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
