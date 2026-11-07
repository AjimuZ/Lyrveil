"""The tag sanitizer of docs/image-generation.md (Sanitizer): the function every user or model tag passes."""

import re

# Everything the prompt syntax of ComfyUI could read as a weight, an escape, an embedding or a separator is dropped.
_EMBEDDING = re.compile(r"\S*embedding:\S*")
_DISALLOWED = re.compile(r"[^\w \-'.]", re.UNICODE)
_SPACES = re.compile(r" +")


def sanitize_tag(raw: str) -> str:
    """Returns `raw` as a safe tag, or an empty string when nothing is left.

    Steps, in order: lowercase, trim and turn `_` into a space; drop the word `embedding:` with anything joined to it;
    keep only letters, digits, spaces, hyphens, apostrophes and periods; collapse spaces. The result never holds
    parentheses, colons, brackets, backslashes or commas, so no tag can change a weight or split itself. The function
    is idempotent.
    """
    text = raw.lower().strip().replace("_", " ")
    text = _EMBEDDING.sub(" ", text)
    text = _DISALLOWED.sub("", text)
    return _SPACES.sub(" ", text).strip()
