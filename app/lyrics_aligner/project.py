"""Editable hymn project model.

A project persists the cues (with word timings), settings, media references and
an audit trail of manual edits.  The lyrics themselves are never rewritten: the
model only stores timings and an ``enabled``/``deleted`` flag, so the original
text is always recoverable.
"""

from __future__ import annotations

import codecs
import copy
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import Cue
from .timeline_validation import validate_timeline


@dataclass
class WordTiming:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": round(float(self.start), 3),
                "end": round(float(self.end), 3)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WordTiming":
        return cls(text=str(data.get("text", "")),
                   start=float(data.get("start", 0.0)),
                   end=float(data.get("end", 0.0)))


@dataclass
class CueTiming:
    index: int
    text: str
    start: float
    end: float
    words: list[WordTiming] = field(default_factory=list)
    enabled: bool = True
    deleted: bool = False
    source: str = "auto"  # auto | manual | inserted
    confidence: float | None = None  # 0..1 from alignment cross-check

    def to_cue(self) -> Cue | None:
        if self.deleted or not self.enabled:
            return None
        if not self.text.strip():
            return None
        return Cue(text=self.text, start=float(self.start), end=float(self.end))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "text": self.text,
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "words": [w.to_dict() for w in self.words],
            "enabled": bool(self.enabled),
            "deleted": bool(self.deleted),
            "source": self.source,
            "confidence": (None if self.confidence is None else round(float(self.confidence), 3)),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CueTiming":
        words = [WordTiming.from_dict(w) for w in (data.get("words") or [])]
        return cls(
            index=int(data.get("index", 0)),
            text=str(data.get("text", "")),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            words=words,
            enabled=bool(data.get("enabled", True)),
            deleted=bool(data.get("deleted", False)),
            source=str(data.get("source", "auto")),
            confidence=(None if data.get("confidence") is None else float(data.get("confidence"))),
        )

    @classmethod
    def from_cue(cls, cue: Cue, *, index: int, source: str = "auto",
                 words: Sequence[WordTiming] | None = None) -> "CueTiming":
        return cls(index=index, text=cue.text, start=cue.start, end=cue.end,
                   words=list(words or []), source=source)


@dataclass
class HymnProject:
    schema_version: int = 1
    id: str = ""
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    media: dict[str, str | None] = field(default_factory=dict)
    cues: list[CueTiming] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    deleted_cues: list[CueTiming] = field(default_factory=list)
    change_log: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = self.created_at

    # ---- cue helpers ----
    def visible_cues(self) -> list[CueTiming]:
        return [c for c in self.cues if not c.deleted and c.enabled]

    def to_cues(self) -> list[Cue]:
        result: list[Cue] = []
        for cue in self.cues:
            converted = cue.to_cue()
            if converted is not None:
                result.append(converted)
        return result

    def reindex(self) -> None:
        for position, cue in enumerate(self.cues, start=1):
            cue.index = position

    def validate(self, audio_duration: float | None = None) -> list:
        from .timeline_validation import validate_timeline

        return validate_timeline(self.to_cues(), audio_duration=audio_duration)

    def has_errors(self, audio_duration: float | None = None) -> bool:
        from .timeline_validation import has_errors as _has

        return _has(self.validate(audio_duration))

    # ---- editing with audit ----
    def record_change(self, action: str, **details: Any) -> None:
        self.change_log.append({"ts": _now(), "action": action, **details})
        self.updated_at = _now()

    def delete_cue(self, index: int, *, hard: bool = False) -> bool:
        for cue in self.cues:
            if cue.index == index:
                if hard:
                    self.cues.remove(cue)
                    self.deleted_cues.append(cue)
                else:
                    cue.deleted = True
                    cue.enabled = False
                self.record_change("delete", index=index, hard=hard, text=cue.text)
                self.reindex()
                return True
        return False

    def insert_cue(self, position: int, text: str, start: float, end: float,
                   *, source: str = "manual") -> CueTiming:
        cue = CueTiming(index=position, text=text, start=start, end=end,
                        source=source)
        self.cues.insert(max(0, min(position - 1, len(self.cues))), cue)
        self.record_change("insert", index=position, text=text,
                           start=start, end=end)
        self.reindex()
        return cue

    # ---- serialization ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "media": dict(self.media),
            "cues": [c.to_dict() for c in self.cues],
            "settings": dict(self.settings),
            "deleted_cues": [c.to_dict() for c in self.deleted_cues],
            "change_log": list(self.change_log),
            "notes": self.notes,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent) + "\n"

    def save(self, path: str | Path) -> Path:
        """Save atomically, keeping the BOM and skipping an identical payload."""
        target = Path(path)
        payload = self.to_dict()
        raw = _read_bytes(target)
        if _same_payload(raw, payload):
            return target
        data = self.to_json().encode("utf-8")
        if raw is not None and raw.startswith(codecs.BOM_UTF8):
            data = codecs.BOM_UTF8 + data
        if raw == data:
            return target
        _atomic_write_bytes(target, data)
        return target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HymnProject":
        project = cls(
            schema_version=int(data.get("schema_version", 1)),
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            media=_coerce_media(data.get("media")),
            cues=[CueTiming.from_dict(c) for c in (data.get("cues") or [])],
            settings=dict(data.get("settings") or {}),
            deleted_cues=[CueTiming.from_dict(c) for c in (data.get("deleted_cues") or [])],
            change_log=list(data.get("change_log") or []),
            notes=str(data.get("notes", "")),
        )
        project.reindex()
        return project

    @classmethod
    def load(cls, path: str | Path) -> "HymnProject":
        path = Path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_cues(
        cls,
        cues: Sequence[Cue],
        *,
        title: str = "",
        project_id: str = "",
        settings: Mapping[str, Any] | None = None,
        media: Mapping[str, str | None] | None = None,
    ) -> "HymnProject":
        project = cls(id=project_id, title=title,
                      settings=dict(settings or {}),
                      media=dict(media or {}))
        project.cues = [
            CueTiming.from_cue(cue, index=i, source="auto")
            for i, cue in enumerate(cues, start=1)
        ]
        return project

    # ---- migration from legacy tempos.json ----
    @classmethod
    def from_tempos_json(cls, path: str | Path, *,
                        title: str = "") -> "HymnProject":
        path = Path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        cues: list[CueTiming] = []
        for key in ("cantado", "playback_cantado"):
            entries = payload.get(key)
            if not entries:
                continue
            for i, entry in enumerate(entries, start=1):
                if isinstance(entry, (list, tuple)):
                    text = str(entry[0]) if entry else ""
                    words_raw = entry[1] if len(entry) > 1 else []
                elif isinstance(entry, dict):
                    text = str(entry.get("text", ""))
                    words_raw = entry.get("palavras") or entry.get("words") or []
                else:
                    continue
                words: list[WordTiming] = []
                start = 0.0
                end = 0.0
                if words_raw:
                    try:
                        start = float(words_raw[0][1])
                        end = float(words_raw[-1][2])
                    except (TypeError, ValueError, IndexError):
                        pass
                    for w in words_raw:
                        try:
                            words.append(WordTiming(
                                text=str(w[0]), start=float(w[1]), end=float(w[2])))
                        except (TypeError, ValueError, IndexError):
                            continue
                if not text.strip():
                    text = " ".join(w.text for w in words).strip()
                cues.append(CueTiming(index=i, text=text, start=start, end=end,
                                     words=words, source="legacy"))
            break  # use the first available track
        project = cls(title=title or path.parent.name, cues=cues)
        project.media = {"legacy_tempos": str(path)}
        return project

    # ---- webapp ``projeto.json`` adapter (additive) ----
    @classmethod
    def from_project_meta(
        cls, source: str | Path | Mapping[str, Any]
    ) -> "HymnProject":
        """Build a project from a legacy ``projeto.json`` payload.

        The webapp writes ``media`` as a string while this model uses a dict;
        the original string is kept on ``media["cantado"]`` and the raw payload
        is retained privately so a round trip can restore every legacy key.
        """
        if isinstance(source, (str, Path)):
            meta, bom = _read_project_meta(Path(source))
        elif isinstance(source, Mapping):
            meta, bom = dict(source), False
        else:
            raise TypeError("source deve ser um caminho ou um dict")
        media_raw = meta.get("media")
        media = _coerce_media(media_raw)
        playback = meta.get("playback")
        if _present(playback) and "playback" not in media:
            media["playback"] = playback
        project = cls(
            schema_version=int(meta.get("schema_version", 2)),
            id=str(meta.get("id", "")),
            title=str(meta.get("name") or meta.get("title") or ""),
            created_at=str(meta.get("created_at", "")),
            updated_at=str(meta.get("updated_at", "")),
            media=media,
            cues=[CueTiming.from_dict(c) for c in (meta.get("cues") or [])],
            settings=dict(meta.get("settings") or {}),
            deleted_cues=[
                CueTiming.from_dict(c) for c in (meta.get("deleted_cues") or [])
            ],
            change_log=list(meta.get("change_log") or []),
            notes=str(meta.get("notes", "")),
        )
        project._project_meta = meta
        project._project_bom = bom
        project._media_original = media_raw
        project._media_was_string = isinstance(media_raw, str)
        return project

    def to_project_meta(
        self,
        *,
        base: Mapping[str, Any] | None = None,
        path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Return a full legacy payload with all original keys preserved.

        ``base`` (or the raw payload captured by :meth:`from_project_meta`, or
        the file at ``path``) is copied first; only additive keys and values
        explicitly changed on the model are applied afterwards.  ``updated_at``
        is never bumped implicitly.
        """
        if base is None:
            if path is not None:
                base = _read_project_meta(Path(path))[0]
            else:
                base = getattr(self, "_project_meta", None) or {}
        out = dict(base)

        if self.title and (not out.get("name") or self.title != out.get("name")):
            out["name"] = self.title
        if self.settings:
            existing_settings = out.get("settings")
            if isinstance(existing_settings, Mapping):
                out["settings"] = _deep_merge(dict(existing_settings), self.settings)
            else:
                out["settings"] = copy.deepcopy(self.settings)

        if "media" not in out:
            out["media"] = dict(self.media)
        elif isinstance(out["media"], str):
            replacement = self.media.get("cantado")
            if _present(replacement) and replacement != out["media"]:
                out["media"] = replacement
        if "playback" not in out and _present(self.media.get("playback")):
            out["playback"] = self.media["playback"]

        out["schema_version"] = max(2, int(self.schema_version))
        out["cues"] = [c.to_dict() for c in self.cues]
        out["deleted_cues"] = [c.to_dict() for c in self.deleted_cues]
        out["change_log"] = list(self.change_log)
        out["notes"] = self.notes
        paths = dict(out.get("paths") or {})
        extra_paths = getattr(self, "_paths", None)
        if isinstance(extra_paths, Mapping):
            paths.update(extra_paths)
        out["paths"] = paths
        return out


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (dict, list, tuple, set)) and not value:
        return False
    return True


def _coerce_media(value: Any) -> dict[str, str | None]:
    """Accept both the legacy string and the model's dict representation."""
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, str):
        return {"cantado": value}
    return {}


def _deep_merge(base: dict[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_project_meta(path: Path) -> tuple[dict[str, Any], bool]:
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


def read_project_meta(path: str | Path) -> dict[str, Any]:
    """Read a ``projeto.json`` file without modifying it (BOM tolerant)."""
    return _read_project_meta(Path(path))[0]


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace ``path`` via temp file, fsync and atomic ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _same_payload(raw: bytes | None, payload: Mapping[str, Any]) -> bool:
    """True when the file already holds the same JSON object (any formatting)."""
    if raw is None:
        return False
    try:
        current = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        return False
    return isinstance(current, dict) and current == dict(payload)


def write_project_meta(
    path: str | Path, payload: Mapping[str, Any], *, bom: bool | None = None
) -> Path:
    """Atomically write a full ``projeto.json`` payload, preserving a BOM.

    An unchanged payload is never rewritten: the existing JSON object and the
    exact bytes are checked before a temp file is created.
    """
    target = Path(path)
    raw = _read_bytes(target)
    if bom is None:
        bom = bool(raw and raw.startswith(codecs.BOM_UTF8))
    if _same_payload(raw, payload):
        return target
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"
    data = text.encode("utf-8")
    if bom:
        data = codecs.BOM_UTF8 + data
    if raw == data:
        return target
    _atomic_write_bytes(target, data)
    return target


def update_project_meta(
    path: str | Path, **fields: Any
) -> dict[str, Any]:
    """Deep-merge ``fields`` into an existing payload, touching nothing else."""
    payload, bom = _read_project_meta(Path(path))
    merged = _deep_merge(payload, fields)
    write_project_meta(path, merged, bom=bom)
    return merged


def load_project_meta(path: str | Path) -> "HymnProject":
    """Load a webapp ``projeto.json`` as an editable :class:`HymnProject`."""
    return HymnProject.from_project_meta(path)


def save_project_meta(
    path: str | Path,
    project: HymnProject | Mapping[str, Any],
    *,
    base: Mapping[str, Any] | None = None,
) -> Path:
    """Save a project back to ``projeto.json`` without losing legacy keys.

    When ``base`` is omitted and the object did not come from
    :meth:`HymnProject.from_project_meta`, the destination file is read as the
    base so unknown keys survive.  The BOM is detected from that destination
    (``utf-8-sig``), with the object's captured flag only as a fallback.
    """
    target = Path(path)
    disk_payload, disk_bom = _read_project_meta(target)
    if base is None and not (
        isinstance(project, HymnProject) and hasattr(project, "_project_meta")
    ):
        base = disk_payload
    if isinstance(project, HymnProject):
        payload = project.to_project_meta(base=base)
        bom = disk_bom or bool(getattr(project, "_project_bom", False))
    else:
        payload = _deep_merge(dict(base), project) if base is not None else dict(project)
        bom = disk_bom
    return write_project_meta(target, payload, bom=bom)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")
