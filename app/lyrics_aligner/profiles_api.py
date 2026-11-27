"""Editable karaoke profile API for the desktop data layer.

This is an additive wrapper around :mod:`lyrics_aligner.karaoke_jobs`: every
function accepts an optional ``path`` so the UI (and tests) can operate on a
copy while the installed ``profiles.json`` stays untouched.  Default values
come from the profile-oriented constants in ``karaoke_jobs``; the webapp's
``DEFAULT_SETTINGS`` is intentionally *not* copied because it diverges from
the real profiles (max_duration_ms, font_size, stanza_mode, transition).
"""

from __future__ import annotations

import codecs
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from . import karaoke_jobs

PROFILES_PATH: Path = karaoke_jobs.PROFILES_PATH


def profiles_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else PROFILES_PATH


# The facade takes a keyword named ``profiles_path``; keep an unshadowed alias
# of this resolver for it.
_resolve_profiles_path = profiles_path


def default_settings() -> dict[str, Any]:
    """Return the profile-oriented defaults (not the divergent webapp ones)."""
    return {
        "alignment": copy.deepcopy(karaoke_jobs.DEFAULT_ALIGNMENT),
        "karaoke": copy.deepcopy(karaoke_jobs.DEFAULT_KARAOKE),
        "background": copy.deepcopy(karaoke_jobs.DEFAULT_BACKGROUND),
        "output": {"width": 1920, "height": 1080},
        "burn": {"preset": "medium", "crf": 18},
        "demo": False,
    }


def _read_profiles(path: str | Path | None = None) -> tuple[dict[str, Any], bool]:
    target = profiles_path(path)
    try:
        raw = target.read_bytes()
    except OSError:
        return {"active": None, "profiles": {}}, False
    bom = raw.startswith(codecs.BOM_UTF8)
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        return {"active": None, "profiles": {}}, bom
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        return {"active": None, "profiles": {}}, bom
    return data, bom


def _write_profiles(payload: Mapping[str, Any], path: str | Path | None = None) -> Path:
    """Atomically replace the profiles file.

    The new bytes are written to a sibling temporary file, flushed and
    fsynced, then moved with ``os.replace``.  A failure at any point leaves
    the original file byte-for-byte intact and removes the temporary file.
    """
    target = profiles_path(path)
    _, bom = _read_profiles(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"
    data = text.encode("utf-8")
    if bom:
        data = codecs.BOM_UTF8 + data

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
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
    return target


def _deep_merge(base: dict[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_profiles(path: str | Path | None = None) -> dict[str, Any]:
    return _read_profiles(path)[0]


def save_profiles(payload: Mapping[str, Any], path: str | Path | None = None) -> Path:
    return _write_profiles(payload, path)


def list_profiles(path: str | Path | None = None) -> list[str]:
    return list(load_profiles(path).get("profiles", {}).keys())


def get_profile(name: str, path: str | Path | None = None) -> dict[str, Any]:
    profile = load_profiles(path).get("profiles", {}).get(name)
    if profile is None:
        raise ValueError(f"Perfil '{name}' nao encontrado. Use 'profiles' para listar.")
    return profile


def get_active_profile(path: str | Path | None = None) -> str | None:
    return load_profiles(path).get("active")


def profile_settings(
    profile: str | Mapping[str, Any], path: str | Path | None = None
) -> dict[str, Any]:
    """Return a profile's settings merged over the profile-oriented defaults."""
    raw = get_profile(profile, path) if isinstance(profile, str) else profile
    return karaoke_jobs.profile_settings(dict(raw))


def _validate_name(name: str) -> str:
    cleaned = str(name).strip()
    if not cleaned:
        raise ValueError("Nome de perfil vazio.")
    return cleaned


def create_profile(
    name: str,
    *,
    display_name: str | None = None,
    description: str = "",
    projects_root: str = "",
    settings: Mapping[str, Any] | None = None,
    extras: Mapping[str, Any] | None = None,
    based_on: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a new profile, optionally seeded from an existing one."""
    key = _validate_name(name)
    payload = load_profiles(path)
    profiles = payload["profiles"]
    if key in profiles:
        raise ValueError(f"Perfil '{key}' ja existe.")

    base: dict[str, Any] = {}
    if based_on is not None:
        base = copy.deepcopy(get_profile(based_on, path))
    profile = copy.deepcopy(base)
    profile["name"] = str(display_name if display_name is not None else (base.get("name") or key))
    if description or "description" not in profile:
        profile["description"] = description
    if projects_root or "projects_root" not in profile:
        profile["projects_root"] = projects_root
    current = profile.get("settings") if isinstance(profile.get("settings"), dict) else {}
    profile["settings"] = _deep_merge(current, settings or {}) if settings else copy.deepcopy(current)
    if not profile["settings"]:
        profile["settings"] = default_settings()
    extra_base = profile.get("extras") if isinstance(profile.get("extras"), dict) else {}
    profile["extras"] = _deep_merge(extra_base, extras or {}) if extras else copy.deepcopy(extra_base)

    profiles[key] = profile
    _write_profiles(payload, path)
    return copy.deepcopy(profile)


def duplicate_profile(
    source: str,
    new_name: str,
    *,
    display_name: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Deep-copy an existing profile under a new key."""
    return create_profile(
        new_name,
        display_name=display_name,
        based_on=source,
        path=path,
    )


def update_profile(
    name: str,
    *,
    settings: Mapping[str, Any] | None = None,
    extras: Mapping[str, Any] | None = None,
    description: str | None = None,
    projects_root: str | None = None,
    display_name: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply a partial, deep-merged edit to an existing profile."""
    payload = load_profiles(path)
    profile = payload["profiles"].get(name)
    if profile is None:
        raise ValueError(f"Perfil '{name}' nao encontrado. Use 'profiles' para listar.")
    if settings:
        current = profile.get("settings") if isinstance(profile.get("settings"), dict) else {}
        profile["settings"] = _deep_merge(current, settings)
    if extras:
        current = profile.get("extras") if isinstance(profile.get("extras"), dict) else {}
        profile["extras"] = _deep_merge(current, extras)
    if description is not None:
        profile["description"] = description
    if projects_root is not None:
        profile["projects_root"] = projects_root
    if display_name is not None:
        profile["name"] = display_name
    _write_profiles(payload, path)
    return copy.deepcopy(profile)


def set_profile_setting(
    name: str,
    section: str,
    key: str,
    value: Any,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for a single nested setting change."""
    return update_profile(name, settings={section: {key: value}}, path=path)


def save_profile_settings(
    name: str,
    settings: Mapping[str, Any],
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Replace/merge a profile's settings payload."""
    return update_profile(name, settings=settings, path=path)


def activate_profile(name: str, path: str | Path | None = None) -> str:
    payload = load_profiles(path)
    if name not in payload["profiles"]:
        raise ValueError(f"Perfil '{name}' nao encontrado. Use 'profiles' para listar.")
    payload["active"] = name
    _write_profiles(payload, path)
    return name


def _original_bytes(target: Path) -> bytes | None:
    try:
        return target.read_bytes()
    except OSError:
        return None


def _restore_bytes(target: Path, raw: bytes | None) -> None:
    """Best-effort, atomic restoration used only if the facade fails."""
    try:
        if raw is None:
            if target.exists():
                target.unlink()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
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
    except OSError:
        # Never mask the original failure with a cleanup failure.
        pass


def set_active_profile(
    name: str,
    *,
    profiles_path: str | Path | None = None,
    settings_path: str | Path | None = None,
) -> str:
    """Activate ``name`` with a single owner for the active profile.

    ``profiles.json.active`` and ``settings.json.profile`` are written together
    through the existing atomic writers.  ``activate_profile`` is left intact
    for backwards compatibility and is called exactly once here (no double
    write).  Any failure rolls both files back to their previous bytes.
    """
    from . import settings as settings_module

    # Validate before touching either file: a bad name leaves no trace.
    if name not in load_profiles(profiles_path)["profiles"]:
        raise ValueError(f"Perfil '{name}' nao encontrado. Use 'profiles' para listar.")

    profiles_target = _resolve_profiles_path(profiles_path)
    settings_target = settings_module.settings_path(settings_path)
    saved_profiles = _original_bytes(profiles_target)
    saved_settings = _original_bytes(settings_target)
    try:
        activate_profile(name, profiles_path)
        settings_module.update_settings({"profile": name}, settings_path)
    except BaseException:
        _restore_bytes(profiles_target, saved_profiles)
        _restore_bytes(settings_target, saved_settings)
        raise
    return name


def delete_profile(
    name: str, path: str | Path | None = None, *, force: bool = False
) -> bool:
    """Remove a profile.

    Removing the active profile is refused with ``ValueError`` unless
    ``force=True``; in that case the first remaining profile becomes active
    (or ``None`` when the deleted profile was the last one).  Nothing is
    changed when the guard fires.  ``path`` remains positional for backwards
    compatibility; ``force`` is keyword-only.
    """
    payload = load_profiles(path)
    profiles = payload["profiles"]
    if name not in profiles:
        return False
    if payload.get("active") == name and not force:
        raise ValueError(
            f"Perfil ativo '{name}' nao pode ser excluido sem force=True."
        )
    profiles.pop(name)
    if payload.get("active") == name:
        payload["active"] = next(iter(profiles), None)
    _write_profiles(payload, path)
    return True


def rename_profile(
    old_name: str, new_name: str, path: str | Path | None = None
) -> str:
    key = _validate_name(new_name)
    payload = load_profiles(path)
    if old_name not in payload["profiles"]:
        raise ValueError(f"Perfil '{old_name}' nao encontrado. Use 'profiles' para listar.")
    if key != old_name and key in payload["profiles"]:
        raise ValueError(f"Perfil '{key}' ja existe.")
    payload["profiles"][key] = payload["profiles"].pop(old_name)
    if payload.get("active") == old_name:
        payload["active"] = key
    _write_profiles(payload, path)
    return key
