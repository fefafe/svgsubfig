from typing import Final

# =============================================================================

# size of sub- and superscripts relative to the text they are attached to, and the
# rise of their baseline in [em] of that text, as TeX sets them in text style
SCRIPT_SIZE: Final[float] = 0.7
SUPERSCRIPT_RISE: Final[float] = 0.363
SUBSCRIPT_RISE: Final[float] = -0.15

# a run of text: characters, rise of their baseline [em] and relative size
Run = tuple[str, float, float]


def parse_scripts(text: str) -> list[Run]:
    """Splits a text into runs, setting sub- and superscripts in TeX notation.

    ``_{...}`` sets a subscript and ``^{...}`` a superscript, which apply to the next
    character alone if it is not a group in braces. Groups may nest, e.g.
    ``e^{x_{i}}``, and ``\\_``, ``\\^``, ``\\{`` and ``\\}`` stand for the literal
    characters. Rise and size of each run are relative to the unscripted text.
    """

    runs: list[Run] = []

    def append(chars: str, rise: float, size: float):
        if runs and runs[-1][1:] == (rise, size):
            runs[-1] = (runs[-1][0] + chars, rise, size)
        else:
            runs.append((chars, rise, size))

    def parse(i: int, rise: float, size: float) -> int:
        """Parses a group from index ``i`` on, returns the index past its end."""

        while i < len(text):
            char = text[i]
            i += 1

            if char == "}":
                return i

            if char == "{":
                i = parse(i, rise, size)

            elif char == "\\" and i < len(text) and text[i] in "_^{}":
                append(text[i], rise, size)
                i += 1

            elif char in "_^" and i < len(text):
                shift = SUPERSCRIPT_RISE if char == "^" else SUBSCRIPT_RISE
                rise_script = rise + shift * size
                size_script = size * SCRIPT_SIZE

                if text[i] == "{":
                    i = parse(i + 1, rise_script, size_script)
                else:
                    append(text[i], rise_script, size_script)
                    i += 1

            else:
                append(char, rise, size)

        return i

    # a stray closing brace ends the parse early, so resume past it
    i = 0

    while i < len(text):
        i = parse(i, 0.0, 1.0)

    return runs
