"""Settings and configuration for vid-cleaner."""

import cappa
from dynaconf import Dynaconf, ValidationError, Validator
from nclutils import pp
from validators import url as url_validate

from vid_cleaner.constants import (
    CACHE_DIR,
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT_PATH,
    USER_CONFIG_PATH,
)

settings = Dynaconf(
    envvar_prefix="VIDCLEANER_",
    settings_files=[DEFAULT_CONFIG_PATH, USER_CONFIG_PATH, PROJECT_ROOT_PATH / "dev-config.toml"],
    environments=False,
    validate_on_update="all",
)

settings.validators.register(
    Validator("cache_dir", default=CACHE_DIR),
    Validator(
        "keep_local_subtitles",
        default=False,
        cast=bool,
    ),
    Validator(
        "langs_to_keep",
        default=["en"],
        cast=list,
        condition=lambda x: isinstance(x, list),
        messages={"condition": "'{name}' must be a list"},
    ),
    Validator(
        "keep_commentary",
        default=False,
        cast=bool,
    ),
    Validator(
        "drop_local_subs",
        default=False,
        cast=bool,
    ),
    Validator(
        "keep_all_subtitles",
        default=False,
        cast=bool,
    ),
    Validator(
        "drop_original_audio",
        default=False,
        cast=bool,
    ),
    Validator(
        "downmix_stereo",
        default=False,
        cast=bool,
    ),
    Validator(
        "radarr_url",
        default="",
        cast=str,
        condition=lambda x: url_validate(x) or not x,
        messages={"condition": "'{name}' in settings must be a valid URL: '{value}'"},
    ),
    Validator(
        "sonarr_url",
        default="",
        cast=str,
        condition=lambda x: url_validate(x) or not x,
        messages={"condition": "'{name}' in settings must be a valid URL: '{value}'"},
    ),
    Validator(
        "radarr_api_key",
        default="",
        cast=str,
        must_exist=True,
        condition=lambda x: x,
        when=Validator("radarr_url", must_exist=True, condition=lambda x: x),
        messages={"condition": "'{name}' must be set if 'radarr_url' is set"},
    ),
    Validator(
        "sonarr_api_key",
        default="",
        cast=str,
        must_exist=True,
        condition=lambda x: x,
        when=Validator("sonarr_url", must_exist=True, condition=lambda x: x),
        messages={"condition": "'{name}' must be set if 'sonarr_url' is set"},
    ),
)


def validate_settings() -> Dynaconf:
    """Validate all registered configuration settings.

    Ensure configuration values meet expected types and constraints before the application runs. This validation step prevents runtime errors from invalid settings.

    Args:
        None

    Returns:
        Dynaconf: The validated settings object

    Raises:
        cappa.Exit: If validation fails, exits with code 1 and prints validation errors
    """
    try:
        settings.validators.validate_all()
    except ValidationError as e:
        accumulative_errors = e.details

        for error in accumulative_errors:
            pp.error(error[1])
        raise cappa.Exit(code=1) from e
    except ValueError as e:
        pp.error(str(e))
        raise cappa.Exit(code=1) from e

    return settings
