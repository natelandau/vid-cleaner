# Contributing to vid-cleaner

## Prerequisites

This project uses [uv](https://docs.astral.sh/uv/). To start developing, install uv using the recommended method for your operating system.

Once uv is installed, follow these steps to start developing.

1. Clone this repository. `git clone https://github.com/natelandau/vid-cleaner`
2. `cd` into the repository `cd vid-cleaner`
3. Install the venv with uv `uv sync`
4. Activate your virtual environment with `source .venv/bin/activate`
5. Install the [prek](https://github.com/j178/prek) hooks with `prek install`.

Confirm everything is installed by running `which vidcleaner`. The output must reference your virtual environment, for example `/Users/your-username/vid-cleaner/.venv/bin/vidcleaner`.

## Developing

Some things to consider when developing:

-   Ensure all code is documented in docstrings
-   Ensure all code is typed
-   Write unit tests for all new functions
-   Write integration tests for all new features

### Before committing

-   Ensure all the code passes linting with `duty lint`
-   Ensure all the code passes tests with `duty test`

### Committing

Confirm you have installed the [prek hooks](https://github.com/j178/prek) included in the repository. These automatically run some of the checks described earlier each time you run git commit, and over time can reduce development overhead quite considerably.

We use [Commitizen](https://github.com/commitizen-tools/commitizen) to manage commits and [Semantic Versioning](https://semver.org/) to manage version numbers.

Commit your code by running `cz c`.

## Running tasks

We use [Duty](https://pawamoy.github.io/duty/) as a task runner. Run `duty --list` to see a list of available tasks.

## Development Configuration

To override your configuration during development, create `.development/dev-config.toml` in the project root. Settings in this file override both the packaged defaults and your user configuration file. Create it with these commands, from the root of the project:

```bash
mkdir -p .development
cp src/vid_cleaner/default_config.toml .development/dev-config.toml
```
