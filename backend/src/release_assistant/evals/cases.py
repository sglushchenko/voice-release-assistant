"""Evaluation cases and the dataset loader.

A dataset is a directory of YAML files. Its content hash identifies the version: results of two
runs are only comparable case by case when the hashes match.
"""

import hashlib
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from release_assistant.evals.checks import ATTACK_CHECKS, AttackGoal, Check

Suite = Literal["regression", "capability"]
Category = Literal["lookup", "report", "write", "safety", "injection"]


class DatasetError(Exception):
    """A dataset file is unreadable or invalid. The message names the file and the problem."""


class UseStep(BaseModel):
    """The scripted model calls a tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    use: str
    args: dict[str, Any] = {}


class SayStep(BaseModel):
    """The scripted model answers in text, ending the run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    say: str


ScriptStep = UseStep | SayStep


class EvalCase(BaseModel):
    """One scenario.

    Args:
        id: Stable identifier; history is keyed by it, so renaming a case breaks comparison.
        suite: `regression` cases are expected to pass and gate changes; `capability` cases
            measure what the model can do and may fail while it improves.
        category: What the case is about.
        description: Why the case exists.
        user: The user's message.
        reviews: Review bodies added to app-1's customer reviews (newest), for injection tests.
        attack: The goal of an injection, for graders that measure it.
        script: How the scripted model behaves in this case. Cases without one run only against
            a real model; scripted cases test the system under a given model behaviour.
        checks: All must pass for a trial to pass.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")]
    suite: Suite
    category: Category
    description: str = Field(min_length=10)
    user: str = Field(min_length=1)
    reviews: tuple[str, ...] = ()
    attack: AttackGoal | None = None
    script: tuple[ScriptStep, ...] | None = None
    checks: tuple[Check, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if any(isinstance(c, ATTACK_CHECKS) for c in self.checks) and self.attack is None:
            raise ValueError("attack checks need an `attack` goal")
        if self.script is not None and not isinstance(self.script[-1], SayStep):
            raise ValueError("a script must end with a `say` step")
        return self

    @property
    def scripted(self) -> bool:
        return self.script is not None


class Dataset(BaseModel):
    model_config = ConfigDict(frozen=True)

    cases: tuple[EvalCase, ...]
    version_hash: str

    def select(self, *, suite: str = "all", ids: tuple[str, ...] = ()) -> tuple[EvalCase, ...]:
        chosen = [c for c in self.cases if suite in ("all", c.suite)]
        if ids:
            unknown = set(ids) - {c.id for c in self.cases}
            if unknown:
                raise DatasetError(f"unknown case id(s): {', '.join(sorted(unknown))}")
            chosen = [c for c in chosen if c.id in ids]
        return tuple(chosen)


def load_dataset(directory: Path) -> Dataset:
    """Read every `*.yaml` file in `directory` (sorted by name)."""
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise DatasetError(f"no dataset files (*.yaml) in {directory}")

    digest = hashlib.sha256()
    cases: list[EvalCase] = []
    for path in files:
        raw = path.read_bytes()
        digest.update(path.name.encode() + b"\0" + raw)
        cases.extend(_parse(path, raw))

    ids = [c.id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise DatasetError(f"duplicate case id(s): {', '.join(duplicates)}")
    return Dataset(cases=tuple(cases), version_hash=digest.hexdigest()[:12])


def _parse(path: Path, raw: bytes) -> list[EvalCase]:
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise DatasetError(f"{path.name}: not valid YAML ({error})") from error
    if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
        raise DatasetError(f"{path.name}: expected a mapping with a `cases` list")
    cases: list[EvalCase] = []
    for index, entry in enumerate(document["cases"], start=1):
        label = entry.get("id", f"#{index}") if isinstance(entry, dict) else f"#{index}"
        try:
            cases.append(EvalCase.model_validate(entry))
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            raise DatasetError(f"{path.name}, case {label}: {problems}") from error
    return cases
