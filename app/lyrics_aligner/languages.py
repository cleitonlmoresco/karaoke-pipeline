from __future__ import annotations

import re


# OpenAI Whisper multilingual language codes. Keeping this list in the app
# avoids importing the model runtime when the Resolve panel is opened.
WHISPER_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"), ("zh", "Chinese"), ("de", "German"),
    ("es", "Spanish"), ("ru", "Russian"), ("ko", "Korean"),
    ("fr", "French"), ("ja", "Japanese"), ("pt", "Portuguese"),
    ("tr", "Turkish"), ("pl", "Polish"), ("ca", "Catalan"),
    ("nl", "Dutch"), ("ar", "Arabic"), ("sv", "Swedish"),
    ("it", "Italian"), ("id", "Indonesian"), ("hi", "Hindi"),
    ("fi", "Finnish"), ("vi", "Vietnamese"), ("he", "Hebrew"),
    ("uk", "Ukrainian"), ("el", "Greek"), ("ms", "Malay"),
    ("cs", "Czech"), ("ro", "Romanian"), ("da", "Danish"),
    ("hu", "Hungarian"), ("ta", "Tamil"), ("no", "Norwegian"),
    ("th", "Thai"), ("ur", "Urdu"), ("hr", "Croatian"),
    ("bg", "Bulgarian"), ("lt", "Lithuanian"), ("la", "Latin"),
    ("mi", "Maori"), ("ml", "Malayalam"), ("cy", "Welsh"),
    ("sk", "Slovak"), ("te", "Telugu"), ("fa", "Persian"),
    ("lv", "Latvian"), ("bn", "Bengali"), ("sr", "Serbian"),
    ("az", "Azerbaijani"), ("sl", "Slovenian"), ("kn", "Kannada"),
    ("et", "Estonian"), ("mk", "Macedonian"), ("br", "Breton"),
    ("eu", "Basque"), ("is", "Icelandic"), ("hy", "Armenian"),
    ("ne", "Nepali"), ("mn", "Mongolian"), ("bs", "Bosnian"),
    ("kk", "Kazakh"), ("sq", "Albanian"), ("sw", "Swahili"),
    ("gl", "Galician"), ("mr", "Marathi"), ("pa", "Punjabi"),
    ("si", "Sinhala"), ("km", "Khmer"), ("sn", "Shona"),
    ("yo", "Yoruba"), ("so", "Somali"), ("af", "Afrikaans"),
    ("oc", "Occitan"), ("ka", "Georgian"), ("be", "Belarusian"),
    ("tg", "Tajik"), ("sd", "Sindhi"), ("gu", "Gujarati"),
    ("am", "Amharic"), ("yi", "Yiddish"), ("lo", "Lao"),
    ("uz", "Uzbek"), ("fo", "Faroese"), ("ht", "Haitian Creole"),
    ("ps", "Pashto"), ("tk", "Turkmen"), ("nn", "Nynorsk"),
    ("mt", "Maltese"), ("sa", "Sanskrit"), ("lb", "Luxembourgish"),
    ("my", "Myanmar"), ("bo", "Tibetan"), ("tl", "Tagalog"),
    ("mg", "Malagasy"), ("as", "Assamese"), ("tt", "Tatar"),
    ("haw", "Hawaiian"), ("ln", "Lingala"), ("ha", "Hausa"),
    ("ba", "Bashkir"), ("jw", "Javanese"), ("su", "Sundanese"),
    ("yue", "Cantonese"),
)


def whisper_language_code(value: str | None) -> str | None:
    """Return a Whisper language code, or None for automatic detection."""
    if value is None or value.strip().casefold() in {"", "auto", "automatic"}:
        return None
    normalized = value.strip().casefold()
    for code, name in WHISPER_LANGUAGES:
        if normalized in {code.casefold(), name.casefold()}:
            return code
    return normalized


# Frequent words that let the app infer the sung language from the lyrics when
# automatic detection picks the wrong language for a quiet or heavily produced
# vocal track. Only clearly distinctive words carry weight.
_PORTUGUESE_MARKERS: dict[str, int] = {
    "não": 3,
    "nao": 2,
    "você": 3,
    "voce": 2,
    "vocês": 2,
    "voces": 2,
    "deus": 3,
    "senhor": 3,
    "então": 2,
    "entao": 2,
    "também": 3,
    "tambem": 2,
    "coração": 3,
    "coracao": 2,
    "irmão": 2,
    "irmao": 2,
    "minha": 2,
    "meu": 1,
    "nós": 2,
    "ao": 1,
    "dos": 1,
    "das": 1,
    "pelo": 1,
    "pela": 1,
    "pra": 2,
    "pro": 1,
    "glória": 2,
    "gloria": 1,
    "aleluia": 1,
}
_SPANISH_MARKERS: dict[str, int] = {
    "dios": 3,
    "señor": 3,
    "senor": 2,
    "corazón": 3,
    "corazon": 2,
    "también": 3,
    "tambien": 2,
    "entonces": 2,
    "quiero": 2,
    "nuestro": 2,
    "nuestra": 2,
    "porque": 1,
    "al": 1,
    "del": 1,
    "los": 1,
    "las": 1,
}
_ENGLISH_MARKERS: dict[str, int] = {
    "the": 2,
    "and": 2,
    "you": 1,
    "your": 1,
    "lord": 2,
    "god": 2,
    "love": 1,
    "with": 1,
    "heart": 1,
    "holy": 1,
    "praise": 1,
    "my": 1,
    "amazing": 2,
    "grace": 2,
}


def guess_language_from_text(text: str | None) -> str | None:
    """Infer 'pt', 'es', or 'en' from the lyric text when it is unambiguous.

    Whisper's acoustic detection can choose the wrong language for a song with
    loud instruments or a soft voice, which then makes the full alignment
    collapse. The written lyrics are a much stronger signal. Return None when
    the evidence is weak so callers can keep automatic detection.
    """
    if not text:
        return None
    normalized = text.casefold()
    words = re.findall(r"[^\W\d_]+", normalized, flags=re.UNICODE)
    if len(words) < 5:
        return None

    pt = sum(_PORTUGUESE_MARKERS.get(word, 0) for word in words)
    es = sum(_SPANISH_MARKERS.get(word, 0) for word in words)
    en = sum(_ENGLISH_MARKERS.get(word, 0) for word in words)

    # Orthography that is characteristic of each language.
    if re.search(r"[ãõ]", normalized):
        pt += 3
    if "ç" in normalized:
        pt += 2
    pt += 3 * len(re.findall(r"\b\w+ção\b|\b\w+ções\b", normalized))
    pt += 2 * len(re.findall(r"\b\w+ão\b", normalized))
    if "ñ" in normalized:
        es += 3
    if "¿" in normalized or "¡" in normalized:
        es += 3

    scores = sorted(
        (("pt", pt), ("es", es), ("en", en)),
        key=lambda item: item[1],
        reverse=True,
    )
    (best_code, best), (_, second) = scores[0], scores[1]
    if best < 4 or best - second < 2:
        return None
    return best_code

