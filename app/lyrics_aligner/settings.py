"""Unified desktop settings with additive, idempotent migration.

The Tk and web front ends historically kept their preferences in five
different files (``config.json``, ``desktop-preferences.json``,
``webapp-config.json``, ``~/.vilm_tkinter_config.json`` and
``profiles.json``).  This module reads those sources without ever modifying
them and writes the real destination ``settings.json`` under
:func:`platform_paths.application_data_root`.

Migration rules:

* additive only -- legacy files are never deleted or rewritten;
* idempotent -- loading twice, or saving the same payload twice, produces
  the same values (and the same bytes, since no timestamp is injected);
* explicit precedence, highest first (same order as ``_apply_legacy``):
    1. settings.json (the destination file -- already saved values);
    2. config.json (a separate legacy source);
    3. desktop-preferences.json;
    4. webapp-config.json;
    5. .vilm_tkinter_config.json;
    6. profiles.json (a profile's ``projects_root`` is legacy and has the
       LOWEST priority, so the user's Downloads folder is never replaced by
       the profile's OneDrive folder);
  chain: settings.json (destino) > config.json > desktop-preferences.json > webapp-config.json > .vilm_tkinter_config.json > profiles.json;
* empty values (None, empty/whitespace strings, empty containers) never
  overwrite a value that is already filled.
"""

from __future__ import annotations

import codecs
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from .platform_paths import application_data_root

SETTINGS_FILENAME = "settings.json"

# ``tema`` is a Portuguese alias of ``theme`` kept in sync by
# ``_normalize_aliases``.  It exists so callers can use either spelling.
DEFAULT_SETTINGS: dict[str, Any] = {
    "ui_language": "pt",
    "profile": "",
    "projects_root": "",
    "theme": "light",
    "tema": "light",
    "backend": "cpu",
    "font_family": "Segoe UI",
    "font_size": 10,
    "log_font_size": 9,
    "bg_color": "#F4EFE0",
    "fg_color": "#1A1A1A",
    "accent_color": "#043D20",
    "button_bg": "#EDE8DA",
    "window_width": 1100,
    "window_height": 720,
}

_TK_KEYS = (
    "theme",
    "font_family",
    "font_size",
    "log_font_size",
    "bg_color",
    "fg_color",
    "accent_color",
    "button_bg",
    "window_width",
    "window_height",
    "projects_root",
)


def settings_path(path: str | Path | None = None) -> Path:
    """Resolve the destination settings file (optional path allows isolated tests)."""
    return Path(path) if path is not None else application_data_root() / SETTINGS_FILENAME


def legacy_sources(
    paths: Mapping[str, str | Path] | None = None,
) -> dict[str, Path]:
    """Return the read-only legacy source files, optionally overridden."""
    root = application_data_root()
    install_root = Path(__file__).resolve().parents[2]
    sources = {
        "preferences": root / "desktop-preferences.json",
        "config": root / "config.json",
        "tk_config": Path.home() / ".vilm_tkinter_config.json",
        "webapp_config": install_root / "webapp-config.json",
        "profiles": install_root / "profiles.json",
    }
    if paths:
        for key, value in paths.items():
            if key in sources and value is not None:
                sources[key] = Path(value)
    return sources


def _read_json(path: Path) -> tuple[dict[str, Any], bool]:
    """Read a JSON object, returning it together with its BOM flag.

    Missing/corrupt files yield an empty object; legacy files are only ever
    read here, never written by this module.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return {}, False
    bom = raw.startswith(codecs.BOM_UTF8)
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        return {}, bom
    return (data if isinstance(data, dict) else {}), bom


def _write_json(path: Path, payload: Mapping[str, Any], *, bom: bool = False) -> Path:
    """Atomically replace the settings file, preserving BOM and bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"
    data = text.encode("utf-8")
    if bom:
        data = codecs.BOM_UTF8 + data

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return path


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (dict, list, tuple, set)) and not value:
        return False
    return True


def _sync_aliases(result: dict[str, Any]) -> None:
    """Keep ``theme``/``tema`` equal without injecting defaults."""
    theme = result.get("theme")
    tema = result.get("tema")
    if _present(theme):
        result["tema"] = theme
    elif _present(tema):
        result["theme"] = tema


def _normalize_aliases(result: dict[str, Any]) -> None:
    _sync_aliases(result)
    if not _present(result.get("theme")) and not _present(result.get("tema")):
        result["theme"] = DEFAULT_SETTINGS["theme"]
        result["tema"] = DEFAULT_SETTINGS["theme"]


def _apply_legacy(
    result: dict[str, Any],
    on_disk_payload: Mapping[str, Any],
    sources: Mapping[str, Path],
) -> None:
    """Merge legacy values in explicit precedence order (highest first).

    1. the existing destination file -- applied by ``load_settings`` already;
    2. ``config.json``; 3. ``desktop-preferences.json``;
    4. ``webapp-config.json``; 5. ``.vilm_tkinter_config.json``;
    6. ``profiles.json`` (lowest, including its ``projects_root``).
    A lower source may only fill a key that is ABSENT from the destination;
    a key explicitly present there (even with an empty value) is already
    filled and is never replaced by a legacy source.
    """
    # Presence, not truthiness: an explicit "" or 0 in settings.json is a
    # deliberate clear and must not be resurrected from a legacy file.
    filled = set(on_disk_payload)
    _sync_aliases(on_disk_payload)
    if "theme" in on_disk_payload or "tema" in on_disk_payload:
        filled.add("theme")
        filled.add("tema")

    def offer(key: str, value: Any) -> None:
        if key in filled or not _present(value):
            return
        result[key] = value
        filled.add(key)

    def offer_keys(source: Mapping[str, Any], keys: Iterable[str]) -> None:
        for key in keys:
            offer(key, source.get(key))

    config, _ = _read_json(sources["config"])
    offer_keys(config, DEFAULT_SETTINGS)

    prefs, _ = _read_json(sources["preferences"])
    offer_keys(prefs, ("ui_language", "profile"))

    webapp, _ = _read_json(sources["webapp_config"])
    offer_keys(webapp, ("projects_root",))

    tk_config, _ = _read_json(sources["tk_config"])
    offer_keys(tk_config, _TK_KEYS)

    profiles, _ = _read_json(sources["profiles"])
    entries = profiles.get("profiles")
    if isinstance(entries, Mapping):
        active = str(profiles.get("active") or "")
        offer("profile", active or None)
        chosen = entries.get(active) if active else None
        if isinstance(chosen, Mapping):
            offer("projects_root", chosen.get("projects_root"))

    _normalize_aliases(result)


def load_settings(
    path: str | Path | None = None,
    *,
    migrate: bool = True,
    legacy_paths: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Load settings without writing anything.

    Existing destination keys win even when their value is empty; defaults
    only fill absent slots and legacy files only fill slots absent from the
    destination file.
    """
    target = settings_path(path)
    on_disk_payload, _ = _read_json(target)
    _sync_aliases(on_disk_payload)
    result = copy.deepcopy(DEFAULT_SETTINGS)
    result.update(on_disk_payload)
    if migrate:
        _apply_legacy(result, on_disk_payload, legacy_sources(legacy_paths))
    else:
        _normalize_aliases(result)
    return result


def save_settings(
    settings: Mapping[str, Any],
    path: str | Path | None = None,
    *,
    legacy_paths: Mapping[str, str | Path] | None = None,
) -> Path:
    """Merge ``settings`` into any existing file and write it deterministically."""
    target = settings_path(path)
    _, bom = _read_json(target)
    # Loading first keeps migrated keys even when a caller writes a partial
    # payload; the explicit updates still take precedence.
    merged = load_settings(target, legacy_paths=legacy_paths)
    merged.update(dict(settings))
    _normalize_aliases(merged)
    return _write_json(target, merged, bom=bom)


def migrate_legacy_settings(
    path: str | Path | None = None,
    *,
    legacy_paths: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Create/refresh the unified settings file from legacy sources.

    Safe to call repeatedly: no timestamp is added and no legacy key is
    overwritten once it has a usable value.
    """
    merged = load_settings(path, legacy_paths=legacy_paths)
    save_settings(merged, path, legacy_paths=legacy_paths)
    return merged


def get_setting(
    key: str,
    default: Any = None,
    path: str | Path | None = None,
    *,
    migrate: bool = True,
    legacy_paths: Mapping[str, str | Path] | None = None,
) -> Any:
    return load_settings(path, migrate=migrate, legacy_paths=legacy_paths).get(key, default)


def update_settings(
    updates: Mapping[str, Any],
    path: str | Path | None = None,
    *,
    legacy_paths: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Apply a partial update and return the full resulting payload."""
    merged = load_settings(path, legacy_paths=legacy_paths)
    merged.update(dict(updates))
    save_settings(merged, path, legacy_paths=legacy_paths)
    return merged
