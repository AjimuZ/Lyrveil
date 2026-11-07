"""Values of contextual image proposals that hold without a database (docs/image-generation.md, Contextual mode)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

# docs/image-generation.md (Contextual mode): a proposal nobody answered expires after this many hours.
PROPOSAL_TTL_HOURS = 24
# At most one proposal per conversation in this many minutes, however it was answered.
PROPOSAL_COOLDOWN_MINUTES = 60
# docs/configuration.md (`images.proposal_min_messages`): the default and the allowed range.
DEFAULT_MIN_MESSAGES = 20
MIN_MIN_MESSAGES = 5
MAX_MIN_MESSAGES = 200


class ImageProposalResult(BaseModel):
    """The structured decision of the `image_proposal` step. `propose` false means the character has nothing to show;
    `scene` is what the picture would show and `reason` why now. The model's output is untrusted until validated."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    propose: bool
    scene: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
