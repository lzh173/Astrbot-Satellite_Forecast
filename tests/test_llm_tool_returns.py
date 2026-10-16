from pathlib import Path


MAIN_SOURCE = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")


def test_llm_tools_return_strings_instead_of_message_results() -> None:
    tool_source = MAIN_SOURCE[MAIN_SOURCE.index('@filter.llm_tool(name="resolve_observer_location")'):]
    assert tool_source.count("-> str:") >= 10
    assert "return event.plain_result(" not in tool_source
