"""Live native prompts backed by the same content loader as read_procedure."""

from typing import Any

from fastmcp.prompts.base import Prompt, PromptArgument
from fastmcp.server.providers import Provider
from pydantic import PrivateAttr

from hermod.content.loader import ContentLoader


class ContentPrompt(Prompt):
    _content: ContentLoader = PrivateAttr()

    async def render(self, arguments: dict[str, Any] | None = None) -> str:
        slot = self.arguments[0].name if self.arguments else None
        return self._content.get_prompt(self.name, (arguments or {}).get(slot) if slot else None)


class ContentPromptProvider(Provider):
    """Construct only metadata on listing; compose the selected procedure on render."""

    def __init__(self, content: ContentLoader) -> None:
        super().__init__()
        self.content = content

    def _make(self, name: str) -> ContentPrompt:
        title, description, argument = self.content.prompt_info(name)
        slots = None
        if argument:
            arg_name, arg_help = argument
            slots = [PromptArgument(
                name=arg_name,
                description=(
                    f"{arg_help}. Optional text substituted into $ARGUMENTS; "
                    "fetching does not execute the procedure."
                ),
                required=False,
            )]
        prompt = ContentPrompt(name=name, title=title, description=description, arguments=slots)
        prompt._content = self.content
        return prompt

    async def _list_prompts(self) -> list[Prompt]:
        return [self._make(name) for name in self.content.list_prompts()]

    async def _get_prompt(self, name: str, version=None) -> Prompt | None:
        try:
            return self._make(name)
        except KeyError:
            return None
