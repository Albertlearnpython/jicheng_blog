import re
import unicodedata

from pypinyin import lazy_pinyin


_CJK_SEQUENCE_PATTERN = re.compile(r"[\u3400-\u9fff]+")


def needs_translation(text):
    return bool(_CJK_SEQUENCE_PATTERN.search(text or ""))


def maybe_translate_user_message(text):
    cleaned = unicodedata.normalize("NFKC", (text or "").strip())
    if not needs_translation(cleaned):
        return cleaned

    pinyin_text = _CJK_SEQUENCE_PATTERN.sub(_replace_with_pinyin, cleaned)
    return (
        "The user originally wrote in Mandarin Chinese. "
        "The message below is a pinyin transliteration without tones. "
        "Interpret it as the intended Chinese message, then reply directly in concise Chinese.\n"
        f"Pinyin message: {pinyin_text}"
    )


def _replace_with_pinyin(match):
    return " ".join(lazy_pinyin(match.group(0)))
