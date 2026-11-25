from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)

from agent.graph import build_agent, TOOLS


class FakeModel:

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):

        # After receiving tool results, produce final answer.
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(
                content="I found properties P001, P003, and P010."
            )

        # Simulate AI selecting the search tool.
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_properties",
                    "args": {
                        "location": "KL",
                        "property_type": "CONDO",
                        "max_price": 600000,
                        "near_mrt": True,
                    },
                    "id": "call_001",
                    "type": "tool_call",
                }
            ],
        )


def test_only_read_only_tools_registered():
    names = {tool.name for tool in TOOLS}

    assert names == {
        "search_properties",
        "get_property_details",
         "search_documents",
    }


def test_agent_executes_property_search():

    agent = build_agent(FakeModel())

    result = agent.invoke(
        {
            "messages": [
                HumanMessage(
                    content="Find condos in KL under RM600000 near MRT"
                )
            ]
        }
    )

    tool_messages = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert len(tool_messages) == 1

    tool_result = str(tool_messages[0].content)

    assert "P001" in tool_result
    assert "P003" in tool_result
    assert "P010" in tool_result

    assert result["messages"][-1].content == (
        "I found properties P001, P003, and P010."
    )
