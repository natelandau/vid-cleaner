"""Instantiate Configuration class and set default values."""

import shutil
from pathlib import Path
from typing import Any

import tomllib
import typer
from loguru import logger

PATH_CONFIG_DEFAULT = Path(__file__).parent / "default_config.toml"


class Config:
    """Representation of a configuration file."""

    def __init__(
        self,
        config_path: Path | None = None,
        context: dict[str, Any] = {},
    ) -> None:
        """Initialize configuration file."""
        self.config_path = config_path.expanduser().resolve() if config_path else None
        self.context = context

        if not config_path or not self.config_path.exists():
            self._create_config()

        self.config = self._load_config()

    def __repr__(self) -> str:
        """Return string representation of Config."""
        return f"{self.config}"

    def _create_config(self) -> None:
        """Create a configuration file from the default when it does not exist."""
        if self.config_path is None:
            logger.error("No configuration file specified")
            raise typer.Exit(code=1)

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PATH_CONFIG_DEFAULT, self.config_path)
        logger.success(f"Created default configuration file at {self.config_path}")
        logger.info(f"Please edit {self.config_path} before continuing")
        raise typer.Exit(code=1)

    def _load_config(self) -> dict[str, Any]:
        """Load the configuration file."""
        logger.debug(f"Loading configuration from {self.config_path}")
        with self.config_path.open("rb") as f:
            try:
                config = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                logger.exception(f"Could not parse '{self.config_path}'")
                raise typer.Exit(code=1) from e

        if config == {}:
            logger.error(f"Configuration file '{self.config_path}' is empty or malformed")
            raise typer.Exit(code=1)

        # Add context to config
        for key, value in self.context.items():
            config[key] = value

        return config

    def get(
        self, key: str, default: str | None = None, pass_none: bool = False
    ) -> str | int | bool | list[str | int] | None:
        """Get a value from the configuration file.

        Args:
            key (str): The name of the config variable.
            default (str, optional): The default value to use if the key is not set. Defaults to None.
            pass_none (bool, optional): Whether to pass None if the key does not exist. Defaults to False.

        Returns:
            str | int | bool | list[str | int] | None: The value of the config variable.

        """
        value = self.config.get(key, default)

        if value is None and not pass_none:
            logger.error(f"Config variable {key} is not set.")
            raise typer.Exit(code=1)

        if not value and not isinstance(value, bool):
            return None

        if isinstance(value, str):
            return value.strip().lstrip('"').rstrip('"')

        return value
