"""Tests for Rilla. The build runs these and will not publish if they fail.

Every call goes through `LocalRunner`, the same code the production runtime
uses, so a result that does not match manifest.yaml fails here first.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pippa_agentapp_sdk import LocalRunner, OutputShapeViolation

from app import MEMES, App, NoDescription, NoMatchingMeme

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def call(inputs: dict, *, explicit: bool = False):
    return LocalRunner(App()).call(
        "meme.find", inputs, granted_secrets={}, now=NOW, request_id="test-1",
        explicit=explicit,
    ).result


def test_the_manifest_is_valid():
    app = App()
    assert app.manifest.app_id.startswith("hack.")
    assert [o.id for o in app.manifest.outcomes] == ["meme.find"]


def test_the_catalogue_is_well_formed():
    ids = [meme["id"] for meme in MEMES]
    assert len(ids) == len(set(ids))
    for meme in MEMES:
        assert meme["image_url"].startswith("https://i.imgflip.com/")
        assert meme["name"] and meme["shows"] and meme["use"] and meme["keywords"]


@pytest.mark.parametrize("description, expected", [
    ("guy looking back at another girl", "Distracted Boyfriend"),
    ("dog in a burning room", "This Is Fine"),
    ("drake", "Drake Hotline Bling"),
    ("woman yelling at a cat", "Woman Yelling At Cat"),
    ("sweating over two buttons", "Two Buttons"),
    ("astronaut with a gun", "Always Has Been"),
    ("kid smiling in front of a burning house", "Disaster Girl"),
    ("skeleton waiting on a bench", "Waiting Skeleton"),
    ("pikachu shocked face", "Surprised Pikachu"),
    ("man thrown out of the window in a meeting", "Boardroom Meeting Suggestion"),
    ("old man hiding pain", "Hide the Pain Harold"),
    ("a car swerving off the motorway", "Left Exit 12 Off Ramp"),
])
def test_it_finds_the_meme_described(description, expected):
    assert call({"description": description}).data["name"] == expected


def test_it_returns_links_and_runners_up():
    data = call({"description": "guy looking back at another girl"}).data
    assert data["image_url"] == "https://i.imgflip.com/1ur9b0.jpg"
    assert data["caption_url"] == "https://imgflip.com/memegenerator/112126428"
    assert data["shows"] and data["used_for"]
    assert "Distracted Boyfriend" not in data["other_matches"]
    assert len(data["other_matches"]) <= 3


def test_no_description_is_refused_with_a_hint():
    with pytest.raises(NoDescription) as failure:
        call({"description": ""})
    assert "Describe the meme" in failure.value.user_message


def test_a_missing_description_is_refused_too():
    with pytest.raises(NoDescription):
        call({})


def test_an_unknown_meme_says_so_rather_than_guessing():
    with pytest.raises(NoMatchingMeme) as failure:
        call({"description": "bananas in pyjamas"})
    assert "bananas in pyjamas" in failure.value.user_message


def test_picked_by_name_it_answers_and_refuses_as_usual():
    picked = call({"description": "dog in a burning room"}, explicit=True)
    assert picked.data == call({"description": "dog in a burning room"}).data
    with pytest.raises(NoDescription):
        call({"description": ""}, explicit=True)


def test_the_runner_enforces_the_manifest(monkeypatch):
    from pippa_agentapp_sdk import Result

    monkeypatch.setattr(App, "handle", lambda self, c: Result(data={"surprise": 1}))
    with pytest.raises(OutputShapeViolation):
        call({"description": "drake"})
