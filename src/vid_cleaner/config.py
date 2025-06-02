"""Instantiate settings default values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cappa
from dynaconf import Dynaconf, ValidationError, Validator
from nclutils import pp
from validators import url as url_validate

from vid_cleaner.constants import CACHE_DIR, DEFAULT_CONFIG_PATH, DEV_CONFIG_PATH, USER_CONFIG_PATH


@dataclass
class SettingsManager:
    """Manage application settings through a singleton pattern.

    Provide centralized configuration management with support for default values, project-specific overrides, and CLI argument integration. Handles initialization of settings from config files, environment variables, and runtime overrides while maintaining type safety through validators.
    """

    _instance: Dynaconf | None = None

    @classmethod
    def initialize(cls) -> Dynaconf:
        """Create and configure a new Dynaconf settings instance with default values.

        Configure settings with environment variables, config files, and validators for all supported settings. Return existing instance if already initialized.

        Returns:
            Dynaconf: The configured settings instance with all validators registered.

        Raises:
            cappa.Exit: If settings are not initialized or project name is not found in config.
        """
        if cls._instance is not None:
            return cls._instance

        settings = Dynaconf(
            environments=False,
            envvar_prefix="VIDCLEANER",
            settings_files=[DEFAULT_CONFIG_PATH, USER_CONFIG_PATH, DEV_CONFIG_PATH],
            validate_on_update="all",
        )

        # Register all validators at once
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
            Validator("overwrite", default=False, cast=bool),
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
            Validator("out_path", default=None),
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

        cls._instance = settings
        return settings

    @classmethod
    def apply_cli_settings(cls, cli_settings: dict[str, Any]) -> None:
        """Override existing settings with non-None values from CLI arguments.

        Update the settings singleton with any non-None values provided via command line arguments, preserving existing values for unspecified settings.

        Args:
            cli_settings (dict[str, Any]): Dictionary of settings from CLI arguments to apply as overrides.

        Raises:
            cappa.Exit: If settings singleton has not been initialized.
        """
        settings = cls._instance
        if settings is None:  # pragma: no cover
            msg = "Settings not initialized"
            pp.error(msg)
            raise cappa.Exit(code=1)

        # Filter out None values to avoid overriding with None
        cli_overrides = {k: v for k, v in cli_settings.items() if v is not None}
        settings.update(cli_overrides)


# Initialize settings singleton
settings = SettingsManager.initialize()
