# Contributing to vid-cleaner

## Prerequisites

This project uses [uv](https://docs.astral.sh/uv/). To start developing, install uv using the recommended method for your operating system.

Once uv is installed, follow these steps to start developing.

1. Clone this repository. `git clone https://github.com/natelandau/vid-cleaner`
2. `cd` into the repository `cd vid-cleaner`
3. Instal the venv with uv `uv sync`
4. Activate your virtual environment with `source .venv/bin/activate`
5. Install the pre-commit hooks with `pre-commit install --install-hooks`.

Confirm everything is up and running by running `which vid-cleaner`. The output should reference your virtual environment and be something like `/Users/your-username/vid-cleaner/.venv/bin/vid-cleaner`.

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

Confirm you have installed the [pre-commit hooks](https://pre-commit.com/#installation) included in the repository. These automatically run some of the checks described earlier each time you run git commit, and over time can reduce development overhead quite considerably.

We use [Commitizen](https://github.com/commitizen-tools/commitizen) to manage commits and [Semantic Versioning](https://semver.org/) to manage version numbers.

Commit your code by running `cz c`.

## Running tasks

We use [Duty](https://pawamoy.github.io/duty/) as a task runner. Run `duty --list` to see a list of available tasks.

## Development Configuration

If you have a user config file, you can override the settings for development by adding a `dev-config.toml` file to the root level of the project. Any settings in this file will override settings in the default (user space) configuration file. You can easily create this file by running `cp src/vid_cleaner/default_config.toml dev-config.toml` from the root of the project.
