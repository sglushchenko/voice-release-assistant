import pytest

from release_assistant.application.policy import ConfirmationContext, ConfirmationPolicy
from release_assistant.application.risk import (
    ActionSpec,
    ApprovalMode,
    ConfirmationChannel,
    RiskTier,
)

REVERSIBLE = ActionSpec("add_tester", RiskTier.REVERSIBLE_WRITE)
PUBLIC = ActionSpec("reply", RiskTier.PUBLIC_WRITE)
IRREVERSIBLE = ActionSpec("delete_tester", RiskTier.IRREVERSIBLE)


def test_unconfigured_action_fails_closed() -> None:
    assert ConfirmationPolicy().effective_mode(REVERSIBLE) is ApprovalMode.BUTTON_ONLY


def test_configuration_is_honoured_when_tier_allows_it() -> None:
    policy = ConfirmationPolicy({"add_tester": ApprovalMode.IMPLICIT_IF_CLEAR})

    assert policy.effective_mode(REVERSIBLE) is ApprovalMode.IMPLICIT_IF_CLEAR


@pytest.mark.parametrize(
    ("spec", "floor"),
    [
        (PUBLIC, ApprovalMode.VOICE_OR_BUTTON),
        (IRREVERSIBLE, ApprovalMode.BUTTON_ONLY),
    ],
)
def test_configuration_cannot_go_below_tier_floor(spec: ActionSpec, floor: ApprovalMode) -> None:
    policy = ConfirmationPolicy({spec.name: ApprovalMode.IMPLICIT_IF_CLEAR})

    assert policy.effective_mode(spec) is floor


@pytest.mark.parametrize(
    "configured", [ApprovalMode.IMPLICIT_IF_CLEAR, ApprovalMode.VOICE_OR_BUTTON]
)
def test_untrusted_content_forces_button_only(configured: ApprovalMode) -> None:
    policy = ConfirmationPolicy({"add_tester": configured})

    mode = policy.effective_mode(REVERSIBLE, ConfirmationContext(untrusted_content_seen=True))

    assert mode is ApprovalMode.BUTTON_ONLY


@pytest.mark.parametrize(
    ("mode", "channel", "expected"),
    [
        (ApprovalMode.BUTTON_ONLY, ConfirmationChannel.BUTTON, True),
        (ApprovalMode.BUTTON_ONLY, ConfirmationChannel.VOICE, False),
        (ApprovalMode.VOICE_OR_BUTTON, ConfirmationChannel.VOICE, True),
        (ApprovalMode.VOICE_OR_BUTTON, ConfirmationChannel.BUTTON, True),
    ],
)
def test_accepts_matrix(mode: ApprovalMode, channel: ConfirmationChannel, expected: bool) -> None:
    assert ConfirmationPolicy.accepts(mode, channel) is expected
