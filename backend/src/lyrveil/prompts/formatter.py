"""PromptFormatter: renders the four logical prompt parts as the text of one template family (ADR 0025, ADR 0031).

Rendering is pure: the same input always gives the same bytes, so the stable text and the history stay identical
between replies and KoboldCpp reuses them from its cache. No family writes BOS, because KoboldCpp adds it, and
thinking is always off. Turn text is placed verbatim, except that frame markers are neutralized
(`neutralize_frame_markers`).
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal

from lyrveil.catalog import TextTemplate

Role = Literal["user", "character"]

# A line that starts, after optional spaces, with one of the two frame markers of docs/prompting.md, in any case.
# A lone "\r" also starts a line, because the tokenizer and the model read it as a line break.
_FRAME_MARKER_LINE = re.compile(
    r"(?:^|(?<=\r))([^\S\r\n]*)\[(?=(?:contesto[^\S\r\n]+per|messaggio[^\S\r\n]+di)\b)",
    re.IGNORECASE | re.MULTILINE,
)


def neutralize_frame_markers(text: str) -> str:
    """Returns `text` with the opening `[` of every line that starts with a frame marker replaced with `(`.

    Only `[Contesto per` and `[Messaggio di` at the start of a line count, so other brackets, such as the `[sicuro]`
    labels of memories, are kept. The function is idempotent, and it changes nothing else in the text.
    """
    return _FRAME_MARKER_LINE.sub(r"\1(", text)


@dataclass(frozen=True)
class Turn:
    """One message of the history: `user` for the user, `character` for the character's replies."""

    role: Role
    text: str


@dataclass(frozen=True)
class ChatParts:
    """The parts of a chat prompt that a reply and a proactive message share (docs/prompting.md, Prompt format).

    `stable_text` is blocks 1, 2, 3 and 6. `history` is block 7, oldest first. `state`, `memories` and `anchor` are
    blocks 4, 5 and 9 of the dynamic context: an empty block is left out of the frame, and trailing line breaks of a
    block are dropped. The names fill the frame markers.
    """

    stable_text: str
    history: Sequence[Turn]
    state: str
    memories: str
    anchor: str
    character_first_name: str
    user_display_name: str


@dataclass(frozen=True)
class FormattedPrompt:
    """A rendered prompt. `prefix` is the start of `text` that depends only on the history, and for `gemma4` and
    `chatml` on the stable text too: it ends where the last user turn begins. `text.startswith(prefix)` always holds.
    """

    text: str
    prefix: str


class PromptFormatter(ABC):
    """Renders prompts for one template family. Subclasses define only how turns are written."""

    family: ClassVar[TextTemplate]
    # Control text that ends a reply in this family, before the user-name stop sequence.
    _end_sequences: ClassVar[tuple[str, ...]]

    def stop_sequences(self, user_display_name: str) -> tuple[str, ...]:
        """Returns the family's stop sequences for a chat with `user_display_name` (docs/prompting.md)."""
        return (*self._end_sequences, f"\n{user_display_name}:")

    def chat(self, parts: ChatParts, current_message: str) -> FormattedPrompt:
        """Renders a reply: stable text, history, then a last user turn with the frame and `current_message`."""
        closing = f"[Messaggio di {parts.user_display_name}]\n{neutralize_frame_markers(current_message)}"
        return self._chat_prompt(parts, closing)

    def proactive(self, parts: ChatParts, instruction: str) -> FormattedPrompt:
        """Renders a proactive message: as `chat`, but the frame ends with `instruction` and has no message marker.

        The instruction is neutralized too, because it carries the reason written by the proactive decision.
        """
        return self._chat_prompt(parts, neutralize_frame_markers(instruction))

    def structured(self, system_text: str, user_text: str) -> str:
        """Renders a structured task: one system text and one user turn, with no frame and no history."""
        return self._render(neutralize_frame_markers(system_text), (), neutralize_frame_markers(user_text)).text

    def _chat_prompt(self, parts: ChatParts, closing: str) -> FormattedPrompt:
        header = f"[Contesto per {parts.character_first_name}, non scritto da {parts.user_display_name}]"
        blocks = (parts.state, parts.memories, parts.anchor)
        kept = [neutralize_frame_markers(block.rstrip("\r\n")) for block in blocks if block.strip()]
        frame = "\n".join([header, *kept, closing])
        history = tuple(Turn(turn.role, neutralize_frame_markers(turn.text)) for turn in parts.history)
        return self._render(neutralize_frame_markers(parts.stable_text), history, frame)

    @abstractmethod
    def _render(self, system_text: str, history: Sequence[Turn], last_user_text: str) -> FormattedPrompt:
        """Writes the system text, the history and the last user turn, ending with the opening of the reply."""


class Gemma4Formatter(PromptFormatter):
    """`gemma4`: the official template with thinking off, ending with the empty thought channel."""

    family: ClassVar[TextTemplate] = "gemma4"
    _end_sequences: ClassVar[tuple[str, ...]] = ("<turn|>", "<|turn>")

    def _render(self, system_text: str, history: Sequence[Turn], last_user_text: str) -> FormattedPrompt:
        roles = {"user": "user", "character": "model"}
        prefix = f"<|turn>system\n{system_text}<turn|>\n" + "".join(
            f"<|turn>{roles[turn.role]}\n{turn.text}<turn|>\n" for turn in history
        )
        text = f"{prefix}<|turn>user\n{last_user_text}<turn|>\n<|turn>model\n<|channel>thought\n<channel|>"
        return FormattedPrompt(text=text, prefix=prefix)


class MistralV3TekkenFormatter(PromptFormatter):
    """`mistral_v3_tekken`: no spaces around the markers, and the system text joins the last user turn.

    Each turn is written by its own role, so two replies in a row, such as a proactive message after a reply, give
    two `{reply}</s>` pieces. The GGUF template would refuse that order; the layout here is an assumption until the
    GPU suite (T-GPU-03, T-GPU-04).
    """

    family: ClassVar[TextTemplate] = "mistral_v3_tekken"
    _end_sequences: ClassVar[tuple[str, ...]] = ("</s>", "[INST]")

    def _render(self, system_text: str, history: Sequence[Turn], last_user_text: str) -> FormattedPrompt:
        prefix = "".join(f"[INST]{turn.text}[/INST]" if turn.role == "user" else f"{turn.text}</s>" for turn in history)
        text = f"{prefix}[INST]{system_text}\n\n{last_user_text}[/INST]"
        return FormattedPrompt(text=text, prefix=prefix)


class ChatMlFormatter(PromptFormatter):
    """`chatml`: the markers are ordinary text in the tokenizer of the profiles that use it."""

    family: ClassVar[TextTemplate] = "chatml"
    _end_sequences: ClassVar[tuple[str, ...]] = ("<|im_end|>", "<|im_start|>", "</s>")

    def _render(self, system_text: str, history: Sequence[Turn], last_user_text: str) -> FormattedPrompt:
        roles = {"user": "user", "character": "assistant"}
        prefix = f"<|im_start|>system\n{system_text}<|im_end|>\n" + "".join(
            f"<|im_start|>{roles[turn.role]}\n{turn.text}<|im_end|>\n" for turn in history
        )
        text = f"{prefix}<|im_start|>user\n{last_user_text}<|im_end|>\n<|im_start|>assistant\n"
        return FormattedPrompt(text=text, prefix=prefix)


_FORMATTERS: dict[TextTemplate, PromptFormatter] = {
    formatter.family: formatter for formatter in (Gemma4Formatter(), MistralV3TekkenFormatter(), ChatMlFormatter())
}


def formatter_for(family: TextTemplate) -> PromptFormatter:
    """Returns the formatter of a catalog template family. Raises KeyError for a name outside `TextTemplate`."""
    return _FORMATTERS[family]
