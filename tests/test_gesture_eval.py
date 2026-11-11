"""The gesture-tag evaluation script's scoring, offline."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_gesture_tags.py"
spec = importlib.util.spec_from_file_location("eval_gesture_tags", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def result(utterance, tag, first=0.4, decided=0.5):
    return {"utterance": utterance, "tag": tag, "first": first, "decided": decided, "total": 1.0}


def test_cases_are_labelled_with_offered_gestures_and_cover_each_one():
    labels = set().union(*(a for _, a in evaluation.CASES)) - {None}
    assert {"yes", "no", "surprised", "curious"} <= labels
    assert len({u for u, _ in evaluation.CASES}) == len(evaluation.CASES)


def test_each_robot_is_tested_only_on_the_gestures_it_offers():
    for robot, gestures in evaluation.ROBOTS.items():
        offered = set(gestures.split(","))
        cases = evaluation.select_cases(gestures.split(","))
        assert cases, robot
        for utterance, allowed in cases:
            assert allowed - {None} <= offered, (robot, utterance)
    soarm = {u for u, _ in evaluation.select_cases(evaluation.ROBOTS["soarm101"].split(","))}
    reachy = {u for u, _ in evaluation.select_cases(evaluation.ROBOTS["reachy"].split(","))}
    assert "My dog died this morning." in reachy - soarm


def test_scoring_counts_hits_misses_wrong_and_spurious_tags():
    cases = dict(evaluation.CASES)
    yes_q, none_q = "Are you a robot?", "What is two plus two?"
    assert cases[yes_q] == {"yes"} and cases[none_q] == {None}
    summary = evaluation.summarize("m", [
        result(yes_q, "yes"), result(yes_q, None), result(yes_q, "no"),
        result(none_q, None), result(none_q, "curious"),
        {"error": "HTTP 500", "utterance": yes_q},
    ], evaluation.CASES)
    assert summary["errors"] == 1 and summary["missed"] == 1
    assert summary["wrong"] == 1 and summary["spurious"] == 1
    assert summary["right"] == 2 / 5
    assert len(summary["failures"]) == 3


