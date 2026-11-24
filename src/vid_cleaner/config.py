"""Load and validate settings from config files, environment variables, and CLI flags."""

from __future__ import annotations

import os
import tomllib
from collections import ChainMap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from iso639 import Lang
from iso639.exceptions import DeprecatedLanguageValue, InvalidLanguageValue

from vid_cleaner import constants
from vid_cleaner.constants import CACHE_DIR

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class ConfigError(Exception):
    """Report every invalid setting found while loading the configuration.

    Attributes:
        errors: One line per invalid setting, naming the key, source, bad value, and what is allowed.
    """

    def __init__(self, errors: Sequence[str]) -> None:
        """Store each error line and join them for the exception message."""
        self.errors: tuple[str, ...] = tuple(errors)
        super().__init__("\n".join(self.errors))


API_KEY_SETTINGS = frozenset({"radarr_api_key", "sonarr_api_key", "tmdb_api_key"})


@dataclass(frozen=True)
class Config:
    """Hold the resolved, validated settings for one run of the CLI."""

    langs_to_keep: tuple[str, ...]
    keep_local_subtitles: bool
    keep_commentary: bool
    drop_local_subs: bool
    keep_all_subtitles: bool
    drop_original_audio: bool
    downmix_stereo: bool
    overwrite: bool
    cache_dir: Path
    radarr_url: str
    radarr_api_key: str = field(repr=False)
    sonarr_url: str
    sonarr_api_key: str = field(repr=False)
    tmdb_api_key: str = field(repr=False)

    def redacted(self) -> dict[str, object]:
        """Return the settings as a dict safe to print, with each set API key masked.

        Returns:
            dict[str, object]: Every setting, with non-empty API keys replaced by `***`.
        """
        values = asdict(self)
        for key in API_KEY_SETTINGS:
            if values[key]:
                values[key] = "***"
        return values


@dataclass(frozen=True)
class RunOptions:
    """Hold the CLI-only values for one run that are not configuration settings."""

    dryrun: bool = False
    verbosity: int = 0
    h265: bool = False
    vp9: bool = False
    video_1080: bool = False
    force: bool = False
    out: Path | None = None


@dataclass(frozen=True)
class LoadedConfig:
    """Pair the loaded settings with warnings the CLI prints before the run."""

    config: Config
    warnings: tuple[str, ...]


_ENV_PREFIX = "VIDCLEANER_"
_URL_API_KEYS = {"radarr_url": "radarr_api_key", "sonarr_url": "sonarr_api_key"}


@dataclass(frozen=True)
class _SourcedValue:
    """Pair a raw setting value with where it came from, for error messages."""

    value: object
    source: str


class _InvalidValueError(Exception):
    """Signal that a raw value cannot convert, carrying what was expected."""

    def __init__(self, expected: str) -> None:
        self.expected = expected
        super().__init__(expected)


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
        return value.lower() in {"true", "1"}
    msg = "a boolean (true, false, 1, or 0)"
    raise _InvalidValueError(msg)


def _to_str(value: object) -> str:
    if isinstance(value, str):
        return value
    msg = "a string"
    raise _InvalidValueError(msg)


def _to_path(value: object) -> Path:
    path = Path(_to_str(value)).expanduser()
    # `cache --clear` deletes this directory, so an empty or relative value must not
    # resolve against the working directory
    if not path.is_absolute():
        msg = "an absolute path or one starting with ~"
        raise _InvalidValueError(msg)
    return path


def _to_http_url(value: object) -> str:
    text = _to_str(value)
    if not text:
        return text
    expected = "an http or https URL with a host, or an empty string"
    try:
        parts = urlsplit(text)
    except ValueError as e:
        raise _InvalidValueError(expected) from e
    if parts.scheme in {"http", "https"} and parts.hostname:
        return text
    raise _InvalidValueError(expected)


def _split_list_text(text: str) -> object:
    if text.lstrip().startswith("["):
        try:
            return tomllib.loads(f"v = {text}")["v"]
        except tomllib.TOMLDecodeError as e:
            msg = f"a TOML array of strings ({e})"
            raise _InvalidValueError(msg) from e
    return [item.strip() for item in text.split(",")]


def _to_language_tuple(value: object) -> tuple[str, ...]:
    items = _split_list_text(value) if isinstance(value, str) else value
    expected = "a list of ISO 639 language codes, such as en,fr"
    if not isinstance(items, list | tuple) or not items:
        raise _InvalidValueError(expected)
    codes: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise _InvalidValueError(expected)
        try:
            Lang(item.strip())
        except (InvalidLanguageValue, DeprecatedLanguageValue) as e:
            raise _InvalidValueError(expected) from e
        codes.append(item.strip())
    return tuple(codes)


_CONVERTERS: dict[str, Callable[[object], object]] = {
    "langs_to_keep": _to_language_tuple,
    "keep_local_subtitles": _to_bool,
    "keep_commentary": _to_bool,
    "drop_local_subs": _to_bool,
    "keep_all_subtitles": _to_bool,
    "drop_original_audio": _to_bool,
    "downmix_stereo": _to_bool,
    "overwrite": _to_bool,
    "cache_dir": _to_path,
    "radarr_url": _to_http_url,
    "radarr_api_key": _to_str,
    "sonarr_url": _to_http_url,
    "sonarr_api_key": _to_str,
    "tmdb_api_key": _to_str,
}
# Every setting name, in `Config` field order. A setting a CLI flag can set has a
# same-named attribute on the command.
SETTING_NAMES: tuple[str, ...] = tuple(_CONVERTERS)
# Settings with a default computed at runtime rather than written in the default config file
_CODE_DEFAULTS: dict[str, object] = {"cache_dir": CACHE_DIR}


def _readable_api_key_warning(path: Path, data: Mapping[str, object]) -> str | None:
    holds_api_key = any(
        key.lower() in API_KEY_SETTINGS and isinstance(value, str) and value
        for key, value in data.items()
    )
    if holds_api_key and path.stat().st_mode & 0o077:
        return f"{path} holds API keys and other users can read it. Run `chmod 600 {path}`."
    return None


def _read_config_files(
    paths: Sequence[Path], errors: list[str], warnings: list[str]
) -> dict[str, _SourcedValue]:
    layer: dict[str, _SourcedValue] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as e:
            errors.append(f"{path}: invalid TOML ({e})")
            continue
        except UnicodeDecodeError as e:
            errors.append(f"{path}: cannot read file; expected UTF-8 text ({e})")
            continue
        except OSError as e:
            errors.append(f"{path}: cannot read file ({e})")
            continue
        for raw_key, value in data.items():
            # Keys match without regard to case, so a file with uppercase keys still loads
            key = raw_key.lower()
            if key in _CONVERTERS:
                layer[key] = _SourcedValue(value=value, source=str(path))
            else:
                warnings.append(f"Unknown setting `{raw_key}` in {path}; ignored")
        if warning := _readable_api_key_warning(path=path, data=data):
            warnings.append(warning)
    return layer


def _read_environment(environ: Mapping[str, str]) -> dict[str, _SourcedValue]:
    layer: dict[str, _SourcedValue] = {}
    for key in SETTING_NAMES:
        variable = f"{_ENV_PREFIX}{key.upper()}"
        # An empty variable counts as unset, so a blanked export cannot break every run
        if environ.get(variable):
            layer[key] = _SourcedValue(
                value=environ[variable], source=f"environment variable {variable}"
            )
    return layer


def _convert_all(values: Mapping[str, _SourcedValue], errors: list[str]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for key, converter in _CONVERTERS.items():
        sourced = values.get(key)
        if sourced is None:
            if key in _CODE_DEFAULTS:
                converted[key] = _CODE_DEFAULTS[key]
            else:
                errors.append(
                    f"`{key}` has no value; expected it in {constants.DEFAULT_CONFIG_PATH}"
                )
            continue
        try:
            converted[key] = converter(sourced.value)
        except _InvalidValueError as e:
            shown = "***" if key in API_KEY_SETTINGS else repr(sourced.value)
            errors.append(
                f"`{key}` from {sourced.source}: {shown} is invalid; expected {e.expected}"
            )
    for url_key, api_key in _URL_API_KEYS.items():
        if converted.get(url_key) and api_key in converted and not converted[api_key]:
            errors.append(f"`{api_key}` must be set when `{url_key}` is set")
    return converted


def load_config(
    *,
    config_paths: Sequence[Path] | None = None,
    environ: Mapping[str, str] | None = None,
    cli_overrides: Mapping[str, object] | None = None,
) -> LoadedConfig:
    """Build the validated settings for a run from config files, environment, and CLI flags.

    Each layer overrides the ones before it: the config files in order, then `VIDCLEANER_*`
    environment variables, then CLI flags.

    Args:
        config_paths: Config files in precedence order. Missing files are skipped. Defaults to
            the package default, user, and development config files.
        environ: Environment variables to read. Defaults to `os.environ`.
        cli_overrides: Raw values from CLI flags, keyed by `Config` field name. Defaults to None.

    Returns:
        LoadedConfig: The resolved settings and any warnings about the config files.

    Raises:
        ConfigError: If any value is invalid, listing every problem.
        ValueError: If `cli_overrides` has a key that is not a `Config` field.
    """
    cli_overrides = cli_overrides or {}
    if unknown_keys := sorted(set(cli_overrides) - set(SETTING_NAMES)):
        msg = (
            f"Unknown CLI override keys {unknown_keys}; "
            f"expected Config field names: {', '.join(SETTING_NAMES)}"
        )
        raise ValueError(msg)
    if config_paths is None:
        config_paths = (
            constants.DEFAULT_CONFIG_PATH,
            constants.USER_CONFIG_PATH,
            constants.DEV_CONFIG_PATH,
        )
    environ = os.environ if environ is None else environ

    errors: list[str] = []
    warnings: list[str] = []
    file_layer = _read_config_files(paths=config_paths, errors=errors, warnings=warnings)
    cli_layer = {
        key: _SourcedValue(value=value, source="CLI flag") for key, value in cli_overrides.items()
    }
    values = ChainMap(cli_layer, _read_environment(environ), file_layer)
    converted = _convert_all(values=values, errors=errors)
    if errors:
        raise ConfigError(errors)
    return LoadedConfig(config=Config(**converted), warnings=tuple(warnings))
