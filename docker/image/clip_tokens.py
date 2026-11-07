"""Counts the CLIP tokens of the tag vocabulary of ADR 0035 with the tokenizer of the pinned ComfyUI tree (T-IMG-16).

Runs inside the build stages clip-tokens-run and clip-tokens-check of docker/image/Dockerfile, with the virtual
environment of the image and the sources at /opt/ComfyUI:
  python clip_tokens.py write SOURCE TARGET   writes SOURCE to TARGET with every `clip_tokens` recomputed
  python clip_tokens.py check SOURCE          exits 1 and names every entry whose `clip_tokens` differs or is missing
The count of a tag is what ComfyUI's SDXL tokenizer gives it inside a prompt: the tokens that carry a word id, so never
the start, end or padding tokens. The CLIP-L and CLIP-G counts must agree, or the script fails. Separators are not
counted here: the prompt builder adds one token per comma.
"""

import json
import sys
from pathlib import Path
from typing import Any

COMFYUI = "/opt/ComfyUI"


def _tokenizer() -> Any:
    """Returns ComfyUI's SDXLTokenizer. Raises ImportError when the tree is not at /opt/ComfyUI."""
    sys.path.insert(0, COMFYUI)
    import comfy.cli_args

    # comfy.model_management picks its device at import time and, without --cpu, asks CUDA for a GPU the build has not.
    comfy.cli_args.args.cpu = True
    from comfy.sdxl_clip import SDXLTokenizer

    return SDXLTokenizer()


def count_tokens(tokenizer: Any, tag: str) -> int:
    """Returns the number of tokens ComfyUI encodes for `tag`. Raises ValueError when CLIP-L and CLIP-G disagree."""
    encoded = tokenizer.tokenize_with_weights(tag, return_word_ids=True)
    counts = {
        name: sum(1 for batch in batches for token in batch if token[2] != 0) for name, batches in encoded.items()
    }
    if len(set(counts.values())) != 1:
        raise ValueError(f"{tag!r}: the encoders disagree: {counts}")
    return next(iter(counts.values()))


def render(document: dict[str, Any]) -> str:
    """Returns the vocabulary as the repository keeps it: one entry per line, a blank line between categories."""
    lines = ["{"]
    for key, value in document.items():
        if key != "tags":
            lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append('  "tags": [')
    entries = document["tags"]
    for index, entry in enumerate(entries):
        if index and entry["category"] != entries[index - 1]["category"]:
            lines.append("")
        separator = "," if index < len(entries) - 1 else ""
        lines.append(f"    {json.dumps(entry, ensure_ascii=False)}{separator}")
    lines += ["  ]", "}"]
    return "\n".join(lines) + "\n"


def main(arguments: list[str]) -> int:
    """Returns the exit status: 0 when done or matching, 1 when `check` finds a difference, 2 for a usage error."""
    if not ((len(arguments) == 3 and arguments[0] == "write") or (len(arguments) == 2 and arguments[0] == "check")):
        print(__doc__, file=sys.stderr)
        return 2
    document = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
    tokenizer = _tokenizer()
    if arguments[0] == "write":
        document["tags"] = [
            {**{key: value for key, value in entry.items() if key != "clip_tokens"},
             "clip_tokens": count_tokens(tokenizer, entry["tag"])}
            for entry in document["tags"]
        ]
        Path(arguments[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(arguments[2]).write_text(render(document), encoding="utf-8", newline="\n")
        print(f"counted {len(document['tags'])} tags")
        return 0
    wrong = []
    for entry in document["tags"]:
        expected = count_tokens(tokenizer, entry["tag"])
        if entry.get("clip_tokens") != expected:
            wrong.append(f"{entry['tag']!r} ({entry['category']}): file {entry.get('clip_tokens')}, tokenizer {expected}")
    for line in wrong:
        print(line, file=sys.stderr)
    print(f"checked {len(document['tags'])} tags, {len(wrong)} differ")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
