#!/usr/bin/env python3
# core/helper.py

"""
executor.call_appropriately() / executor.run_blocking() / executor.spawn_task() Usage Guide:

General Rule:
- If Step B depends on Step A finishing → use `executor.spawn_task(...)` in async code.
- If order/result doesn't matter → call `executor.spawn_task(...)` without await.
- Use the sync variant (`executor.run_blocking`) in sync code, async variant (`await executor.spawn_task(...)` or `executor.call_appropriately(...)`) in async code.

---

Sync context → def function():
    ✅ executor.run_blocking(coro, ...)
        - Blocking call, waits until done.
        - `result = ...` if you need the return value.
        - Calling without assignment still blocks, just ignores the result.
    🚫 executor.spawn_task(...)
        - Invalid outside async.
    🚫 executor.spawn_task(coro, ...)
        - Returns a Task you can't await; not useful in sync code.

Async context → async def function():
    ✅ result = await executor.spawn_task(coro, ...)
        - Pauses until the task completes; use when later steps depend on the result.
    ✅ executor.spawn_task(coro, ...)   # no await
        - execute-and-forget: launches task and continues immediately.
        - Only for background/optional work.
    ✅ result = await executor.call_appropriately(sync_func(*args, **kwargs), referrer="_module_referrer") (_module_referrer should be set to a string inside the module.)
        - Runs a synchronous function in a thread safely.
        - Correct for sync I/O or CPU-bound tasks in async context.
    ⚠️ executor.run_blocking(coro, ...)
        - Blocks the event loop; only use for truly blocking calls that must run synchronously.

---

Rule of Thumb:
- Use `await executor.spawn_task(...)` for most async calls where you need results.
- Drop `await` only for true background tasks.
- Use `executor.run_blocking(...)` in sync functions when you need the result.
- Use `executor.call_appropriately()` for running synchronous functions in async code.
"""

# ------------------------------------------------------------
# Gallery Title Cleaning
# ------------------------------------------------------------

# Symbols that are filesystem safe and should not be removed or replaced
ALLOWED_SYMBOLS = [ "!", "#", "&", "'", "(", ")", "\"", ",", ".", ":", "?", "_"]

# Fallback blacklist (these always become "_")
BROKEN_SYMBOL_BLACKLIST = [
    "↑", "↓", "→", "←",
    "♡", "♥", "★", "☆", "♪", "◆", "◇", "※", "✔", "✖",
    "◦", "∙", "•", "°", "●", "‣", "®", "©",
    "…", "@", "¬", "<", ">", "^", "¤", "¢",
    "♂", "♀", "⚥", "⚢", "⚣", "⚤", "⚦", "⚧", "⚨", "⚩", "♂", "♀",
    "£", "$", "¥",
    "ð", "§", "¶", "†", "‡", "‰", "µ", "¦", "~"
]

# Define explicit replacements for certain symbols
BROKEN_SYMBOL_REPLACEMENTS = {
    # Miscellaneous
    "ā": "a", "Ā": "A", "ē": "e", "Ē": "E",
    "ī": "i", "Ī": "I", "ō": "o", "Ō": "O",
    "ū": "u", "Ū": "U","ŕ": "r", "Ŕ": "R",
    "ś": "s", "Ś": "S", "ź": "z", "Ź": "Z", "ż": "z", "Ż": "Z",
    
    # Accented Latin vowels
    "à": "a", "À": "A", "á": "a", "Á": "A", "â": "a", "Â": "A",
    "ã": "a", "Ã": "A", "ä": "a", "Ä": "A", "å": "a", "Å": "A",
    "è": "e", "È": "E", "é": "e", "É": "E", "ê": "e", "Ê": "E",
    "ë": "e", "Ë": "E",
    "ì": "i", "Ì": "I", "í": "i", "Í": "I", "î": "i", "Î": "I",
    "ï": "i", "Ï": "I",
    "ò": "o", "Ò": "O", "ó": "o", "Ó": "O", "ô": "o", "Ô": "O",
    "õ": "o", "Õ": "O", "ö": "o", "Ö": "O", "ø": "o", "Ø": "O",
    "ù": "u", "Ù": "U", "ú": "u", "Ú": "U", "û": "u", "Û": "U",
    "ü": "u", "Ü": "U",
    "ý": "y", "Ý": "Y", "ÿ": "y", "Ÿ": "Y",

    # Special Latin ligatures & consonants
    "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ç": "c", "Ç": "C",
    "ñ": "n", "Ñ": "N",
    "ß": "ss",
    "Ð": "D",

    # Punctuation & misc symbols
    "’": "'", "¿": "?", "¡": "!",
    "ー": "-", "×": "X",

    # Greek letters
    "α": "a", "Α": "A",
    "β": "b", "Β": "B",
    "γ": "g", "Γ": "G",
    "δ": "d", "Δ": "D",
    "ε": "e", "Ε": "E",
    "ζ": "z", "Ζ": "Z",
    "η": "e", "Η": "E",
    "θ": "th", "Θ": "Th",
    "ι": "i", "Ι": "I",
    "κ": "k", "Κ": "K",
    "λ": "l", "Λ": "L",
    "μ": "m", "Μ": "M",
    "ν": "n", "Ν": "N",
    "ξ": "x", "Ξ": "X",
    "ο": "o", "Ο": "O",
    "π": "p", "Π": "P",
    "ρ": "r", "Ρ": "R",
    "σ": "s", "Σ": "S", "ς": "s",
    "τ": "t", "Τ": "T",
    "υ": "y", "Υ": "Y",
    "φ": "f", "Φ": "F",
    "χ": "ch", "Χ": "Ch",
    "ψ": "ps", "Ψ": "Ps",
    "ω": "o", "Ω": "O",

    # Cyrillic letters
    "а": "a", "А": "A",
    "б": "b", "Б": "B",
    "в": "v", "В": "V",
    "г": "g", "Г": "G",
    "д": "d", "Д": "D",
    "е": "e", "Е": "E",
    "ё": "e", "Ё": "E",
    "ж": "zh", "Ж": "Zh",
    "з": "z", "З": "Z",
    "и": "i", "И": "I",
    "й": "i", "Й": "I",
    "к": "k", "К": "K",
    "л": "l", "Л": "L",
    "м": "m", "М": "M",
    "н": "n", "Н": "N",
    "о": "o", "О": "O",
    "п": "p", "П": "P",
    "р": "r", "Р": "R",
    "с": "s", "С": "S",
    "т": "t", "Т": "T",
    "у": "u", "У": "U",
    "ф": "f", "Ф": "F",
    "х": "h", "Х": "H",
    "ц": "ts", "Ц": "Ts",
    "ч": "ch", "Ч": "Ch",
    "ш": "sh", "Ш": "Sh",
    "щ": "shch", "Щ": "Shch",
    "ъ": "", "Ъ": "",
    "ы": "y", "Ы": "Y",
    "ь": "", "Ь": "",
    "э": "e", "Э": "E",
    "ю": "yu", "Ю": "Yu",
    "я": "ya", "Я": "Ya",
    
    # Possible Broken Symbols
    "²": "_",
    "―": "_",
    "‘": "_",
    "“": "_",
    "”": "_",
    "‼": "_",
    "↔": "_",
    "①": "1",
    "②": "2",
    "③": "3",
    "④": "4",
    "⑤": "5",
    "█": "_",
    "□": "_",
    "△": "_",
    "▶": "_",
    "❤": "_",
    "〇": "_",
    "「": "_",
    "」": "_",
    "【": "_",
    "】": "_",
    "〜": "_",
    "３": "_",
    "？": "_",
    "｜": "_",
    "～": "_",
    "💅": "_"
}