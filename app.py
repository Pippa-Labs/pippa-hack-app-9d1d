"""Rilla: find the meme somebody describes, or make one with their words.

Two outcomes:

* `meme.find`: the meme's name and a link to its blank picture.
* `meme.make`: a link to the meme with the person's text on it, drawn by
  memegen.link. The app builds that link and fetches nothing itself: the
  picture is drawn when somebody opens it. Memes memegen has a template for
  (`memegen` in memes.json) get their text in the right panels; the rest get
  classic top and bottom text over the Imgflip picture.

The catalogue is `memes.json`, beside this file, so a call never touches the
network: each entry has the meme's name, its Imgflip template id and picture,
what it shows, what it is used for, and extra words people use for it.

Matching is deliberately simple word overlap, weighted towards the meme's own
name, then its keywords, then its description. Words are lower-cased and
lightly stemmed, so "burning", "burns" and "burn" all meet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote, urlencode

from pippa_agentapp_sdk import (
    AgentAppError,
    Call,
    Manifest,
    Result,
    load_manifest_file,
    validate_manifest,
)

HERE = Path(__file__).parent
MANIFEST_PATH = HERE / "manifest.yaml"
MEMES = json.loads((HERE / "memes.json").read_text("utf-8"))

CAPTION_URL = "https://imgflip.com/memegenerator/{id}"
MEMEGEN = "https://api.memegen.link/images/"

#: What separates one panel's text from the next in the `text` input.
LINE_BREAK = re.compile(r"\s*(?:/|\|)\s*")

#: Memegen's escapes for characters that mean something in a URL path.
#: `_` and `-` mean a space, so real ones are doubled. Order matters: the
#: doubling has to happen before spaces become `_`.
_ESCAPES = [
    ("_", "__"), ("-", "--"), (" ", "_"), ("?", "~q"), ("&", "~a"),
    ("%", "~p"), ("#", "~h"), ("/", "~s"), ("\\", "~b"), ("<", "~l"),
    (">", "~g"), ('"', "''"),
]

#: Longest caption line worth drawing.
MAX_LINE = 120

#: Words that say nothing about which meme is meant.
STOPWORDS = frozenset("""
a an the my me i im is are was were be been do does did have has had of on in
at to for with and or but if this that these those it its so just please can
could would what whats which where who one there their they them he she his
her him you your meme memes called name named link send show find picture pic
image template gif about like looks looking from into out up some any thing
""".split())

#: Below this score a match is a guess, and the app says so instead.
MIN_SCORE = 2

#: How many runners-up to name.
OTHERS = 3

_WORD = re.compile(r"[a-z0-9]+")


class NoDescription(AgentAppError):
    user_message = "Describe the meme you mean, for example \"dog in a burning room\"."
    retryable = False


class NoCaption(AgentAppError):
    user_message = "Tell me the words to put on the meme."
    retryable = False


class NoMatchingMeme(AgentAppError):
    user_message = "I do not know a meme that matches that description."
    retryable = False


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def words(text: str) -> set[str]:
    """The meaningful, stemmed words in `text`."""
    text = text.lower().replace("'", "")
    return {_stem(w) for w in _WORD.findall(text) if w not in STOPWORDS}


def _index(meme: dict) -> tuple[set[str], set[str], set[str]]:
    return (
        words(meme["name"]),
        words(" ".join(meme["keywords"])),
        words(meme["shows"] + " " + meme["use"]),
    )


INDEX = [(meme, _index(meme)) for meme in MEMES]


def score(query: set[str], index: tuple[set[str], set[str], set[str]]) -> int:
    name, keywords, description = index
    total = 0
    for word in query:
        if word in name:
            total += 3
        elif word in keywords:
            total += 2
        elif word in description:
            total += 1
    return total


def rank(description: str) -> list[tuple[int, dict]]:
    """Every meme that matches at all, best first."""
    query = words(description)
    scored = [(score(query, index), meme) for meme, index in INDEX]
    scored = [pair for pair in scored if pair[0] > 0]
    # Stable sort, so equal scores keep catalogue order (most popular first).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def encode_line(line: str) -> str:
    """One caption line, as memegen wants it in the URL path."""
    line = " ".join(line.split())[:MAX_LINE]
    if not line:
        return "_"   # memegen's empty line
    for raw, escaped in _ESCAPES:
        line = line.replace(raw, escaped)
    return quote(line, safe="_~'")


def split_lines(text: str, slots: int) -> list[str]:
    """The person's text, one entry per panel, exactly `slots` long.

    Fewer lines than panels leaves the rest blank. More lines than panels
    puts the overflow on the last panel rather than dropping their words.
    """
    lines = [part for part in LINE_BREAK.split(text.strip())]
    if len(lines) > slots:
        lines = lines[: slots - 1] + [" ".join(lines[slots - 1:])]
    return lines + [""] * (slots - len(lines))


def caption_url(meme: dict, lines: list[str]) -> str:
    path = "/".join(encode_line(line) for line in lines)
    if meme["memegen"]:
        return f"{MEMEGEN}{meme['memegen']}/{path}.png"
    query = urlencode({"background": meme["image_url"]})
    return f"{MEMEGEN}custom/{path}.png?{query}"


class App:
    def __init__(self) -> None:
        self._manifest = validate_manifest(load_manifest_file(MANIFEST_PATH))

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def handle(self, call: Call) -> Result:
        if call.outcome_id == "meme.find":
            return self._find(call)
        if call.outcome_id == "meme.make":
            return self._make(call)
        raise AgentAppError(f"this app cannot do {call.outcome_id!r}")

    @staticmethod
    def _match(description: str) -> list[tuple[int, dict]]:
        """Ranked matches, or a refusal that says what to type."""
        if not words(description):
            raise NoDescription(f"no usable description: {description!r}")
        ranked = rank(description)
        if not ranked or ranked[0][0] < MIN_SCORE:
            error = NoMatchingMeme(f"no meme scored {MIN_SCORE}+ for {description!r}")
            error.user_message = (
                f"I do not know a meme that matches \"{description}\". "
                "Try describing what is in the picture."
            )
            raise error
        return ranked

    def _make(self, call: Call) -> Result:
        description = (call.inputs.get("meme") or "").strip()
        text = (call.inputs.get("text") or "").strip()
        if not words(description):
            error = NoDescription(f"no meme named: {description!r}")
            error.user_message = (
                "Say which meme to use, for example \"drake\" or "
                "\"dog in a burning room\"."
            )
            raise error
        if not text.replace("/", "").replace("|", "").strip():
            raise NoCaption("no caption text")

        meme = self._match(description)[0][1]
        lines = split_lines(text, meme["lines"])
        url = caption_url(meme, lines)
        used = sum(1 for line in lines if line.strip())
        return Result(
            data={
                "name": meme["name"],
                "image_url": url,
                "panels": meme["lines"],
                "panels_filled": used,
            },
            narrative=f"Here is your \"{meme['name']}\" meme: {url}",
        )

    def _find(self, call: Call) -> Result:
        description = (call.inputs.get("description") or "").strip()
        ranked = self._match(description)
        best = ranked[0][1]
        others = [
            meme["name"] for points, meme in ranked[1 : OTHERS + 1]
            if points >= MIN_SCORE
        ]
        return Result(
            data={
                "name": best["name"],
                "image_url": best["image_url"],
                "caption_url": CAPTION_URL.format(id=best["id"]),
                "shows": best["shows"],
                "used_for": best["use"],
                "other_matches": others,
            },
            narrative=f"That is the \"{best['name']}\" meme: {best['image_url']}",
        )
