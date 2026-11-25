# Vid Cleaner

[![Changelog](https://img.shields.io/github/v/release/natelandau/vid-cleaner?include_prereleases&label=changelog)](https://github.com/natelandau/vid-cleaner/releases) [![PyPI version](https://badge.fury.io/py/vid-cleaner.svg)](https://badge.fury.io/py/vid-cleaner) ![PyPI - Python Version](https://img.shields.io/pypi/pyversions/vid-cleaner) [![Tests](https://github.com/natelandau/vid-cleaner/actions/workflows/automated-tests.yml/badge.svg)](https://github.com/natelandau/vid-cleaner/actions/workflows/automated-tests.yml) [![codecov](https://codecov.io/gh/natelandau/vid-cleaner/graph/badge.svg?token=NHBKL0B6CL)](https://codecov.io/gh/natelandau/vid-cleaner)

Tools to transcode, inspect, and convert videos. vid-cleaner is a command-line wrapper around [ffmpeg](https://ffmpeg.org/) and [ffprobe](https://ffmpeg.org/ffprobe.html) that removes unwanted tracks, converts codecs, and finds or checks video files in a library. It is built for my own workflows, and I share it in case it is useful to others.

## Features

-   Remove unwanted audio and subtitle tracks, optionally keeping the original language audio track
-   Remove commentary, SDH, and description tracks
-   Determine a video's original language from its filename, its container metadata, or the TMDb, Radarr, and Sonarr APIs
-   Convert to H.265 or VP9
-   Convert 4k to 1080p
-   Add a stereo track to a surround file (5.1, 7.1, or Atmos), with a mix that keeps dialogue clear
-   Inspect the streams in a video file
-   Create clips from a video file
-   Search for video files under a directory that match specific criteria
-   Check that video files are valid, and exit non-zero when any file is not

## Install

vid-cleaner needs these programs:

-   Python 3.11 or later
-   [ffmpeg](https://ffmpeg.org/) and [ffprobe](https://ffmpeg.org/ffprobe.html), on your `PATH`

Every command except `cache` needs ffmpeg and ffprobe. If either program is not on `PATH`, the command stops before it reads a file.

To install vid-cleaner, run one of these commands:

```bash
# With uv
uv tool install vid-cleaner

# With pip
python -m pip install --user vid-cleaner
```

## Usage

Run `vidcleaner --help` to see the commands, and `vidcleaner COMMAND --help` to see the options of one command. Every command accepts `--dryrun` (`-n`), which shows what the command will do without a change to a file.

Every command that reads video files skips the `._` metadata files that macOS writes beside each file it copies to a network share or external disk. This includes `._` files that a shell pattern such as `*.mkv` adds to the command line.

### Inspecting a video file

`vidcleaner inspect FILES` prints a table of the video, audio, and subtitle streams in each file, with the codec, language, and channel layout of each stream. Use `--json` to print the raw ffprobe output instead.

```shell
vidcleaner inspect movie.mkv
vidcleaner inspect --json movie.mkv
```

### Cleaning a video file

`vidcleaner clean FILES` removes the audio and subtitle tracks you do not want, and can convert the video in the same pass. With no options, it keeps the audio tracks in the languages that `langs_to_keep` names in the [configuration file](#configuration), plus the original language of the video.

`clean` keeps attachments, such as the fonts that ASS subtitles use. WebM cannot hold attachments, so `--vp9` removes them and shows a notice for each one.

```shell
# Keep only English audio and convert to H.265
vidcleaner clean --langs=en --h265 movie.mkv

# Add a stereo track and keep every subtitle track
vidcleaner clean --downmix --keep-subs movie.mkv

# Convert a 4k file to 1080p
vidcleaner clean --1080p movie.mkv
```

These are the options you use most:

| Option                     | Effect                                                                     |
| -------------------------- | -------------------------------------------------------------------------- |
| `-l`, `--langs`            | Languages to keep, as comma-separated ISO 639-1 codes (`en,es`)            |
| `-H`, `--h265`             | Convert the video to H.265                                                 |
| `-V`, `--vp9`              | Convert the video to VP9, in a WebM container                              |
| `-p`, `--1080p`            | Convert a 4k video to 1080p. With `--force`, a smaller video is upscaled only after you approve it. |
| `-d`, `--downmix`          | Add a stereo track if the file has none. With `--force`, replace the stereo track in the language of the surround track. |
| `-s`, `--keep-subs`        | Keep every subtitle track                                                  |
| `-c`, `--keep-commentary`  | Keep commentary audio tracks. Without this flag, all commentary tracks are removed, also tracks with no language tag. |
| `-f`, `--force`            | Process the file even if it is already in the requested format             |

Each on-or-off option in the `Configuration` group of `vidcleaner clean --help` also has a `--no-` form, such as `--no-downmix` or `--no-keep-commentary`. Use the `--no-` form to turn off for one run an option that your configuration file turns on.

Run `vidcleaner clean --help` for the full list. To act on every file that matches a query, see [Cleaning what you searched for](#cleaning-what-you-searched-for).

### Making a clip

`vidcleaner clip FILES` cuts a section out of a video without a new encode. Set the start time with `--start` and the length with `--duration`, both in `HH:MM:SS` format. The defaults are `00:00:00` and one minute.

```shell
# A 90-second clip that starts ten minutes in
vidcleaner clip --start=00:10:00 --duration=00:01:30 movie.mkv
```

### Searching for video files

`vidcleaner search DIRECTORY` finds video files under a directory and prints a table of matches. The directory defaults to the current directory. These options control the search:

-   `--filters` keeps only files with the traits you name. Run `vidcleaner search --help` for the list of traits.
-   `--depth` sets how many levels of subdirectories to search. The default, 0, searches only the directory itself.
-   `--sort` orders the results by `alpha`, `size`, or `bitrate`, and `--reverse` flips the order.
-   `--limit` keeps only the first N results after the sort.


```shell
# The five largest 4k files, two directories deep
vidcleaner search /media --filters=4k --sort=size --depth=2 --limit=5
```

If you name several filters, a file must have every trait to match. More filters give fewer results.

```shell
# Only 4k files that also have a 5.1 track and no stereo track to go with it
vidcleaner search /media --filters=4k,surround5,needs_stereo
```

### Cleaning what you searched for

`clean` accepts the same query options as `search`, so a `search` command is a preview of the `clean` command that acts on the same files. To change a `search` command into a `clean` command, replace the directory with `--from DIRECTORY`:

```shell
# Preview
vidcleaner search /media --filters=4k --sort=size --limit=5

# Act on exactly that selection
vidcleaner clean --from /media --filters=4k --sort=size --limit=5 --h265
```

`clean` shows the same table as `search`, then asks you to confirm before it changes a file. The prompt tells you if each original gets a backup or is replaced in place. In scripts and cron jobs, use `--yes` to skip the prompt. If there is no terminal and you do not give `--yes`, the command stops with an error.

These rules apply to `--from`:

-   `--from` must name a directory that exists.
-   You cannot use `--from` together with file paths or with `--out`.
-   `--yes` and the query options (`--filters`, `--sort`, `--reverse`, `--depth`, `--limit`) work only with `--from`.
-   `--limit` must be 1 or more.

### Where the output goes

`clean` and `clip` replace the file you give them. The original file gets a new name with a timestamp and a `.bak` extension, in the same directory. To write the result to a different location and keep the input unchanged, use `--out PATH`. `--out` works only with one input file.

> **CAUTION:** Do not use `--overwrite` unless you have a copy of the original. `--overwrite` replaces the original with no backup.

`overwrite = true` in the configuration file has the same effect as `--overwrite`. To keep a backup for one run, use `--no-overwrite`.

`--vp9` writes a new file, because VP9 must go in a WebM container. `vidcleaner clean --vp9 movie.mkv` writes `movie.webm` in the same directory and keeps `movie.mkv`. With `--overwrite`, the command deletes `movie.mkv`.

If one file fails when you clean or clip several files, the command continues with the other files. At the end, it lists the failures and exits with a non-zero status. `clean` and `clip` refuse a file that has no video stream.

### Checking the output

Before `clean` or `clip` replaces the original, the command checks the new file. The `--verify` option sets the level, for example `--verify=full`. The default level is `quick`. To change the default, set `verify` in the configuration file.

-   `off`: no check.
-   `quick`: reads the new file and makes sure that it has a video stream, the expected number of audio and subtitle streams, and the expected length.
-   `full`: does the `quick` check, then decodes the whole file. This check takes minutes for each file.

If the check fails, the command keeps the original, reports the file as failed, and continues with the next file. A dry run does not check anything, because it writes no file.

### Checking that files are valid

`vidcleaner check` reports if each video file is valid. If a file is not valid, the command exits `1`. Use it to find damaged and mislabeled files in a library.

```shell
# Two files named directly
vidcleaner check movie.mkv other.mp4

# Every video file in a library, three levels deep
vidcleaner check --from /media --depth=3
```

The command prints one line for every file, so one invalid file does not hide the state of the others.

```console
✓ movie.mkv
✗ broken.mkv  Invalid data found when processing input
✗ audiobook.mkv  no video stream
✗ notes.txt  not a video file

Checked 4 file(s): 1 valid, 3 invalid
```

A file is valid when all of these conditions are true:

-   The file exists.
-   The file has a video container extension.
-   ffprobe can read the file.
-   The file has a minimum of one video stream.

Cover art does not count as a video stream. An audio-only file with an embedded poster is invalid.

The command prints valid files to stdout and invalid files to stderr. To hide the invalid files, run `vidcleaner check FILES 2>/dev/null`.

By default, the command reads only the metadata of each file, which takes milliseconds. To decode every frame instead, use `--deep`. A deep check finds damage in the body of a file that has a correct header. It takes minutes for each file.

```shell
# Fully decode every 4k file to find damage that ffprobe cannot see
vidcleaner check --from /media --filters=4k --deep
```

`check` accepts the same query options as `search`, except `--limit`. A limit can leave files unchecked while the command exits `0`. A trait filter never hides an invalid file, because the filter applies only to files that ffprobe can read.

### Configuration

The configuration file `~/.config/vid-cleaner/config.toml` sets the defaults for `clean` and `clip`. If the file does not exist, vid-cleaner creates it with the defaults below the first time it runs. Only your user account can read the file, because it can hold API keys. If other users can read a file that holds API keys, vid-cleaner shows a warning with the `chmod` command that corrects it.

vid-cleaner gets each setting from the first of these sources that sets it:

1. A command-line flag, such as `--downmix` or `--no-downmix`.
2. An environment variable with the `VIDCLEANER_` prefix and the key in uppercase. For example, `VIDCLEANER_TMDB_API_KEY` sets `tmdb_api_key`.
3. The configuration file.

An environment variable for an on-or-off setting takes `true`, `false`, `1`, or `0`. An environment variable for `langs_to_keep` takes a comma-separated list, such as `VIDCLEANER_LANGS_TO_KEEP=en,fr`, or a TOML array, such as `VIDCLEANER_LANGS_TO_KEEP='["en", "fr"]'`. vid-cleaner ignores an empty environment variable.

If a setting has a value that is not valid, `clean`, `clip`, and `cache` stop and list each setting that is not valid, with its source. The other commands do not read the settings, so they continue to work. If the file has a key that vid-cleaner does not know, vid-cleaner shows a warning and ignores the key.

vid-cleaner uses the original language of a video to select the audio and subtitle tracks to keep. To find that language, it looks for an IMDb or TMDB ID in three places, and stops at the first one that it finds:

1. The filename, matching either a bare `tt0245712` or the `{tmdb-55}` naming convention.
2. The `IMDB` and `TMDB` metadata tags in the container, as tools such as mkvmerge write them.
3. A Radarr or Sonarr title search.

The first two places need only `tmdb_api_key`. To use the Radarr or Sonarr title search when the first two places have no ID, set the `radarr_` or `sonarr_` keys.

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

# Replace the original file without keeping a timestamped .bak copy
overwrite = false

# Check the new file before it replaces the original: "off", "quick", or "full"
verify = "quick"

# External services used to determine the original language of a movie or TV show
radarr_api_key = ""
radarr_url     = ""
sonarr_api_key = ""
sonarr_url     = ""
tmdb_api_key   = ""
```

### File locations

vid-cleaner keeps its files in the locations that the [XDG Base Directory specification](https://specifications.freedesktop.org/basedir-spec/latest/) defines:

-   Configuration file: `~/.config/vid-cleaner/config.toml`, or `$XDG_CONFIG_HOME/vid-cleaner/config.toml` if `XDG_CONFIG_HOME` is set
-   Cache: `~/.cache/vid-cleaner`, or `$XDG_CACHE_HOME/vid-cleaner` if `XDG_CACHE_HOME` is set. To use a different directory, set `cache_dir` in the configuration file or `VIDCLEANER_CACHE_DIR`.

To print the contents of the cache, run `vidcleaner cache`. To empty the cache, run `vidcleaner cache --clear`.

## Contributing

To set up a development environment and run the checks, read [CONTRIBUTING.md](CONTRIBUTING.md).
