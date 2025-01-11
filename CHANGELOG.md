## v0.3.6 (2025-01-11)

### Fix

- parse 7 channel audio (#42)

## v0.3.5 (2024-11-21)

### Fix

- fix crash on data streams (#37)

## v0.3.4 (2024-11-15)

### Fix

- **config**: use XDG specification for config, state, and cache dirs (#35)
- improve user messaging (#31)

## v0.3.3 (2024-07-09)

### Fix

- display video process steps (#30)
- don't crash with `attachment` streams (#29)

## v0.3.2 (2024-04-28)

### Fix

- add support for `.m4v` files (#25)
- improve stream handling (#24)

## v0.3.1 (2024-03-11)

### Fix

- find correct tmdb movie information (#23)

### Refactor

- move version to constants (#22)
- **config**: migrate to confz package (#21)

## v0.3.0 (2023-12-23)

### Feat

- `--dry-run` shows ffmpeg commands without executing
- option to convert to 1080p
- **inspect**: add `--json` to output full stream data in json format

## v0.2.1 (2023-12-22)

### Fix

- improve cli options
- remove data streams from vp9 (#7)

## v0.2.0 (2023-12-08)

### Feat

- **clean**: don't reorder streams if already in the right order (#6)
- **inspect**: add stream title (#5)

## v0.1.1 (2023-12-01)

## v0.1.0 (2023-12-01)

### Feat

- `--force` flag to re-encode vp9 or h265
- add cli options to config file
- progress bar when copying files
- only keep one tmp file
- cleanup tmp files on early exit
- initial commit (#1)

### Fix

- rename cli to `vidcleaner`
- use uuid for tmp_dir
