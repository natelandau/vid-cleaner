"""Test loading settings from config files, environment variables, and CLI flags."""

from pathlib import Path

import pytest

from vid_cleaner import constants
from vid_cleaner.config import ConfigError, load_config
from vid_cleaner.constants import VerifyLevel


def write_toml(path: Path, text: str) -> Path:
    """Write `text` to `path` and return the path."""
    path.write_text(text)
    return path


def _config_paths(tmp_path: Path, user_toml: str = "") -> list[Path]:
    return [constants.DEFAULT_CONFIG_PATH, write_toml(tmp_path / "user.toml", user_toml)]


def _errors(tmp_path: Path, *, user_toml: str = "", environ=None, cli=None) -> tuple[str, ...]:
    with pytest.raises(ConfigError) as exc_info:
        load_config(
            config_paths=_config_paths(tmp_path, user_toml),
            environ=environ or {},
            cli_overrides=cli or {},
        )
    return exc_info.value.errors


@pytest.mark.parametrize(
    ("key", "file_value", "env_value", "cli_value", "expected"),
    [
        ("keep_commentary", "true", None, None, True),
        ("keep_commentary", "true", "false", None, False),
        ("keep_commentary", "false", "true", False, False),
        ("langs_to_keep", '["en"]', "en,fr", None, ("en", "fr")),
        ("langs_to_keep", '["en"]', '["de"]', "es", ("es",)),
        ("tmdb_api_key", '"file"', "env", None, "env"),
        ("overwrite", "true", None, None, True),
        ("verify", '"quick"', "full", None, VerifyLevel.FULL),
        ("verify", '"full"', None, "off", VerifyLevel.OFF),
    ],
)
def test_precedence_cli_over_env_over_file(
    tmp_path, key, file_value, env_value, cli_value, expected
) -> None:
    """Verify a CLI flag beats an env var, which beats the user file."""
    environ = {} if env_value is None else {f"VIDCLEANER_{key.upper()}": env_value}
    cli = {} if cli_value is None else {key: cli_value}

    loaded = load_config(
        config_paths=_config_paths(tmp_path, f"{key} = {file_value}"),
        environ=environ,
        cli_overrides=cli,
    )

    assert getattr(loaded.config, key) == expected


def test_user_file_overrides_default_file(tmp_path) -> None:
    """Verify a later config file overrides an earlier one."""
    loaded = load_config(config_paths=_config_paths(tmp_path, "downmix_stereo = true"), environ={})

    assert loaded.config.downmix_stereo is True


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, constants.CACHE_DIR),
        ({"VIDCLEANER_CACHE_DIR": "~/vidcache"}, Path.home() / "vidcache"),
    ],
)
def test_cache_dir_defaults_and_expands_tilde(tmp_path, environ, expected) -> None:
    """Verify cache_dir falls back to the XDG cache path and expands `~`."""
    loaded = load_config(config_paths=_config_paths(tmp_path), environ=environ)

    assert loaded.config.cache_dir == expected


@pytest.mark.parametrize(
    ("variable", "text", "expected"),
    [
        ("VIDCLEANER_KEEP_COMMENTARY", "TRUE", True),
        ("VIDCLEANER_KEEP_COMMENTARY", "1", True),
        ("VIDCLEANER_KEEP_COMMENTARY", "0", False),
        ("VIDCLEANER_KEEP_COMMENTARY", "", False),
        ("VIDCLEANER_VERIFY", "FULL", VerifyLevel.FULL),
        ("VIDCLEANER_LANGS_TO_KEEP", " en , fr ", ("en", "fr")),
        ("VIDCLEANER_LANGS_TO_KEEP", '["en", "fr"]', ("en", "fr")),
    ],
)
def test_environment_value_parsing(tmp_path, variable, text, expected) -> None:
    """Verify env text converts to the setting's type, and an empty value counts as unset."""
    key = variable.removeprefix("VIDCLEANER_").lower()

    loaded = load_config(config_paths=_config_paths(tmp_path), environ={variable: text})

    assert getattr(loaded.config, key) == expected


@pytest.mark.parametrize(
    ("environ", "cli", "user_toml", "fragment"),
    [
        (
            {"VIDCLEANER_KEEP_COMMENTARY": "no"},
            {},
            "",
            "environment variable VIDCLEANER_KEEP_COMMENTARY",
        ),
        ({"VIDCLEANER_VERIFY": "fast"}, {}, "", "environment variable VIDCLEANER_VERIFY"),
        ({}, {"langs_to_keep": ""}, "", "CLI flag"),
        ({}, {}, 'langs_to_keep = ["english"]', "langs_to_keep"),
        ({}, {}, 'radarr_url = "ftp://host"', "radarr_url"),
        ({}, {}, 'radarr_url = "http://[bad"', "radarr_url"),
        ({}, {}, 'radarr_url = "http://"\nradarr_api_key = "k"', "radarr_url"),
        ({}, {}, 'cache_dir = ""', "cache_dir"),
        ({"VIDCLEANER_CACHE_DIR": "cache"}, {}, "", "environment variable VIDCLEANER_CACHE_DIR"),
        ({}, {}, 'radarr_url = "http://host:7878"', "`radarr_api_key`"),
        ({}, {}, "keep_commentary = [", "invalid TOML"),
    ],
)
def test_invalid_value_reports_key_and_source(tmp_path, environ, cli, user_toml, fragment) -> None:
    """Verify each invalid setting is reported with where it came from."""
    errors = _errors(tmp_path, user_toml=user_toml, environ=environ, cli=cli)

    assert any(fragment in error for error in errors), errors


def test_cli_language_list_is_trimmed(tmp_path) -> None:
    """Verify spaces around comma-separated CLI languages are ignored."""
    loaded = load_config(
        config_paths=_config_paths(tmp_path),
        environ={},
        cli_overrides={"langs_to_keep": " en , fr "},
    )

    assert loaded.config.langs_to_keep == ("en", "fr")


def test_every_invalid_setting_reported_at_once(tmp_path) -> None:
    """Verify one load reports all bad settings, not just the first."""
    errors = _errors(
        tmp_path,
        user_toml='keep_commentary = "yes"\noverwrite = 3\nsonarr_url = "nope"',
    )

    assert len(errors) == 3
    for key in ("keep_commentary", "overwrite", "sonarr_url"):
        assert any(key in error for error in errors), (key, errors)


def test_api_key_value_never_in_error(tmp_path) -> None:
    """Verify an invalid API key value is masked in the error line."""
    errors = _errors(tmp_path, user_toml="sonarr_api_key = 123456789")

    assert any("sonarr_api_key" in error for error in errors)
    assert not any("123456789" in error for error in errors)


def test_unreadable_user_config_is_a_config_error(tmp_path) -> None:
    """Verify a file that is not UTF-8 is reported by path instead of crashing."""
    user = tmp_path / "user.toml"
    user.write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(ConfigError) as exc_info:
        load_config(config_paths=[constants.DEFAULT_CONFIG_PATH, user], environ={})

    assert any(str(user) in error for error in exc_info.value.errors)


def test_uppercase_file_keys_apply(tmp_path) -> None:
    """Verify keys match without regard to letter case."""
    loaded = load_config(config_paths=_config_paths(tmp_path, 'TMDB_API_KEY = "k"'), environ={})

    assert loaded.config.tmdb_api_key == "k"


def test_unknown_keys_warn_and_continue(tmp_path) -> None:
    """Verify an unknown key or table produces a warning naming it and the file."""
    paths = _config_paths(tmp_path, "keep_comentary = true\n[default]\nx = 1")

    loaded = load_config(config_paths=paths, environ={})

    assert len(loaded.warnings) == 2
    assert all(str(paths[1]) in warning for warning in loaded.warnings)
    assert any("keep_comentary" in warning for warning in loaded.warnings)
    assert any("default" in warning for warning in loaded.warnings)


def test_redacted_hides_api_keys(tmp_path) -> None:
    """Verify the redacted view masks set API keys and keeps empty ones empty."""
    loaded = load_config(
        config_paths=_config_paths(tmp_path, 'tmdb_api_key = "secret"'), environ={}
    )

    redacted = loaded.config.redacted()

    assert redacted["tmdb_api_key"] == "***"
    assert redacted["radarr_api_key"] == ""
    assert "secret" not in str(redacted)
    assert "secret" not in repr(loaded.config)


def test_unknown_cli_override_key_raises_value_error(tmp_path) -> None:
    """Verify a programming error in CLI wiring fails loudly."""
    with pytest.raises(ValueError, match="not_a_setting"):
        load_config(
            config_paths=_config_paths(tmp_path), environ={}, cli_overrides={"not_a_setting": 1}
        )


@pytest.mark.parametrize(
    ("mode", "toml", "warns"),
    [
        (0o644, 'tmdb_api_key = "k3y-value"', True),
        (0o640, 'RADARR_API_KEY = "k3y-value"', True),
        (0o600, 'tmdb_api_key = "k3y-value"', False),
        (0o644, 'tmdb_api_key = ""', False),
    ],
)
def test_readable_api_key_file_warns(tmp_path, mode, toml, warns) -> None:
    """Verify a warning when a config file holding an API key is readable by others."""
    paths = _config_paths(tmp_path, toml)
    paths[1].chmod(mode)

    warnings = load_config(config_paths=paths, environ={}).warnings

    matching = [warning for warning in warnings if "chmod 600" in warning]
    assert bool(matching) is warns
    assert not any("k3y-value" in warning for warning in warnings)
