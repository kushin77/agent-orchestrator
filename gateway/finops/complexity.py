#!/usr/bin/env python3
"""Task difficulty scorer for the FinOps model chooser (issue #17).

Adapted from the hermes-agents ``services/complexity_scorer.py`` (fast /
standard / deep complexity analysis) and the leaderboard
``lib/complexity-scorer.sh``. Produces a deterministic 0-100 difficulty score
from four weighted signals so the chooser can escalate a task to a capable
tier when it is genuinely hard — never pre-emptively.

The scorer is deliberately quantitative and offline: it has no model-tiers
knowledge and no thresholds embedded. It returns a raw 0-100 score plus the
per-component breakdown; the chooser interprets the score against the
difficulty thresholds declared in ``tiers.yaml`` (escalation.thresholds).

Components (each 0-25, weighted 0.25):

- word count       — longer task descriptions imply harder, multi-part work.
- file count       — a task touching many files has a wider blast radius.
- modification    — affected-symbol count and repository size amplify risk.
  scope
- historical fail  — task classes that have failed often need a stronger tier.
  rate

Standalone module (stdlib only); imports nothing from the rest of the package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class DifficultyScore:
    """Result of a difficulty analysis (0-100)."""

    score: float
    word_count_component: float  # 0-25
    file_count_component: float  # 0-25
    modification_scope_component: float  # 0-25
    historical_fail_component: float  # 0-25
    components: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "word_count_component": self.word_count_component,
            "file_count_component": self.file_count_component,
            "modification_scope_component": self.modification_scope_component,
            "historical_fail_component": self.historical_fail_component,
            "components": dict(self.components),
        }


# Weighting for the four component scores (hermes default).
COMPONENT_WEIGHTS = {
    "word_count": 0.25,
    "file_count": 0.25,
    "modification_scope": 0.25,
    "historical_fail_rate": 0.25,
}


class DifficultyScorer:
    """Deterministic task-difficulty scorer used to drive tier escalation."""

    def __init__(
        self,
        fail_rate_provider: Optional[Callable[[str], float]] = None,
    ) -> None:
        """Create a scorer.

        ``fail_rate_provider`` optionally maps a task-class name to a historical
        failure rate in [0, 1]; when absent the historical component is 0.
        """
        self.fail_rate_provider = fail_rate_provider
        self._history: List[Dict[str, Any]] = []

    def score(
        self,
        prompt: str = "",
        files: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None,
        repo_loc: Optional[int] = None,
        task_class: Optional[str] = None,
    ) -> DifficultyScore:
        """Compute a 0-100 difficulty score for a task.

        Every signal is optional and defaults to its minimum, so a caller that
        only knows the task class still gets a valid, deterministic score.
        """
        if not isinstance(prompt, str):
            raise ValueError("prompt must be a string")
        word = self._score_word_count(prompt)
        file_comp = self._score_file_count(files or [])
        mod = self._score_modification_scope(symbols or [], repo_loc)
        fail = self._score_historical_failure(task_class)
        total = (
            word * COMPONENT_WEIGHTS["word_count"]
            + file_comp * COMPONENT_WEIGHTS["file_count"]
            + mod * COMPONENT_WEIGHTS["modification_scope"]
            + fail * COMPONENT_WEIGHTS["historical_fail_rate"]
        )
        score = DifficultyScore(
            score=round(total, 2),
            word_count_component=word,
            file_count_component=file_comp,
            modification_scope_component=mod,
            historical_fail_component=fail,
            components={
                "word_count": word,
                "file_count": file_comp,
                "modification_scope": mod,
                "historical_fail_rate": fail,
                "weights": dict(COMPONENT_WEIGHTS),
            },
        )
        self._history.append(
            {"task_class": task_class, "score": score.score}
        )
        return score

    # -- component scorers (each returns 0-25, hermes-adapted) -----------------
    @staticmethod
    def _score_word_count(prompt: str) -> float:
        """Longer prompts imply more involved work. 10 words = 0, 150+ = 25."""
        words = len(prompt.split())
        if words < 10:
            return 0.0
        if words > 150:
            return 25.0
        return round((words - 10) / (150 - 10) * 25.0, 2)

    @staticmethod
    def _score_file_count(files: List[str]) -> float:
        """More touched files = wider blast radius. 1 file = 0, 5+ = 25."""
        if not files:
            return 0.0
        count = len(files)
        if count <= 1:
            return 0.0
        if count >= 5:
            return 25.0
        return round((count - 1) / (5 - 1) * 25.0, 2)

    @staticmethod
    def _score_modification_scope(
        symbols: List[str], repo_loc: Optional[int]
    ) -> float:
        """Affected symbols plus repo size amplify risk (capped at 25)."""
        base = 0.0
        if symbols:
            base = min(15.0, len(symbols) / 20.0 * 15.0)
        if repo_loc:
            if repo_loc > 100_000:
                base += 10.0
            elif repo_loc > 50_000:
                base += 5.0
        return round(min(25.0, base), 2)

    def _score_historical_failure(self, task_class: Optional[str]) -> float:
        """A task class that has failed often needs a stronger tier."""
        if task_class is None or self.fail_rate_provider is None:
            return 0.0
        try:
            rate = self.fail_rate_provider(task_class)
        except Exception:
            return 0.0
        if not isinstance(rate, (int, float)) or not 0.0 <= rate <= 1.0:
            return 0.0
        return round(float(rate) * 25.0, 2)

    # -- observability -----------------------------------------------------------
    @property
    def history(self) -> List[Dict[str, Any]]:
        """Scored-task history (deterministic ordering, useful for tests)."""
        return list(self._history)
