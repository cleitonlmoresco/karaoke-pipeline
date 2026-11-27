from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from .models import Cue


@dataclass(frozen=True)
class AudioTimeMap:
    """Monotonic mapping from a reference recording to a target recording."""

    source_times: tuple[float, ...]
    target_times: tuple[float, ...]
    offset_seconds: float
    confidence: float
    method: str = "subsequencia"

    def map_time(self, seconds: float) -> float:
        if not self.source_times:
            return float(seconds) + self.offset_seconds
        if len(self.source_times) == 1:
            return max(0.0, float(seconds) + self.offset_seconds)

        source = self.source_times
        target = self.target_times
        if seconds <= source[0]:
            return max(0.0, target[0] + (seconds - source[0]))
        if seconds >= source[-1]:
            return max(0.0, target[-1] + (seconds - source[-1]))

        # Binary search on the monotonic source axis.
        low = 0
        high = len(source) - 1
        while high - low > 1:
            middle = (low + high) // 2
            if source[middle] <= seconds:
                low = middle
            else:
                high = middle
        span = source[high] - source[low]
        if span <= 1e-9:
            return target[low]
        ratio = (seconds - source[low]) / span
        return target[low] + (target[high] - target[low]) * ratio

    def map_cues(
        self, cues: Sequence[Cue], *, target_duration: float | None = None
    ) -> list[Cue]:
        mapped: list[Cue] = []
        for cue in cues:
            start = self.map_time(cue.start)
            end = self.map_time(cue.end)
            if target_duration is not None:
                latest_start = max(0.0, target_duration - 0.25)
                start = min(max(0.0, start), latest_start)
                end = min(max(start + 0.2, end), max(latest_start + 0.2, start + 0.2))
            else:
                start = max(0.0, start)
                end = max(start + 0.2, end)
            mapped.append(Cue(text=cue.text, start=start, end=end))

        # Keep the mapped order and guarantee that one subtitle does not
        # overlap the next one.
        for index in range(len(mapped) - 1):
            current = mapped[index]
            following = mapped[index + 1]
            if current.end > following.start - 0.01:
                bounded_end = max(current.start + 0.2, following.start - 0.01)
                mapped[index] = replace(current, end=bounded_end)
        return mapped


def build_audio_time_map(
    reference_path: str | Path,
    target_path: str | Path,
    *,
    sample_rate: int = 22050,
    hop_length: int = 2048,
    anchor_times: Sequence[float] | None = None,
) -> AudioTimeMap:
    """Align two renditions of the same song using chroma + subsequence DTW.

    The reference is normally the sung recording. The target may contain extra
    instrumental passages (live intro, extended solo, etc.); subsequence DTW
    allows those target-only regions to be skipped instead of stretching the
    lyrics over them.
    """
    try:
        import librosa
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "librosa e numpy são necessários para sincronizar o playback."
        ) from exc

    reference = Path(reference_path)
    target = Path(target_path)
    if not reference.is_file():
        raise FileNotFoundError(reference)
    if not target.is_file():
        raise FileNotFoundError(target)

    reference_audio, _ = librosa.load(str(reference), sr=sample_rate, mono=True)
    target_audio, _ = librosa.load(str(target), sr=sample_rate, mono=True)
    if reference_audio.size == 0 or target_audio.size == 0:
        raise ValueError("Não foi possível ler o áudio para sincronizar o playback.")

    reference_chroma = librosa.feature.chroma_cens(
        y=reference_audio, sr=sample_rate, hop_length=hop_length
    )
    target_chroma = librosa.feature.chroma_cens(
        y=target_audio, sr=sample_rate, hop_length=hop_length
    )
    if reference_chroma.shape[1] < 2 or target_chroma.shape[1] < 2:
        raise ValueError("O áudio é curto demais para sincronizar o playback.")

    # Subsequence DTW fully matches the shorter rendition and may skip
    # regions of the longer one, which is what we want when the playback has
    # an extra intro or a longer instrumental passage. A plain DTW fits both
    # renditions end to end, which is the only sensible option when the
    # playback is a shortened or time-scaled version of the sung recording.
    # Both candidates are evaluated; a degenerate path (almost all of one
    # rendition mapped onto a tiny sliver of the other one) is discarded.
    def align(subseq: bool) -> "np.ndarray":
        _, path = librosa.sequence.dtw(
            X=reference_chroma,
            Y=target_chroma,
            metric="cosine",
            subseq=subseq,
        )
        # librosa returns the path from the end to the start. Its columns
        # always index the reference and the target respectively.
        return path[::-1]

    def mean_cosine_cost(path: "np.ndarray") -> float:
        reference_frames = reference_chroma[:, path[:, 0]]
        target_frames = target_chroma[:, path[:, 1]]
        denominator = np.linalg.norm(reference_frames, axis=0) * np.linalg.norm(
            target_frames, axis=0
        )
        dots = np.sum(reference_frames * target_frames, axis=0)
        cosine = np.divide(
            dots, denominator, out=np.ones_like(dots), where=denominator > 1e-9
        )
        return float(np.mean(1.0 - cosine))

    def used_fraction(indices: "np.ndarray", total: int) -> float:
        if total <= 0:
            return 0.0
        return float(indices.max() - indices.min() + 1) / float(total)

    candidates: list[tuple[float, str, "np.ndarray"]] = []
    for subseq, method in ((True, "subsequencia"), (False, "global")):
        path = align(subseq)
        reference_used = used_fraction(path[:, 0], reference_chroma.shape[1])
        target_used = used_fraction(path[:, 1], target_chroma.shape[1])
        if reference_used < 0.3 or target_used < 0.3:
            continue
        candidates.append((mean_cosine_cost(path), method, path))

    if not candidates:
        raise ValueError("Não foi possível estabelecer a sincronia com o playback.")

    subsequence = [item for item in candidates if item[1] == "subsequencia"]
    global_path = [item for item in candidates if item[1] == "global"]
    chosen = None
    if subsequence and global_path:
        best_subsequence = min(subsequence, key=lambda item: item[0])
        best_global = min(global_path, key=lambda item: item[0])
        # On near-ties keep the subsequence alignment: it can skip
        # playback-only passages instead of stretching the lyrics over them.
        chosen = (
            best_subsequence
            if best_subsequence[0] <= best_global[0] + 0.02
            else best_global
        )
    elif subsequence:
        chosen = min(subsequence, key=lambda item: item[0])
    else:
        chosen = min(global_path, key=lambda item: item[0])

    _cost, method, path = chosen
    source_times = path[:, 0].astype(float) * hop_length / sample_rate
    target_times = path[:, 1].astype(float) * hop_length / sample_rate

    # The path can repeat a source frame; keep a single strictly increasing
    # source axis for interpolation.
    keep = np.concatenate(([True], np.diff(source_times) > 1e-9))
    source_times = source_times[keep]
    target_times = target_times[keep]
    if len(source_times) < 2:
        raise ValueError("Não foi possível estabelecer a sincronia com o playback.")
    if target_times[-1] <= target_times[0]:
        raise ValueError("Não foi possível estabelecer a sincronia com o playback.")

    base_map = AudioTimeMap(
        source_times=tuple(float(value) for value in source_times),
        target_times=tuple(float(value) for value in target_times),
        offset_seconds=float(np.median(target_times - source_times)),
        confidence=1.0,
        method=method,
    )

    # Repetitive songs (chorus repeats) can make the DTW lock onto the wrong
    # chorus: locally consistent, globally shifted. Semantic anchors (the sung
    # cue starts) are matched against the target chroma to detect and fix
    # such shifts, and to measure a slow tempo drift.
    anchors = anchor_times or ()
    if len(anchors) >= 4:
        corrected = _correct_with_anchors(
            reference_chroma,
            target_chroma,
            anchors,
            base_map,
            sample_rate=sample_rate,
            hop_length=hop_length,
        )
        if corrected is not None:
            return corrected

    offsets = target_times - source_times
    offset = float(np.median(offsets))
    deviation = np.abs(offsets - offset)
    confidence = float(np.mean(deviation <= 0.75))
    return AudioTimeMap(
        source_times=tuple(float(value) for value in source_times),
        target_times=tuple(float(value) for value in target_times),
        offset_seconds=offset,
        confidence=confidence,
        method=method,
    )


def _correct_with_anchors(
    reference_chroma: "np.ndarray",
    target_chroma: "np.ndarray",
    anchors: Sequence[float],
    base_map: AudioTimeMap,
    *,
    sample_rate: int,
    hop_length: int,
) -> AudioTimeMap | None:
    """Refine the DTW map with semantic anchors (sung cue starts).

    For each anchor, a short chroma patch of the reference is searched around
    the DTW-mapped position in the target. The collected offsets are then
    reduced to a constant shift (same tempo) or a linear drift (tempo
    difference). Erratic anchors mean the audio match is unreliable, in which
    case None is returned and the DTW map is kept.
    """
    import numpy as np

    ref_frames = reference_chroma.shape[1]
    tgt_frames = target_chroma.shape[1]
    patch_frames = max(4, round(4.0 * sample_rate / hop_length))
    half_patch = patch_frames // 2
    search_radius = round(15.0 * sample_rate / hop_length)
    step_frames = max(1, round(0.2 * sample_rate / hop_length))

    matches: list[tuple[float, float, float]] = []
    for anchor in anchors:
        center = int(round(anchor * sample_rate / hop_length))
        if center < half_patch or center >= ref_frames - half_patch:
            continue
        patch = reference_chroma[:, center - half_patch : center + half_patch]
        base = base_map.map_time(anchor)
        base_idx = int(round(base * sample_rate / hop_length))
        lo = max(0, base_idx - search_radius)
        hi = min(tgt_frames - patch.shape[1], base_idx + search_radius)
        best_sim = -1e9
        best = -1
        for j in range(lo, hi + 1, step_frames):
            target_patch = target_chroma[:, j : j + patch.shape[1]]
            denominator = np.linalg.norm(patch) * np.linalg.norm(target_patch)
            if denominator <= 1e-9:
                continue
            similarity = float(np.sum(patch * target_patch) / denominator)
            # Viés suave para a posição do DTW: em áudio repetitivo vários
            # trechos empatam e o empate fica com o casamento já estabelecido.
            distance = abs(j - base_idx) / (search_radius + 1)
            similarity -= 0.05 * distance
            if similarity > best_sim:
                best_sim = similarity
                best = j
        if best >= 0:
            mapped_time = (best + half_patch) * hop_length / sample_rate
            matches.append((anchor, mapped_time - anchor, best_sim))

    if len(matches) < 3:
        return None

    offsets = np.array([offset for _, offset, _ in matches])
    times = np.array([anchor for anchor, _, _ in matches])
    median_offset = float(np.median(offsets))
    inlier_mask = np.abs(offsets - median_offset) <= 1.0
    inlier_count = int(np.sum(inlier_mask))

    if inlier_count < max(3, int(len(matches) * 0.6)):
        # Anchors disagree with each other: keep the DTW map.
        return None

    inlier_times = times[inlier_mask]
    inlier_offsets = offsets[inlier_mask]

    # Slow tempo drift: fit a line to the inlier offsets. Drift beyond 6 s
    # over 10 minutes is treated as unreliable; keep the constant shift.
    slope = 0.0
    intercept = median_offset
    span = float(np.ptp(inlier_times))
    if len(inlier_times) >= 4 and span > 20.0:
        centered = inlier_times - float(np.mean(inlier_times))
        denominator = float(np.sum(centered * centered))
        if denominator > 1e-9:
            slope = float(np.sum(centered * inlier_offsets) / denominator)
            intercept = float(np.mean(inlier_offsets))
            total_drift = abs(slope) * span
            if total_drift > 6.0 or total_drift < 0.15:
                # Deriva grande demais é sinal de casamento ruim; deriva
                # desprezível é apenas ruído. Nos dois casos, deslocamento
                # constante é a resposta mais segura.
                slope = 0.0
                intercept = float(np.median(inlier_offsets))

    # Rebuild a dense map from the fitted trend.
    duration = ref_frames * hop_length / sample_rate
    points = []
    cursor = 0.0
    while cursor <= duration + 0.5:
        mapped = cursor + intercept + slope * cursor
        points.append((cursor, max(0.0, mapped)))
        cursor += 1.0
    source_points = tuple(item[0] for item in points)
    target_points = tuple(item[1] for item in points)

    if len(source_points) < 2:
        return None
    if target_points[-1] <= target_points[0]:
        return None

    residual = inlier_offsets - (intercept + slope * inlier_times)
    confidence = float(np.mean(np.abs(residual) <= 0.75))
    method = "ancoras" if slope == 0.0 else "ancoras-deriva"
    return AudioTimeMap(
        source_times=source_points,
        target_times=target_points,
        offset_seconds=float(intercept),
        confidence=max(0.0, min(1.0, confidence)),
        method=method,
    )


__all__ = ["AudioTimeMap", "build_audio_time_map"]
