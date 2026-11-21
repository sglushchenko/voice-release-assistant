"""One conversation: load the history, let the agent answer, store what it added."""

from release_assistant.agent.conversation import ConversationStore
from release_assistant.agent.messages import AgentRun
from release_assistant.agent.runner import Agent


class Chat:
    """
    Args:
        agent: Answers each message.
        store: Keeps the history between messages (and, later, between restarts).
        conversation_id: Which conversation this is.
    """

    def __init__(self, agent: Agent, store: ConversationStore, conversation_id: str) -> None:
        self._agent = agent
        self._store = store
        self._conversation_id = conversation_id

    async def send(self, text: str) -> AgentRun:
        history = await self._store.load(self._conversation_id)
        run = await self._agent.respond(history, text)
        await self._store.append(self._conversation_id, run.new_messages)
        return run
