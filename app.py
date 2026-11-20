"""Rilla: name the meme somebody describes, and link to its picture.

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


class App:
    def __init__(self) -> None:
        self._manifest = validate_manifest(load_manifest_file(MANIFEST_PATH))

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def handle(self, call: Call) -> Result:
        if call.outcome_id != "meme.find":
            raise AgentAppError(f"this app cannot do {call.outcome_id!r}")

        description = (call.inputs.get("description") or "").strip()
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
