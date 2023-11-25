# video-transcode

Tools to transcode, inspect and convert videos.

## Install CLI apps

Use [PIPX](https://pypa.github.io/pipx/) to install this package from Github.

```bash
pipx install git+https://${GITHUB_TOKEN}@github.com/natelandau/video-transcode
```

Running the above command will install all script entry points as standalone scripts in the users' PATH.

**Note: You must be authenticated on Github for this to work**

**_Alternative_**
You can install from the local filesystem. This approach will create a link to the _editable version_ of the script which may cause problems if you plan on developing from that directory.

```bash
pipx install ~/path/to/project
```

## Contributing

## Setup: Once per project

1. Install Python 3.11 and [Poetry](https://python-poetry.org)
2. Clone this repository. `git clone https://github.com/natelandau/video-transcode`
3. Install the Poetry environment with `poetry install`.
4. Activate your Poetry environment with `poetry shell`.
5. Install the pre-commit hooks with `pre-commit install --install-hooks`.

## Developing

-   This project follows the [Conventional Commits](https://www.conventionalcommits.org/) standard to automate [Semantic Versioning](https://semver.org/) and [Keep A Changelog](https://keepachangelog.com/) with [Commitizen](https://github.com/commitizen-tools/commitizen).
    -   When you're ready to commit changes run `cz c`
-   Run `poe` from within the development environment to print a list of [Poe the Poet](https://github.com/nat-n/poethepoet) tasks available to run on this project. Common commands:
    -   `poe lint` runs all linters
    -   `poe test` runs all tests with Pytest
-   Run `poetry add {package}` from within the development environment to install a run time dependency and add it to `pyproject.toml` and `poetry.lock`.
-   Run `poetry remove {package}` from within the development environment to uninstall a run time dependency and remove it from `pyproject.toml` and `poetry.lock`.
-   Run `poetry update` from within the development environment to upgrade all dependencies to the latest versions allowed by `pyproject.toml`.
