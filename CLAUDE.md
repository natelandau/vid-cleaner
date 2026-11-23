# CLAUDE.md

## Checks

```bash
uv run duty lint              # ruff, ty, typos, actionlint, zizmor, prek hooks
uv run duty test              # full suite with coverage
uv run duty test -k downmix   # extra args pass through to pytest
```

- Run checks only through these duties, never ruff, ty, typos, actionlint, zizmor, or pytest directly. Several duties run through gate-cache, and a direct run neither reads nor records its passes.
- `already passed on tree ..., skipping` means the check passed on this content. Do not rerun it another way. `GATE_CACHE_DISABLE=1` forces a real run.
- Run `uv run gate-cache --skill` before adding or changing a gate.
- Locally `duty test` stops at the first failure. Fix it and rerun to see the next one.
- Doctests in `src/` run with the suite.
- ffmpeg and ffprobe are needed to run `vidcleaner` by hand and for `duty dev_setup`, not for tests.
- `.dev/`: sample media from `duty dev_setup`. `.development/dev-config.toml`: local settings override. Unrelated.

## Commits

- Conventional Commits format. A commitizen hook rejects other messages.
- The commit hook runs `duty test` against the staged state with unstaged changes stashed, so each commit must pass on its own.

## Settings

- `vid_cleaner.settings` is a process-wide dynaconf singleton that business logic reads directly. A setting a run mutates leaks between tests. Add each leaky setting to the autouse `_reset_leaky_settings` fixture in `tests/conftest.py`.
- Several `clean` flag defaults are `settings.*` values, and cappa makes a bool flag with a truthy default `store_false`. With `keep_commentary = true` in config, `--keep-commentary` turns it off. Intended and documented, not a bug.

## Command layer

- Attach destructured discovery options under the attribute name `discovery`. `config_subcommand` reads trait filters from every command through that name.
- Import `command`, `invoke`, and `parse` from `cappa.base`, never as `cappa.invoke(...)`. ty resolves those names on the `cappa` package to same-named submodules (astral-sh/ty#4553).

## Conversion planners

Each `_plan_*` method on `VideoFile` adds `OutputStream` and `PlanAction` entries to one `ConversionPlan`, and `build_command()` emits one ffmpeg command. When adding or changing a planner:

- Never compute output stream indexes. Write `{n}` into `extra_args` (`"-b:a:{n}"`). `build_command()` substitutes it.
- An ffmpeg option with no stream index applies to every stream of that type. A planner that changes a codec or filter must cover every kept stream of that type.
- Mapping order is output order.
- A planner that overrides an earlier decision, such as VP9 dropping image subtitles, must correct the earlier `PlanAction`. Those records are the user-facing report.

## Conventions

- `VideoCleanError` and `VideoProbeError` mean skip this file and continue. `cappa.Exit` means stop the run, and batch loops must not catch it. A batch with any failure exits 1 after processing every remaining file.
- Models return message strings. The CLI layer prints them.
- Print through nclutils `pp`, not `Console.print`. `pp.success` and `pp.error` default to `markup=False`, which keeps bracketed filenames such as `Movie [x265].mkv` intact.
- Keep the `# isort: skip` comments and function-local imports in `utils/`, `models/video_file.py`, and `controllers/`. Without them, subpackages fail to import on their own.

## Tests

- Drive the real CLI, not `main()`:

    ```python
    with pytest.raises(cappa.Exit) as exc_info:
        invoke(obj=VidCleaner, argv=args, deps=[config_subcommand])
    ```

- `VideoFile.probe_box` re-probes whenever the cached box's `path_to_file` differs from `latest_temp_path()`, so a `get_probe_as_box` mock fires more than once per file. A call-count assertion on it must count those re-probes.
