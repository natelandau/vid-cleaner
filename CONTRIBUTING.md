# Contributing to vid-cleaner

This guide shows you how to set up a development environment, run the checks, and commit a change.

## Set up the environment

You need these programs:

-   [uv](https://docs.astral.sh/uv/), to manage Python and the project dependencies
-   [ffmpeg](https://ffmpeg.org/) and [ffprobe](https://ffmpeg.org/ffprobe.html), to run `vidcleaner` and `duty dev_setup` by hand. The test suite does not need them.

To install the project, do these steps:

1. Clone the repository:

    ```bash
    git clone https://github.com/natelandau/vid-cleaner
    cd vid-cleaner
    ```

2. Install Python, the project, and the development tools into `.venv`:

    ```bash
    uv sync
    ```

3. Install the [prek](https://github.com/j178/prek) git hooks:

    ```bash
    uv run prek install
    ```

To make sure that the installation works, run `uv run vidcleaner --help`. The command prints the list of `vidcleaner` commands.

The commands in this guide start with `uv run`. If you activate the virtual environment with `source .venv/bin/activate`, you can omit `uv run`.

## Run the checks

We use [duty](https://pawamoy.github.io/duty/) as the task runner. Run `uv run duty --list` to see every task.

| Task                    | What it does                                                                         |
| ----------------------- | ------------------------------------------------------------------------------------ |
| `uv run duty lint`      | Runs ruff, the ty type checker, typos, actionlint, zizmor, and the prek hooks        |
| `uv run duty test`      | Runs the test suite with coverage. The run fails if coverage is less than 90%.       |
| `uv run duty dev_setup` | Creates sample `.mkv` files in `.dev/` with ffmpeg, for manual tests of `vidcleaner` |

To run only some tests, give pytest options to `duty test`. For example, `uv run duty test -k downmix` runs the tests with "downmix" in their names. Locally, `duty test` stops at the first failure and runs the tests that failed last time first.

Some checks run through [gate-cache](https://github.com/natelandau/gate-cache). If a check already passed on the same files, it prints `already passed on tree ..., skipping` and exits 0. To force a full run, set `GATE_CACHE_DISABLE=1`.

CI runs `duty lint` and `duty test` on every pull request and every push to `main`. The tests run on each supported Python version. Run both tasks before you open a pull request.

## Write the code

The project uses these conventions:

-   Every public function, class, and method has a Google-style docstring.
-   Every function signature has type annotations. ty checks `src/` and `tests/`.
-   Every change in behavior has a test. Tests run the real command line with `run_vidcleaner` from `tests/helpers.py`, not the internal functions. `tests/conftest.py` has the shared fixtures.
-   The `tests/` folders match the folders in `src/vid_cleaner/`. Put a test in the folder of the module that it covers. Subcommand tests go in `tests/cli/`.

## Commit a change

Commit messages use the [Conventional Commits](https://www.conventionalcommits.org/) format, and [Commitizen](https://github.com/commitizen-tools/commitizen) checks each message. To write a commit message with prompts, run `uv run cz c`.

Each commit runs the prek hooks. The hooks run the linters and the full test suite against the staged changes, so each commit must pass on its own. Version numbers follow [Semantic Versioning](https://semver.org/).

## Use a development configuration

To override your user configuration during development, create `.development/dev-config.toml` in the project root. Settings in this file override the packaged defaults and your user configuration file. To start from the defaults, run these commands from the project root:

```bash
mkdir -p .development
cp src/vid_cleaner/default_config.toml .development/dev-config.toml
```
