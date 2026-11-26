"""Nudging a turn that keeps working without progress (aida.core.loop_detection).

What matters: a turn that repeats itself is told so, on evidence only; a turn
that is making progress (an edited script rerun, different scripts, a value
being polled that changes) is left alone; every call stays paired with its
result; and the nudges per turn are capped.
"""

from __future__ import annotations

import pytest

from aida.config.settings import AppConfig
from aida.core.agent import AgentLoop
from aida.core.events import TurnNudged
from aida.core.loop_detection import (
    MAX_LOOP_NUDGES,
    ProgressMonitor,
    error_signature,
    normalize_result,
)
from aida.core.tools import NativeTool, ToolResult
from aida.providers.base import CompletionSettings, Message, ToolSchema
from aida.providers.mock import MockProvider, MockToolCall, MockTurn

FIT_SCRIPT = "\n".join(
    [
        "import numpy as np",
        "from scipy.optimize import curve_fit",
        "q, I = np.loadtxt('data.dat', unpack=True)",
        "def model(q, a, rg):",
        "    return a * np.exp(-(q * rg) ** 2 / 3)",
        "popt, _ = curve_fit(model, q, I, p0=[1.0, 50.0])",
        "print('Rg =', popt[1])",
        "np.savetxt('fit.dat', model(q, *popt))",
        "print('done')",
        "",
    ]
)


def _write(path: str, content: str = FIT_SCRIPT) -> tuple[str, dict, str, bool]:
    return "write_file", {"path": path, "content": content}, f"Wrote {path}", False


# --- ProgressMonitor --------------------------------------------------------


def test_same_call_with_same_result_fires_at_the_threshold_and_its_multiples():
    monitor = ProgressMonitor(threshold=3)
    signs = [monitor.observe("read_file", {"path": "a.txt"}, "abc", False) for _ in range(6)]
    assert [s.kind if s else None for s in signs] == [
        None,
        None,
        "repeated_call",
        None,
        None,
        "repeated_call",
    ]
    assert "read_file 3 times" in signs[2].message


def test_a_rerun_whose_output_changed_is_progress():
    monitor = ProgressMonitor(threshold=3)
    for chi2 in ("1.9", "1.4", "1.1", "1.05"):
        assert monitor.observe("run_python_script", {"path": "fit.py"}, chi2, False) is None


def test_durations_timestamps_and_ids_do_not_make_a_result_new():
    monitor = ProgressMonitor(threshold=3)
    results = [
        "exit code: 0\nduration: 1.23s\nstdout:\nRg = 51\n2026-11-26T10:00:01 saved 3fa9c2d1e0",
        "exit code: 0\nduration: 1.31s\nstdout:\nRg = 51\n2026-11-26T10:02:44 saved 77b0a1c9f2",
        "exit code: 0\nduration: 0.98s\nstdout:\nRg = 51\n2026-11-26T10:05:09 saved 0c1d2e3f4a",
    ]
    signs = [monitor.observe("run_python_script", {"path": "fit.py"}, r, False) for r in results]
    assert signs[-1] is not None and signs[-1].kind == "repeated_call"
    assert normalize_result("Rg = 51") == "Rg = 51"  # data values are kept


def test_the_same_error_from_different_calls_fires_and_quotes_it():
    monitor = ProgressMonitor(threshold=3)
    signs = [
        monitor.observe(
            "run_python_script",
            {"path": f"fit_{i}.py"},
            f'exit code: 1\nstderr:\n  File "fit_{i}.py", line {10 + i}\n'
            "ModuleNotFoundError: No module named 'lmfit'",
            True,
        )
        for i in range(3)
    ]
    assert signs[-1] is not None and signs[-1].kind == "repeated_error"
    assert "No module named 'lmfit'" in signs[-1].message


def test_error_signature_masks_numbers_but_keeps_the_message():
    a = error_signature("Traceback...\nValueError: shapes (100,) and (99,) not aligned")
    b = error_signature("Traceback...\nValueError: shapes (120,) and (7,) not aligned")
    assert a == b and a.startswith("ValueError: shapes")
    assert error_signature("   \n") == ""


def test_near_copy_scripts_count_as_churn_and_distinct_scripts_do_not():
    monitor = ProgressMonitor(threshold=3)
    variants = [
        FIT_SCRIPT,
        FIT_SCRIPT.replace("p0=[1.0, 50.0]", "p0=[2.0, 40.0]"),
        FIT_SCRIPT.replace("print('done')", "print('finished')"),
        FIT_SCRIPT + "print(popt)\n",
    ]
    names = ["fit.py", "fit_v2.py", "fit_fixed.py", "fit_final.py"]
    signs = [monitor.observe(*_write(n, v)) for n, v in zip(names, variants, strict=True)]
    assert [s.kind if s else None for s in signs] == [None, None, None, "script_churn"]
    assert "fit_v2.py, fit_fixed.py, fit_final.py" in signs[-1].message
    assert "edit_file" in signs[-1].message

    distinct = ProgressMonitor(threshold=2)
    for i in range(5):
        body = "\n".join(f"step_{i}_{j} = compute_{i}({j})" for j in range(12))
        assert distinct.observe(*_write(f"part_{i}.py", body)) is None


def test_rewriting_the_same_path_or_a_data_file_is_not_churn():
    monitor = ProgressMonitor(threshold=2)
    for _ in range(4):
        sign = monitor.observe(
            "write_file", {"path": "fit.py", "content": FIT_SCRIPT}, "Wrote fit.py", False
        )
        assert sign is None or sign.kind == "repeated_call"
    data = ProgressMonitor(threshold=2)
    for name in ("a.csv", "b.csv", "c.csv"):
        assert data.observe(*_write(name, "q,I\n0.01,5\n0.02,4\n")) is None


def test_threshold_below_two_is_off():
    for threshold in (0, 1):
        monitor = ProgressMonitor(threshold=threshold)
        assert not monitor.enabled
        assert all(monitor.observe("t", {}, "same", False) is None for _ in range(10))


# --- AgentLoop --------------------------------------------------------------


async def _probe(_args):
    return ToolResult(content="motor at 12.5 mm")


PROBE = NativeTool(
    schema=ToolSchema(name="probe", description="Read a value", parameters={"type": "object"}),
    func=_probe,
)


def _probe_round(call_id: str) -> MockTurn:
    return MockTurn(tool_calls=[MockToolCall(name="probe", id=call_id)])


async def _run(script: list[MockTurn], **loop_kwargs):
    provider = MockProvider(script)
    loop = AgentLoop(provider, CompletionSettings(model="mock"), {"probe": PROBE}, **loop_kwargs)
    messages = [Message(role="user", content="analyse the data")]
    events = [e async for e in loop.run(messages)]
    return events, messages, provider


@pytest.mark.asyncio
async def test_a_repeated_call_is_nudged_after_its_result_and_the_turn_goes_on():
    script = [_probe_round(f"t{i}") for i in range(3)] + [MockTurn(text="Rg is 51 Å.")]
    events, messages, provider = await _run(script)
    nudges = [e for e in events if isinstance(e, TurnNudged)]
    assert [n.reason for n in nudges] == ["loop"]
    assert len(provider.calls) == 4
    # The nudge follows the third call's result, so every call stays paired.
    nudge_at = next(i for i, m in enumerate(messages) if m.content == nudges[0].text)
    assert messages[nudge_at - 1].role == "tool" and messages[nudge_at - 1].tool_call_id == "t2"
    assert messages[-1].content == "Rg is 51 Å."


@pytest.mark.asyncio
async def test_parallel_repeats_in_one_round_give_one_nudge():
    calls = [MockToolCall(name="probe", id=f"p{i}") for i in range(6)]
    events, messages, _ = await _run([MockTurn(tool_calls=calls), MockTurn(text="ok")])
    assert sum(isinstance(e, TurnNudged) for e in events) == 1
    assert [m.role for m in messages[-3:]] == ["tool", "user", "assistant"]


@pytest.mark.asyncio
async def test_loop_nudges_are_capped_and_can_be_switched_off():
    rounds = 3 * (MAX_LOOP_NUDGES + 2)
    script = [_probe_round(f"t{i}") for i in range(rounds)] + [MockTurn(text="done")]
    events, _, _ = await _run(script, max_iterations=rounds + 5)
    assert sum(isinstance(e, TurnNudged) for e in events) == MAX_LOOP_NUDGES

    script = [_probe_round(f"t{i}") for i in range(6)] + [MockTurn(text="done")]
    events, _, _ = await _run(script, loop_nudge_threshold=0)
    assert not any(isinstance(e, TurnNudged) for e in events)


@pytest.mark.asyncio
async def test_counts_start_afresh_each_turn():
    provider = MockProvider(
        [_probe_round("a1"), _probe_round("a2"), MockTurn(text="first")]
        + [_probe_round("b1"), _probe_round("b2"), MockTurn(text="second")]
    )
    loop = AgentLoop(provider, CompletionSettings(model="mock"), {"probe": PROBE})
    messages = [Message(role="user", content="one")]
    first = [e async for e in loop.run(messages)]
    messages.append(Message(role="user", content="two"))
    second = [e async for e in loop.run(messages)]
    assert not any(isinstance(e, TurnNudged) for e in first + second)


# --- config -----------------------------------------------------------------


def test_loop_nudge_threshold_config():
    assert AppConfig().loop_nudge_threshold == 3
    assert AppConfig.from_dict({"loop_nudge_threshold": 5}).loop_nudge_threshold == 5
    assert AppConfig.from_dict({"loop_nudge_threshold": 0}).loop_nudge_threshold == 0
    for bad in (1, -2):
        assert AppConfig.from_dict({"loop_nudge_threshold": bad}).loop_nudge_threshold == 3
    assert AppConfig(loop_nudge_threshold=4).to_dict()["loop_nudge_threshold"] == 4
