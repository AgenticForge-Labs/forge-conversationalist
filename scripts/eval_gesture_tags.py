#!/usr/bin/env python3
"""Does a model start its reply with the right [gesture: NAME] tag, and how fast?

Builds the same instructions the voice agent uses (from .env.local and a gesture list),
sends labelled utterances to one or more OpenRouter models, and reports for each model the
hit rate (right gesture, or no tag where none fits) and the time to the first token and
to the end of the tag. It spends a few cents of the OPENROUTER_API_KEY in .env.local.

    python scripts/eval_gesture_tags.py MODEL [MODEL ...] [--gestures yes,no,...] [--repeat 1]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: (utterance, acceptable gestures); None means "no tag" is right (or acceptable).
CASES: list[tuple[str, set[str | None]]] = [
    ("Are you a robot?", {"yes"}),
    ("Do you have an arm?", {"yes"}),
    ("Can you hear me?", {"yes"}),
    ("Is Paris the capital of France?", {"yes"}),
    ("Is your name Bart?", {"no"}),
    ("Can you cook dinner?", {"no"}),
    ("Is the sky green?", {"no"}),
    ("Do you have legs?", {"no"}),
    ("Are you human?", {"no"}),
    ("Is the moon made of cheese?", {"no"}),
    ("I just won the lottery!", {"surprised"}),
    ("Guess what, I'm having twins!", {"surprised"}),
    ("The whole company server just went down.", {"surprised"}),
    ("You will not believe it, I got the job!", {"surprised"}),
    ("Tell me a joke about noodles.", {"curious"}),
    ("What do you think clouds taste like?", {"curious"}),
    ("If you could be any animal, which would you be?", {"curious"}),
    ("What does the thing do with the other thing?", {"curious", None}),
    ("What is two plus two?", {None}),
    ("How tall is Mount Everest?", {None}),
    ("Summarize Romeo and Juliet in one sentence.", {None}),
    ("Explain how a robot arm works in one sentence.", {None}),
    ("Hello there.", {None}),
    ("Thanks, that is all.", {None, "yes"}),
    ("Say yes.", {"yes"}),
    ("Show me no.", {"no"}),
    ("Look surprised.", {"surprised"}),
    ("Act curious.", {"curious"}),
    ("Nod your head.", {"yes"}),
    ("Shake your head.", {"no"}),
]


def build_instructions(gestures: list[str]) -> str:
    from dotenv import dotenv_values

    from forge_conversationalist.agent import _robot_roster, _room_instructions, _self_description
    from forge_conversationalist.config import load_profile
    from forge_conversationalist.integrations import load_integrations
    from forge_conversationalist.personality import load_personality

    env = {k: str(v) for k, v in dotenv_values(ROOT / ".env.local").items() if v is not None}
    integrations = load_integrations(env)
    roster = _robot_roster({"performers": {"soarm101": {"status": "idle", "expressions": gestures}}})
    return _room_instructions(
        integrations, caller_id="eval", session_id="eval", personality=load_personality(env),
        robot_roster=roster,
        self_description=_self_description(load_profile(env), integrations.ensemble.performer_ids),
    )


async def one(client, key: str, model: str, system: str, utterance: str) -> dict:
    from forge_conversationalist.gesture_tags import GestureTagFilter

    body = {"model": model, "stream": True, "max_tokens": 90, "temperature": 0.5,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": utterance}]}
    started = time.monotonic()
    first = decided = None
    text = ""
    tags = GestureTagFilter()
    spoken = ""
    try:
        async with client.stream("POST", "https://openrouter.ai/api/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {key}"}, json=body) as response:
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}", "utterance": utterance}
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                try:
                    delta = json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
                except (KeyError, IndexError, ValueError):
                    continue
                if not delta:
                    continue
                now = time.monotonic() - started
                first = first if first is not None else now
                text += delta
                spoken += tags.feed(delta)
                if decided is None and (tags.started is not None or spoken.strip()):
                    decided = now  # the tag is complete, or speech began without one
    except Exception as exc:  # noqa: BLE001 - a flaky call is data, not a crash
        return {"error": type(exc).__name__, "utterance": utterance}
    return {"utterance": utterance, "tag": tags.started, "first": first, "decided": decided,
            "total": time.monotonic() - started, "reply": text[:70]}


async def evaluate(model: str, system: str, key: str, repeat: int, concurrency: int) -> list[dict]:
    import httpx

    gate = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=60) as client:
        async def run(utterance):
            async with gate:
                return await one(client, key, model, system, utterance)
        return await asyncio.gather(*(run(u) for u, _ in CASES for _ in range(repeat)))


def summarize(model: str, results: list[dict]) -> dict:
    allowed = {u: a for u, a in CASES}
    ok = [r for r in results if "error" not in r]
    right = sum(r["tag"] in allowed[r["utterance"]] for r in ok)
    spurious = sum(r["tag"] is not None and None in allowed[r["utterance"]] and r["tag"] not in allowed[r["utterance"]]
                   for r in ok)
    missed = sum(r["tag"] is None and None not in allowed[r["utterance"]] for r in ok)
    wrong = len(ok) - right - spurious - missed

    def q(values, p):
        values = sorted(v for v in values if v is not None)
        return values[min(len(values) - 1, int(len(values) * p))] if values else float("nan")

    return {"model": model, "n": len(results), "errors": len(results) - len(ok),
            "right": right / max(1, len(ok)), "missed": missed, "wrong": wrong, "spurious": spurious,
            "first_med": statistics.median([r["first"] for r in ok if r["first"] is not None] or [float("nan")]),
            "decided_med": statistics.median([r["decided"] for r in ok if r["decided"] is not None] or [float("nan")]),
            "decided_p90": q([r["decided"] for r in ok], 0.9),
            "failures": [(r["utterance"], r["tag"]) for r in ok if r["tag"] not in allowed[r["utterance"]]]}


def main(argv: list[str] | None = None) -> int:
    from dotenv import dotenv_values

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="+")
    parser.add_argument("--gestures", default="curious,neutral,no,sleep2,surprised,yes")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--show-failures", action="store_true")
    args = parser.parse_args(argv)
    key = str(dotenv_values(ROOT / ".env.local").get("OPENROUTER_API_KEY", ""))
    if not key:
        print("OPENROUTER_API_KEY is not set in .env.local", file=sys.stderr)
        return 2
    system = build_instructions(args.gestures.split(","))
    print(f"instructions: {len(system)} chars; {len(CASES)} cases x {args.repeat}\n")
    print(f"{'model':46} {'hit':>5} {'miss':>4} {'wrong':>5} {'spur':>4} {'err':>3}  first  tag  tag-p90")
    for model in args.models:
        results = asyncio.run(evaluate(model, system, key, args.repeat, args.concurrency))
        s = summarize(model, results)
        print(f"{model:46} {s['right']:5.0%} {s['missed']:4} {s['wrong']:5} {s['spurious']:4} {s['errors']:3}"
              f"  {s['first_med']:5.2f} {s['decided_med']:5.2f} {s['decided_p90']:6.2f}", flush=True)
        if args.show_failures:
            for utterance, tag in s["failures"]:
                print(f"      {utterance!r} -> {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
