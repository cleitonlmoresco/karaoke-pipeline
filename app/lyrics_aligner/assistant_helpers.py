"""Helpers for the creation assistant (Bloco D)."""

from __future__ import annotations

from typing import Sequence


def detect_repeated_verses(cues: Sequence, threshold: float = 0.85) -> list[list[int]]:
    """Return groups of cue indices that are (near-)duplicates.

    Uses normalized text comparison.  Each group contains at least 2 indices.
    """
    from lyrics_aligner.matching import normalize_for_match

    normalized = [normalize_for_match(c.text if hasattr(c, "text") else str(c)) for c in cues]
    groups: list[list[int]] = []
    used: set[int] = set()
    for i in range(len(cues)):
        if i in used:
            continue
        group = [i]
        for j in range(i + 1, len(cues)):
            if j in used:
                continue
            a, b = normalized[i], normalized[j]
            if not a or not b:
                continue
            # Simple similarity: ratio of common chars
            if a == b:
                sim = 1.0
            else:
                # Jaccard on word sets
                wa, wb = set(a.split()), set(b.split())
                if not wa or not wb:
                    continue
                sim = len(wa & wb) / len(wa | wb)
            if sim >= threshold:
                group.append(j)
                used.add(j)
        if len(group) > 1:
            used.add(i)
            groups.append(group)
    return groups


def format_repeated_report(groups: list[list[int]], cues: Sequence) -> str:
    if not groups:
        return "Nenhum verso repetido detectado."
    lines = ["Versos repetidos detectados:"]
    for g in groups:
        texts = [ (cues[i].text if hasattr(cues[i], "text") else str(cues[i])) for i in g]
        lines.append(f"  Linhas {', '.join(str(i+1) for i in g)}: \"{texts[0][:40]}\"")
    return "\n".join(lines)


def translate_lyrics_prompt(text: str, target_lang: str) -> str:
    return (
        f"Traduza as seguintes letras de hino para {target_lang}, "
        "mantendo o mesmo número de linhas e a mesma estrutura (não adicione "
        "nem remova linhas). Retorne apenas as letras traduzidas, uma linha "
        f"por linha:\n\n{text}"
    )


def spell_check_prompt(text: str) -> str:
    return (
        "Corrija apenas erros ortográficos e de acentuação nas seguintes "
        "letras de hino, sem alterar a estrutura, o número de linhas ou as "
        "palavras intencionalmente (ex.: nomes próprios). Retorne apenas o "
        f"texto corrigido, uma linha por linha:\n\n{text}"
    )


def suggest_timing_prompt(cue_text: str, start: float, end: float) -> str:
    return (
        f"O cue \"{cue_text}\" está atualmente em {start:.2f}s - {end:.2f}s. "
        "Sugira, com base no ritmo típico de hinos, se o início e o fim "
        "deveriam ser ajustados. Responda em formato: INICIO: X.XX, FIM: Y.YY, MOTIVO: ..."
    )
