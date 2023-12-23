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
