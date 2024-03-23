"""Instantiate Configuration class and set default values."""

from pathlib import Path
from typing import Annotated, ClassVar

from confz import BaseConfig, CLArgSource, ConfigSources, FileSource
from pydantic import BeforeValidator

from vid_cleaner.constants import APP_DIR, CONFIG_PATH

PATH_CONFIG_DEFAULT = Path(__file__).parent / "default_config.toml"


def pass_opt_without_value(value: str) -> bool:
    """Confz does not work well with Typer options. Confz requires a value for each CLI option, but Typer does not. To workaround this, for example, if --log-to-file is passed, we set the value to "True" regardless of what follows the CLI option."""
    return bool(value)


OPT_BOOLEAN = Annotated[
    bool,
    BeforeValidator(pass_opt_without_value),
]


class VidCleanerConfig(BaseConfig):  # type: ignore [misc]
    """Configuration class for vid - cleaner."""

    log_to_file: OPT_BOOLEAN = False
    log_file: Path = APP_DIR / "vid_cleaner.log"
    keep_languages: frozenset[str] = frozenset(["eng"])
    radarr_api_key: str = ""
    radarr_url: str = ""
    sonarr_api_key: str = ""
    sonarr_url: str = ""
    tmdb_api_key: str = ""

    CONFIG_SOURCES: ClassVar[ConfigSources | None] = [
        FileSource(file=CONFIG_PATH),
        CLArgSource(remap={"log-file": "log_file", "log-to-file": "log_to_file"}),
    ]
