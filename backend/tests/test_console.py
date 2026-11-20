import pytest

from release_assistant.agent.console import Console, open_console
from release_assistant.agent.scripted import ScriptedModel, say, use
from release_assistant.config import Settings


def console_with(*script):  # type: ignore[no-untyped-def]
    return open_console(Settings(_env_file=None), ScriptedModel(script))


async def test_a_question_is_answered_and_the_footer_shows_the_cost() -> None:
    console = console_with(use("list_apps"), say("You have two apps."))

    reply = await console.handle("what apps do I have?")

    assert "You have two apps." in reply.text
    assert "steps 2 · tools 1" in reply.text


async def test_trace_shows_which_tool_ran_with_what() -> None:
    console = console_with(use("list_apps"), say("ok"))
    await console.handle("apps?")

    trace = (await console.handle("/trace")).text

    assert "-> list_apps" in trace
    assert "<- list_apps [trusted]" in trace


async def test_a_proposed_change_waits_until_the_person_confirms_it() -> None:
    console = console_with(
        use("propose_add_tester", group_id="group-1", email="new.tester@example.com"),
        say("I asked for your approval."),
    )
    reply = await console.handle("add new.tester@example.com to the first group")
    assert "waiting for you" in reply.text
    pending = (await console.handle("/pending")).text
    action_id = pending.split()[0]

    assert (await console.handle(f"/confirm {action_id}")).text == "Done."
    assert (await console.handle("/pending")).text == "Nothing is waiting for you."


async def test_rejecting_cancels_the_action() -> None:
    console = console_with(
        use("propose_add_tester", group_id="group-1", email="new.tester@example.com"),
        say("Proposed."),
    )
    await console.handle("add someone")
    action_id = (await console.handle("/pending")).text.split()[0]

    assert (await console.handle(f"/reject {action_id}")).text == "Cancelled."
    assert (await console.handle("/pending")).text == "Nothing is waiting for you."


async def test_an_unknown_action_is_reported_not_crashed_on() -> None:
    reply = await console_with().handle("/confirm nope")

    assert reply.text.startswith("Refused (404)")


@pytest.mark.parametrize("line", ["/confirm", "/reject"])
async def test_confirm_and_reject_need_an_id(line: str) -> None:
    assert "Usage" in (await console_with().handle(line)).text


async def test_reset_starts_a_fresh_conversation() -> None:
    console = console_with(say("first"), say("second"))
    await console.handle("hello")

    assert (await console.handle("/reset")).text == "Conversation cleared."
    assert "Nothing to show yet" in (await console.handle("/trace")).text
    assert "second" in (await console.handle("again")).text


async def test_quit_and_unknown_commands() -> None:
    console: Console = console_with()

    assert (await console.handle("/quit")).quit
    assert "Unknown command" in (await console.handle("/frobnicate")).text
    assert (await console.handle("   ")).text == ""
