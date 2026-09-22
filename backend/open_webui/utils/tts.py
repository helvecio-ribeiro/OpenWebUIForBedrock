"""Deterministic language and voice selection for multilingual TTS."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

SUPPORTED_TTS_LANGUAGES = frozenset({'en', 'es', 'pt'})

_SPANISH_WORDS = frozenset(
    {
        'al',
        'como',
        'con',
        'de',
        'del',
        'el',
        'en',
        'es',
        'esta',
        'este',
        'gracias',
        'hola',
        'la',
        'las',
        'lo',
        'los',
        'para',
        'puedo',
        'por',
        'que',
        'se',
        'sí',
        'su',
        'también',
        'una',
        'un',
        'usted',
        'y',
    }
)
_ENGLISH_WORDS = frozenset(
    {
        'a',
        'and',
        'are',
        'as',
        'at',
        'for',
        'from',
        'in',
        'is',
        'it',
        'of',
        'on',
        'that',
        'the',
        'this',
        'to',
        'was',
        'with',
        'you',
        'your',
    }
)
_PORTUGUESE_WORDS = frozenset(
    {
        'ao',
        'aos',
        'com',
        'como',
        'da',
        'das',
        'de',
        'do',
        'dos',
        'e',
        'em',
        'esta',
        'este',
        'eu',
        'foi',
        'mais',
        'não',
        'no',
        'nos',
        'o',
        'olá',
        'os',
        'para',
        'por',
        'português',
        'que',
        'se',
        'sim',
        'sua',
        'um',
        'uma',
        'você',
        'vocês',
    }
)
_WORDS = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)


def normalize_tts_language(value: str | None) -> str | None:
    """Normalize an IETF locale to one of the configured language keys."""
    if not value:
        return None
    language = re.split(r'[-_]', value.strip().lower(), maxsplit=1)[0]
    return language if language in SUPPORTED_TTS_LANGUAGES else None


def detect_tts_language(text: str, fallback: str = 'en') -> str:
    """Distinguish English, Spanish, and Portuguese using a fallback for ambiguity."""
    fallback = normalize_tts_language(fallback) or 'en'
    normalized = text.lower()
    words = _WORDS.findall(normalized)
    spanish_score = sum(word in _SPANISH_WORDS for word in words)
    english_score = sum(word in _ENGLISH_WORDS for word in words)
    portuguese_score = sum(word in _PORTUGUESE_WORDS for word in words)

    # Shared accents are deliberately omitted. These characters are much
    # stronger signals when distinguishing Spanish from Portuguese.
    spanish_score += 3 * len(re.findall(r'[ñ¿¡]', normalized))
    portuguese_score += 3 * len(re.findall(r'[ãõç]', normalized))

    scores = {'en': english_score, 'es': spanish_score, 'pt': portuguese_score}
    best_score = max(scores.values())
    winners = [language for language, score in scores.items() if score == best_score]
    if best_score and len(winners) == 1:
        return winners[0]
    return fallback


def resolve_tts_voice(
    *,
    text: str,
    voices: Mapping[str, str],
    explicit_language: str | None = None,
    preferred_language: str | None = None,
    default_language: str = 'en',
) -> tuple[str, str]:
    """Resolve one language and voice for an entire assistant response."""
    normalized_voices = {
        language: voice.strip()
        for raw_language, voice in voices.items()
        if (language := normalize_tts_language(raw_language)) and isinstance(voice, str) and voice.strip()
    }
    if not normalized_voices:
        raise ValueError('At least one supported TTS language voice is required')

    default = normalize_tts_language(default_language)
    if default not in normalized_voices:
        default = 'en' if 'en' in normalized_voices else next(iter(normalized_voices))

    explicit = normalize_tts_language(explicit_language)
    if explicit in normalized_voices:
        language = explicit
    else:
        preferred = normalize_tts_language(preferred_language)
        fallback = preferred if preferred in normalized_voices else default
        detected = detect_tts_language(text, fallback)
        language = detected if detected in normalized_voices else fallback

    return language, normalized_voices[language]


def tts_cache_key(engine: str, payload: Mapping) -> str:
    """Hash the effective upstream request, including its resolved voice."""
    cache_body = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(cache_body + engine.encode()).hexdigest()
