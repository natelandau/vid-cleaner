# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run duty --list            # all task runner targets
uv run duty lint              # ruff check + ruff format --check + mypy + typos + prek hooks
uv run duty test              # pytest with coverage (fails under 60%)
uv run duty test -k downmix   # extra args are passed through to pytest
uv run duty dev_setup         # generate sample .mkv files under .dev/ with ffmpeg
```

`addopts` includes `--exitfirst` and `--failed-first`, so a run stops at the first failure and reruns previous failures first. `testpaths` includes `src`, so a bare `uv run pytest` also runs `--doctest-modules` over the package; `duty test` passes `tests` explicitly and skips those doctests.

Commit with `cz c` (commitizen). The prek `pytest` hook runs `duty test` on every commit, against that commit's staged state with unstaged changes stashed, so each commit must be green on its own.

ffmpeg and ffprobe must be on PATH for anything except the `cache` command.

Two unrelated dev directories: `.dev/` holds the sample media `duty dev_setup` generates, `.development/dev-config.toml` is the local settings override layer.

## Settings

`config.py` exports a process-wide dynaconf singleton as `vid_cleaner.settings`, and business logic reads `settings.*` directly instead of taking arguments. Anything a run mutates therefore leaks between tests; `tests/conftest.py` has an autouse fixture for the two worst offenders (`verbosity` and `out_path`), and a new leaky setting belongs there too.

Several flag defaults are `settings.*` values, and cappa infers `store_false` for a bool whose default is truthy. A user config with `keep_commentary = true` thus turns `--keep-commentary` into a switch that turns it off. This is documented behavior, not a bug.

## Command layer

`src/vid_cleaner/vidcleaner.py` is the whole CLI surface: one cappa dataclass per subcommand, each naming its implementation with `invoke="vid_cleaner.cli.<module>.main"`.

Destructured discovery options must always be attached under the attribute name `discovery`, because `config_subcommand` reads trait filters off every command through that one name.

`--from` is what puts a command in discovery mode. Explicit paths and discovery are mutually exclusive and never merge.

`discover_video_files` presorts alphabetically before applying the requested sort, so ties are deterministic. Unreadable files are collected and reported, never fatal.

## Writing a conversion planner

Each `_plan_*` method on `VideoFile` contributes `OutputStream` and `PlanAction` entries to one `ConversionPlan`, and `build_command()` emits a single ffmpeg command so the file is decoded and written exactly once. When adding or changing a planner:

- Never compute output stream indexes. Write `{n}` into `extra_args` (`"-b:a:{n}"`); `build_command()` owns the substitution.
- An ffmpeg option with no stream index applies to every stream of that type, so a planner that changes a codec or filter must iterate all kept streams of that type, not just the first.
- Mapping order is output order, so stream reordering is free.
- A planner that overrides an earlier decision, such as VP9 dropping image subtitles, must correct the earlier `PlanAction`. Those records are the user-facing report, not bookkeeping.

`VideoFile.probe_box` re-probes whenever the cached box's `path_to_file` no longer matches `latest_temp_path()`, so every ffmpeg step invalidates it implicitly.

## Conventions

- `VideoCleanError` and `VideoProbeError` mean "skip this file and keep going"; `cappa.Exit` means "stop the run", and the batch loops must not catch it. A batch with any failure exits 1 after processing every remaining file.
- Models return message strings; the CLI layer renders them.
- Print through nclutils `pp` rather than `Console.print`: `pp.success` and `pp.error` default to `markup=False`, which is what keeps bracketed filenames such as `Movie [x265].mkv` intact.
- The `# isort: skip` comments and function-local imports around `utils/`, `models/video_file.py`, and `controllers/` are load-bearing. Tidying them makes subpackages unimportable on their own.

## Testing

Tests drive the real CLI rather than calling `main()`:

```python
with pytest.raises(cappa.Exit) as exc_info:
    cappa.invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])
```

Fixtures live in `tests/conftest.py`. A `get_probe_as_box` mock is called more than once per file because of the re-probe rule above, so call-count assertions on it are fragile.
