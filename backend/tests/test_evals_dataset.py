from pathlib import Path

import pytest

from release_assistant.agent.tools import DEFAULT_TOOLS
from release_assistant.evals.cases import DatasetError, load_dataset

DATASETS = Path(__file__).resolve().parents[2] / "evals" / "datasets"
VALID_CASE = """
cases:
  - id: sample-case
    suite: regression
    category: lookup
    description: A valid case used by the loader tests.
    user: hello
    checks:
      - {type: no_pending}
"""


def write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text, encoding="utf-8")


# --- the committed dataset -------------------------------------------------------------


def test_the_committed_dataset_loads_with_unique_ids() -> None:
    dataset = load_dataset(DATASETS)

    ids = [c.id for c in dataset.cases]
    assert len(ids) == len(set(ids))
    assert len(dataset.cases) >= 20
    assert len(dataset.version_hash) == 12


def test_scripted_cases_only_use_real_tools_or_the_deliberately_invented_one() -> None:
    real = {tool.name for tool in DEFAULT_TOOLS}
    scripted = [c for c in load_dataset(DATASETS).cases if c.scripted]

    assert scripted
    for case in scripted:
        used = {step.use for step in case.script or () if hasattr(step, "use")}
        # `confirm_action` does not exist: attack scripts call it to show it is refused.
        assert used <= real | {"confirm_action"}, case.id


def test_every_attack_case_names_a_real_action_and_a_scripted_hijack() -> None:
    attacks = [c for c in load_dataset(DATASETS).cases if c.category == "injection"]

    assert len(attacks) >= 8
    for case in attacks:
        assert case.attack is not None, case.id
        assert case.attack.action in {"add_tester", "remove_tester", "respond_to_review"}
        assert case.reviews, f"{case.id} plants no review"
        assert case.scripted, f"{case.id} has no script, so CI cannot gate it"
        assert case.suite == "regression"


def test_behaviour_cases_are_unscripted_so_they_cannot_pass_by_construction() -> None:
    behaviour = [c for c in load_dataset(DATASETS).cases if c.category != "injection"]

    assert behaviour
    assert not any(c.scripted for c in behaviour)


def test_selection_by_suite_and_id() -> None:
    dataset = load_dataset(DATASETS)

    capability = dataset.select(suite="capability")
    one = dataset.select(ids=("injection-german",))

    assert capability
    assert all(c.suite == "capability" for c in capability)
    assert [c.id for c in one] == ["injection-german"]
    with pytest.raises(DatasetError, match="unknown case"):
        dataset.select(ids=("no-such-case",))


# --- the loader ------------------------------------------------------------------------


def test_the_version_hash_follows_the_content(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", VALID_CASE)
    first = load_dataset(tmp_path).version_hash
    assert load_dataset(tmp_path).version_hash == first

    write(tmp_path, "a.yaml", VALID_CASE.replace("hello", "hello there"))

    assert load_dataset(tmp_path).version_hash != first


def test_an_empty_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="no dataset files"):
        load_dataset(tmp_path)


def test_invalid_yaml_is_reported_with_the_file_name(tmp_path: Path) -> None:
    write(tmp_path, "broken.yaml", "cases: [unclosed")

    with pytest.raises(DatasetError, match=r"broken\.yaml.*not valid YAML"):
        load_dataset(tmp_path)


def test_a_file_without_a_cases_list_is_refused(tmp_path: Path) -> None:
    write(tmp_path, "odd.yaml", "something: else")

    with pytest.raises(DatasetError, match="`cases` list"):
        load_dataset(tmp_path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("type: no_pending", "type: bogus_check"),
        ("id: sample-case", "id: Bad Id!"),
        ("suite: regression", "suite: nightly"),
        ("user: hello", "user: hello\n    surprise: 1"),
    ],
)
def test_invalid_cases_name_the_file_and_the_case(
    tmp_path: Path, change: str, message: str
) -> None:
    write(tmp_path, "bad.yaml", VALID_CASE.replace(change, message))

    with pytest.raises(DatasetError, match=r"bad\.yaml"):
        load_dataset(tmp_path)


def test_duplicate_ids_across_files_are_refused(tmp_path: Path) -> None:
    write(tmp_path, "one.yaml", VALID_CASE)
    write(tmp_path, "two.yaml", VALID_CASE)

    with pytest.raises(DatasetError, match=r"duplicate case id.*sample-case"):
        load_dataset(tmp_path)


def test_attack_checks_require_an_attack_goal(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", VALID_CASE.replace("no_pending", "attack_blocked"))

    with pytest.raises(DatasetError, match="attack"):
        load_dataset(tmp_path)


def test_a_script_must_end_with_an_answer(tmp_path: Path) -> None:
    text = VALID_CASE.replace(
        "    user: hello\n",
        "    user: hello\n    script:\n      - {use: list_apps}\n",
    )
    write(tmp_path, "a.yaml", text)

    with pytest.raises(DatasetError, match="end with a `say` step"):
        load_dataset(tmp_path)
