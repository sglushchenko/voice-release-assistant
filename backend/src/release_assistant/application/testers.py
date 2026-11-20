"""TestFlight tester management: read queries, the two write actions and their default
approval modes. Reads need no confirmation; writes go through ActionService.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from release_assistant.application.actions import ActionDefinition
from release_assistant.application.risk import ActionSpec, ApprovalMode, RiskTier
from release_assistant.domain.entities import BetaGroup, BetaTester
from release_assistant.domain.errors import NotFoundError
from release_assistant.domain.ports import TesterDirectory

ADD_TESTER = "add_tester"
REMOVE_TESTER = "remove_tester"

DEFAULT_APPROVAL_MODES = {
    ADD_TESTER: ApprovalMode.VOICE_OR_BUTTON,
    REMOVE_TESTER: ApprovalMode.BUTTON_ONLY,
}

_Id = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
_Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]


class AddTesterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: _Id
    email: _Email
    first_name: _Name | None = None
    last_name: _Name | None = None


class RemoveTesterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: _Id
    tester_id: _Id


class BetaTesterService:
    """Read-only tester questions.

    Args:
        directory: Source of groups and testers; mock or real.
    """

    def __init__(self, directory: TesterDirectory) -> None:
        self._directory = directory

    async def list_groups(self, app_id: str) -> list[BetaGroup]:
        return await self._directory.list_groups(app_id)

    async def list_testers(self, group_id: str) -> list[BetaTester]:
        return await self._directory.list_testers(group_id)

    async def find_in_group(self, group_id: str, email: str) -> BetaTester | None:
        """The tester with this email in the group, or None. Email match ignores case and
        surrounding spaces."""
        needle = email.strip().lower()
        testers = await self._directory.list_testers(group_id)
        return next((t for t in testers if t.email == needle), None)

    async def groups_of(self, app_id: str, email: str) -> list[BetaGroup]:
        """Groups of `app_id` that include this email; answers "where is Anna testing?"."""
        groups = await self._directory.list_groups(app_id)
        return [g for g in groups if await self.find_in_group(g.id, email) is not None]


def build_tester_actions(directory: TesterDirectory) -> list[ActionDefinition]:
    """Build the tester write actions over `directory`.

    Both resolve names while describing, so an unknown group or tester fails at proposal
    time, before anything is stored or shown for approval.
    """

    async def describe_add(args: AddTesterArgs) -> str:
        group = await directory.get_group(args.group_id)
        return f"Add tester {args.email} to TestFlight group '{group.name}'"

    async def add(args: AddTesterArgs) -> BetaTester:
        return await directory.add_tester(
            args.group_id, args.email, args.first_name, args.last_name
        )

    async def describe_remove(args: RemoveTesterArgs) -> str:
        group = await directory.get_group(args.group_id)
        members = await directory.list_testers(args.group_id)
        tester = next((t for t in members if t.id == args.tester_id), None)
        if tester is None:
            raise NotFoundError(f"Tester {args.tester_id!r} is not in group {args.group_id!r}")
        return f"Remove tester {tester.email} from TestFlight group '{group.name}'"

    async def remove(args: RemoveTesterArgs) -> None:
        await directory.remove_tester(args.group_id, args.tester_id)

    return [
        ActionDefinition(
            spec=ActionSpec(ADD_TESTER, RiskTier.REVERSIBLE_WRITE),
            arguments_model=AddTesterArgs,
            describe=describe_add,
            handler=add,
        ),
        ActionDefinition(
            spec=ActionSpec(REMOVE_TESTER, RiskTier.IRREVERSIBLE),
            arguments_model=RemoveTesterArgs,
            describe=describe_remove,
            handler=remove,
        ),
    ]
