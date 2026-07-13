import aiosqlite
import pytest
import pytest_asyncio
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import PrivateAttr

from agents.telegram_bot_agent import TelegramBotAgent


class ScriptedChatModel(BaseChatModel):
    """Fake chat model that returns a scripted sequence of AIMessages and
    ignores bound tool schemas — langchain_core's own fakes raise
    NotImplementedError on bind_tools, so a custom one is needed to exercise
    a real ReAct loop (tool call -> ToolMessage -> final answer) end to end."""

    responses: list[AIMessage]
    _index: int = PrivateAttr(default=0)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = self.responses[self._index]
        self._index += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"


@pytest_asyncio.fixture
async def checkpointer():
    conn = await aiosqlite.connect(":memory:")
    try:
        yield AsyncSqliteSaver(conn)
    finally:
        await conn.close()


@pytest.fixture
def bot(checkpointer):
    return TelegramBotAgent(
        token="fake", groq_key="fake", allowed_users=["1"], checkpointer=checkpointer
    )


def test_build_tools_schema(bot):
    tools = bot._build_tools(chat_id=123)
    names = sorted(t.name for t in tools)
    assert names == [
        "actualizar_estado",
        "analizar_url",
        "buscar_autos",
        "resumen",
        "ver_pipeline",
    ]
    for t in tools:
        assert t.description


@pytest.mark.asyncio
async def test_run_agent_direct_answer(bot):
    bot.model = ScriptedChatModel(
        responses=[AIMessage(content="Hola! ¿En qué te ayudo?")]
    )
    response = await bot._run_agent(chat_id=1, username="tester", text="hola")
    assert response == "Hola! ¿En qué te ayudo?"


@pytest.mark.asyncio
async def test_run_agent_executes_tool_and_answers(bot):
    # Airtable isn't configured in this test, so `resumen` returns its
    # deterministic "not configured" message — the point is verifying the
    # ReAct loop actually calls the real tool and feeds its result back.
    bot.model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[ToolCall(name="resumen", args={}, id="call_1")]
            ),
            AIMessage(content="No tienes datos guardados todavía."),
        ]
    )
    response = await bot._run_agent(
        chat_id=2, username="tester", text="dame el resumen"
    )
    assert response == "No tienes datos guardados todavía."


@pytest.mark.asyncio
async def test_conversation_persists_across_turns(bot, checkpointer):
    bot.model = ScriptedChatModel(responses=[AIMessage(content="primera respuesta")])
    await bot._run_agent(chat_id=3, username="tester", text="hola")

    snapshot = await checkpointer.aget_tuple({"configurable": {"thread_id": "3"}})
    assert snapshot is not None
    messages = snapshot.checkpoint["channel_values"]["messages"]
    assert any(getattr(m, "content", "") == "hola" for m in messages)


@pytest.mark.asyncio
async def test_reset_thread_deletes_history(bot, checkpointer):
    bot.model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    await bot._run_agent(chat_id=4, username="tester", text="hola")

    snapshot_before = await checkpointer.aget_tuple(
        {"configurable": {"thread_id": "4"}}
    )
    assert snapshot_before is not None

    await bot._reset_thread("4")

    snapshot_after = await checkpointer.aget_tuple({"configurable": {"thread_id": "4"}})
    assert snapshot_after is None


@pytest.mark.asyncio
async def test_reset_thread_on_fresh_chat_does_not_raise(bot):
    # Edge case that broke before adding the defensive setup() call:
    # resetting a chat that never sent a message (checkpointer tables not
    # yet initialized for that thread).
    await bot._reset_thread("never-used-thread")
