"""A leading [gesture: NAME] tag starts a robot gesture from the same model reply."""

import pytest

pytest.importorskip("livekit.agents")
from livekit.agents import llm

from forge_conversationalist.gesture_tags import (
    GestureTagFilter,
    filter_gesture_stream,
    start_gesture,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def feed_all(pieces):
    tags = GestureTagFilter()
    spoken = "".join(tags.feed(piece) for piece in pieces) + tags.flush()
    return tags.started, spoken


@pytest.mark.parametrize("pieces,gesture,spoken", [
    (["[gesture: yes] Sure thing."], "yes", "Sure thing."),
    (["[gesture:surprised]Oh! ", "Really?"], "surprised", "Oh! Really?"),
    (["[ges", "ture: no", "] Like this."], "no", "Like this."),
    (["[", "gesture", ":", " curious", "]", " Hmm."], "curious", "Hmm."),
    (["  [GESTURE: Yes]\nOkay."], "yes", "Okay."),
    (["No tag here."], None, "No tag here."),
    (["Hello [gesture: yes] there."], None, "Hello there."),  # mid-reply: removed, ignored
    (["Hi. ", "[gesture: no]", " Bye."], None, "Hi. Bye."),
    (["See [1] and [note] here."], None, "See [1] and [note] here."),
    (["[unclosed bracket that never ends and is far too long to be a tag at all"], None,
     "[unclosed bracket that never ends and is far too long to be a tag at all"),
    (["[gesture: yes"], None, "[gesture: yes"),  # reply ended inside a tag: spoken, not acted on
])
def test_the_tag_is_removed_from_speech_and_only_a_leading_tag_acts(pieces, gesture, spoken):
    assert feed_all(pieces) == (gesture, spoken)


class FakeEnsemble:
    def __init__(self, performers, outcome="accepted"):
        self.performers = performers
        self.outcome = outcome
        self.calls = []

    async def status(self):
        return {"performers": self.performers}

    async def express(self, name, performer_id):
        self.calls.append((name, performer_id))
        return {"outcome": self.outcome, "reason": "x"}


ARM = {"status": "idle", "expressions": ["yes", "no", "surprised"],
       "expression_durations_s": {"yes": 3.0, "no": 2.0, "surprised": 4.6}}


async def test_the_gesture_goes_to_every_robot_that_offers_it_and_the_voice_waits_half_way():
    ensemble = FakeEnsemble({"arm": ARM, "head": {"status": "idle", "expressions": ["yes"]},
                             "gone": {"status": "blocked", "expressions": ["yes"]}})
    delay = await start_gesture(ensemble, "yes")
    assert ensemble.calls == [("yes", "arm"), ("yes", "head")]
    assert delay == pytest.approx(1.5)  # half of the longest (3.0 s); unknown duration -> 2.0 s
    assert await start_gesture(FakeEnsemble({"arm": ARM}), "surprised") == pytest.approx(2.3)


async def test_a_gesture_nobody_offers_or_accepts_does_not_delay_the_voice():
    assert await start_gesture(FakeEnsemble({"arm": ARM}), "dance") == 0.0
    assert await start_gesture(FakeEnsemble({"arm": ARM}, outcome="denied"), "yes") == 0.0

    class Broken:
        async def status(self):
            raise OSError("link down")

    assert await start_gesture(Broken(), "yes") == 0.0


def chunks(*texts):
    return [llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=t)) for t in texts]


async def collect(stream, ensemble, slept):
    async def fake_sleep(seconds):
        slept.append(seconds)

    async def source():
        for item in stream:
            yield item

    out = []
    async for item in filter_gesture_stream(source(), ensemble, sleep=fake_sleep):
        out.append(item)
    return out


async def test_the_stream_is_cleaned_the_gesture_starts_first_and_the_voice_waits():
    ensemble = FakeEnsemble({"arm": ARM})
    slept = []
    out = await collect(chunks("[gesture: ", "surprised] Oh! ", "I did not expect that."),
                        ensemble, slept)
    assert "".join(c.delta.content for c in out) == "Oh! I did not expect that."
    assert ensemble.calls == [("surprised", "arm")]
    assert slept == [pytest.approx(2.3)]


async def test_replies_without_a_tag_and_non_text_chunks_pass_through_untouched():
    ensemble = FakeEnsemble({"arm": ARM})
    tool_chunk = llm.ChatChunk(id="t", delta=llm.ChoiceDelta(role="assistant", tool_calls=[
        llm.FunctionToolCall(arguments="{}", name="check", call_id="1")]))
    slept = []
    out = await collect([*chunks("Just talking."), tool_chunk, "plain string"], ensemble, slept)
    assert out[1] is tool_chunk and out[2] == "plain string"
    assert out[0].delta.content == "Just talking."
    assert ensemble.calls == [] and slept == []


async def test_only_one_gesture_per_reply():
    ensemble = FakeEnsemble({"arm": ARM})
    out = await collect(chunks("[gesture: yes] Yes. ", "[gesture: no] And no."), ensemble, [])
    assert "".join(c.delta.content for c in out) == "Yes. And no."
    assert ensemble.calls == [("yes", "arm")]


def test_the_instructions_teach_the_tag_and_keep_the_tool():
    from forge_conversationalist.agent import _room_instructions

    integrations = type("Integrations", (), {"ensemble": object()})()
    text = _room_instructions(integrations, caller_id="p", session_id="r")
    assert "[gesture: yes]" in text and "very first thing in the reply" in text
    assert "request_ensemble_gesture" in text and "never mention or explain it" in text


async def test_the_agent_subclass_filters_the_models_stream():
    from forge_conversationalist.gesture_tags import agent_class_with_gesture_tags

    class Default:
        @staticmethod
        async def llm_node(agent, chat_ctx, tools, model_settings):
            for text in ("[gesture: no] Like ", "this."):
                yield llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=text))

    class Base:
        default = Default

    ensemble = FakeEnsemble({"arm": ARM})
    agent = agent_class_with_gesture_tags(Base, ensemble)()
    spoken = ""
    async for chunk in agent.llm_node(None, [], None):
        spoken += chunk.delta.content
    assert spoken == "Like this." and ensemble.calls == [("no", "arm")]
