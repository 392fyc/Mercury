"""Retry loop with structured feedback into next attempt's prompt.

Per ADR §6.4: max_retries = 3 (hard cap). Each subsequent attempt
forwards the previous attempt's `verify.fail_reasons` into the bible's
prompt composition as a `feedback` block — the model conditions on what
went wrong rather than re-rolling blind.

Generation failures (adapter rc != 0) short-circuit the verify call and
also feed structured `generation_errors` text into the next prompt, so
network / API errors propagate forward as feedback as well.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .character_bible import CharacterBible
from .pipeline import (
    FrameResult,
    FrameSpec,
    GenerationOptions,
    generate_frames,
)
from .verify import VerifyConfig, VerifyResult, verify_frames


@dataclass
class RetryAttempt:
    attempt: int
    passed: bool
    frame_results: list[FrameResult]
    verify: VerifyResult
    feedback_used: str = ""


@dataclass
class RetryReport:
    passed: bool
    attempts: list[RetryAttempt] = field(default_factory=list)
    total_attempts: int = 0
    final_fail_reasons: list[str] = field(default_factory=list)


def _build_feedback(prev: RetryAttempt | None) -> str:
    if prev is None:
        return ""
    parts: list[str] = []
    gen_errors = [
        f"frame {fr.spec.index}: {fr.stderr.strip().splitlines()[-1] if fr.stderr.strip() else 'unknown error'}"
        for fr in prev.frame_results
        if not fr.success
    ]
    if gen_errors:
        parts.append("Previous generation errors: " + "; ".join(gen_errors))
    if prev.verify.fail_reasons:
        parts.append(
            "Previous verify failures: "
            + "; ".join(prev.verify.fail_reasons[:3])
        )
    return " ".join(parts)


def run_with_retry(bible: CharacterBible, frames: list[FrameSpec],
                   verify_cfg: VerifyConfig,
                   opts: GenerationOptions | None = None,
                   max_retries: int = 3) -> RetryReport:
    opts = opts or GenerationOptions()
    attempts: list[RetryAttempt] = []
    prev: RetryAttempt | None = None

    for attempt_num in range(1, max(1, max_retries) + 1):
        feedback = _build_feedback(prev)
        results = generate_frames(bible, frames, opts=opts, feedback=feedback)
        good_paths: list[Path] = [r.out_path for r in results if r.success and r.out_path]
        verdict = verify_frames(good_paths, verify_cfg)
        all_generated = all(r.success for r in results)
        passed = verdict.passed and all_generated
        cur = RetryAttempt(
            attempt=attempt_num,
            passed=passed,
            frame_results=results,
            verify=verdict,
            feedback_used=feedback,
        )
        attempts.append(cur)
        if passed:
            return RetryReport(passed=True, attempts=attempts,
                               total_attempts=attempt_num)
        prev = cur

    last = attempts[-1] if attempts else None
    fail_reasons = list(last.verify.fail_reasons) if last else ["no attempts"]
    if last and any(not r.success for r in last.frame_results):
        miss = [r.spec.index for r in last.frame_results if not r.success]
        fail_reasons.append(f"generation failed for frames {miss}")
    return RetryReport(
        passed=False,
        attempts=attempts,
        total_attempts=len(attempts),
        final_fail_reasons=fail_reasons,
    )
