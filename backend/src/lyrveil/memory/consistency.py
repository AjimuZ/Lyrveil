"""The consistency rules of memory extraction (docs/memory-engine.md, Consistency rules and Deduplication): what one
extracted item does to the memories of its conversation. Pure: MemoryConsistencyService reads the inputs from the
database and writes the decision.

The model only proposes. `decide` takes a resolved item and the memories it could touch, and returns a Decision that
always keeps these invariants: an inference never supersedes or demotes a stated or confirmed memory, a locked memory
is never changed, a temporary item never changes a lasting memory, an `identity_candidate` never changes another
memory, and two active lasting memories with the same single-valued subject never keep different values.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from lyrveil.memory.text import fingerprint, stems

# docs/memory-engine.md (Consistency rules, guard 3): the subjects that hold one value at a time.
SINGLE_VALUED_SUBJECTS = frozenset(
    {"user.name", "user.age", "user.city", "user.employer", "user.job", "user.partner", "user.birthday"}
)
# docs/memory-engine.md (Deduplication): the stem-set Jaccard similarity from which two memories of the same kind and
# subject are one.
SIMILARITY_THRESHOLD = 0.8
# Guard 2: the provenances an inference can never supersede or make uncertain.
PROTECTED_PROVENANCES = frozenset({"stated", "user_confirmed"})
# Guard 1: the kinds that describe the user, so a `stated` one needs a source message by the user.
USER_KINDS = frozenset({"user_fact", "user_preference"})
IDENTITY_CANDIDATE = "identity_candidate"
CONFIRMED_CONFIDENCE = 1.0
# The phrase list, per language under `prompts/<code>/`, of the explicit change cues of guard 3.
CHANGE_CUE_FILE = "change_cues.txt"
# docs/memory-engine.md (Consistency rules): a temporary memory without `valid_until` lasts 7 days.
TEMPORARY_DEFAULT = timedelta(days=7)
MAX_SUBJECT_KEY_CHARS = 64
MAX_VALUE_CHARS = 280
# Revision reasons (`memory_revisions.reason`), one per rule that changes an existing memory.
UPDATE = "update"
CORRECTION = "correction"
CONTRADICTION = "contradiction"
SUBJECT_CHANGE = "subject_change"
SUBJECT_CONFLICT = "subject_conflict"
INFERENCE_CONFLICT = "inference_conflict"
EXPIRED = "expired"


def subject_key(subject: str | None) -> str | None:
    """Returns the subject as a key such as `user.employer`: lowercase, spaces as `_`, only letters, digits, `.` and
    `_`, at most MAX_SUBJECT_KEY_CHARS; None when nothing is left."""
    if subject is None:
        return None
    lowered = "_".join(subject.strip().lower().split())
    key = "".join(char for char in lowered if char.isalnum() or char in "._")[:MAX_SUBJECT_KEY_CHARS].strip("._")
    return key or None


def value_text(value: str | None) -> str | None:
    """Returns the value to store: trimmed and cut at MAX_VALUE_CHARS, or None when blank."""
    if value is None:
        return None
    return value.strip()[:MAX_VALUE_CHARS] or None


def expiry(valid_until: date | None, now: datetime) -> datetime:
    """Returns when a temporary memory expires: the end of `valid_until` (UTC), or TEMPORARY_DEFAULT from `now` when
    the date is missing or already past."""
    if valid_until is not None:
        end = datetime.combine(valid_until + timedelta(days=1), time.min, tzinfo=UTC)
        if end > now:
            return end
    return now + TEMPORARY_DEFAULT


def checked_provenance(provenance: str, kind: str, key: str | None, *, by_user: bool, by_character: bool) -> str:
    """Guard 1. Returns the provenance to store for an item the model marked `provenance`.

    A `stated` fact about the user (a user kind, or a `user.` subject) with no source message by the user becomes
    `inferred`; a `stated` `character_fact` that the character says in the chat becomes `fictional`. Anything else is
    kept.
    """
    if provenance != "stated":
        return provenance
    about_user = kind in USER_KINDS or (key or "").startswith("user.")
    if about_user and not by_user:
        return "inferred"
    if kind == "character_fact" and by_character:
        return "fictional"
    return provenance


def stem_set(text: str, language: str | None) -> frozenset[str]:
    """Returns the distinct stems of `text`, the set the similarity compares."""
    return frozenset(stems(text, language))


def jaccard(first: frozenset[str], second: frozenset[str]) -> float:
    """Returns the size of the intersection over the size of the union, and 0.0 when both sets are empty."""
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def values_differ(first: str | None, second: str | None, language: str | None) -> bool:
    """Tells whether two subject values are both given and differ once case, accents, punctuation, stop words and word
    forms are ignored ("Verdi & Co" and "verdi e co." agree)."""
    if first is None or second is None:
        return False
    return fingerprint(first, language) != fingerprint(second, language)


@dataclass(frozen=True)
class KnownMemory:
    """An existing memory as the rules see it. `source_max_seq` is the seq of its newest source message, None when it
    has none."""

    id: str
    kind: str
    content: str
    subject_key: str | None
    value_text: str | None
    provenance: str
    durability: str
    status: str
    importance: int
    locked: bool
    conflict_group_id: str | None
    fingerprint: str
    source_max_seq: int | None


@dataclass(frozen=True)
class Proposal:
    """An extracted item ready for the rules: its target and sources resolved, its text trimmed, its provenance passed
    through checked_provenance and `confidence` the stored value of that provenance."""

    relation: str
    kind: str
    content: str
    subject_key: str | None
    value_text: str | None
    provenance: str
    durability: str
    importance: int
    confidence: float
    fingerprint: str
    language: str
    has_user_source: bool
    source_max_seq: int
    change_cue: bool


@dataclass(frozen=True)
class Change:
    """One existing memory a decision changes, with the revision reason."""

    memory: KnownMemory
    reason: str


@dataclass(frozen=True)
class Decision:
    """What one item does.

    With `confirm`, the item is that memory's duplicate: it adds its sources and raises the importance (only the
    sources for a locked memory), with no new version, and nothing else changes. Otherwise a new memory is inserted
    with `status`, `provenance`, `confidence` and `needs_review`. `supersede` become `superseded` by it; `demote`
    become `uncertain` in its conflict group; `group` join that group with their status kept (a stated or confirmed
    memory an inference conflicts with). `in_conflict` puts the new memory in a group: `conflict_group_id` when one of
    the members already has one, otherwise a new one.
    """

    confirm: KnownMemory | None = None
    status: str = "active"
    provenance: str = ""
    confidence: float = 0.0
    needs_review: bool = False
    supersede: tuple[Change, ...] = ()
    demote: tuple[Change, ...] = ()
    group: tuple[Change, ...] = ()
    in_conflict: bool = False
    conflict_group_id: str | None = None


def _same_subject(first: str | None, second: str | None) -> bool:
    return first is None or second is None or first == second


def _effective(proposal: Proposal, target: KnownMemory | None) -> tuple[str, KnownMemory | None]:
    """Returns the relation the rules apply and its target, from what the model proposed.

    `new` and `temporary` ignore a target. An `identity_candidate`, and a temporary item aimed at a lasting memory,
    become `new`: they are stored next to the memory and never change it. A duplicate whose single-valued subject has
    another value is not a duplicate. A correction needs a source message by the user, and an update the same subject
    and a source newer than the target's; a correction or update that fails its check is unclear, so a contradiction,
    and an update about another subject is new information.
    """
    relation = proposal.relation
    if relation in ("new", "temporary") or target is None or proposal.kind == IDENTITY_CANDIDATE:
        return "new", None
    if relation == "duplicate":
        single = target.subject_key in SINGLE_VALUED_SUBJECTS and target.subject_key == proposal.subject_key
        if single and values_differ(target.value_text, proposal.value_text, proposal.language):
            return "new", None
        return "duplicate", target
    if proposal.durability == "temporary" and target.durability == "lasting":
        return "new", None
    if relation == "correction" and not proposal.has_user_source:
        return "contradiction", target
    if relation == "update":
        if not _same_subject(proposal.subject_key, target.subject_key):
            return "new", None
        if target.source_max_seq is not None and proposal.source_max_seq <= target.source_max_seq:
            return "contradiction", target
    return relation, target


def _duplicate_of(
    proposal: Proposal, same: KnownMemory | None, similar: Sequence[KnownMemory], excluded: str | None
) -> KnownMemory | None:
    """Returns the memory in use that the item repeats: the one with its fingerprint, otherwise the most similar one of
    the same kind and subject at SIMILARITY_THRESHOLD or above whose value does not differ. `excluded` is never
    returned: the target an update or correction replaces."""
    if same is not None and same.id != excluded:
        return same
    words = stem_set(proposal.content, proposal.language)
    best: KnownMemory | None = None
    best_score = SIMILARITY_THRESHOLD
    for memory in similar:
        if memory.id == excluded or memory.kind != proposal.kind or memory.subject_key != proposal.subject_key:
            continue
        if values_differ(memory.value_text, proposal.value_text, proposal.language):
            continue
        score = jaccard(words, stem_set(memory.content, proposal.language))
        if score > best_score or (best is None and score >= best_score):
            best, best_score = memory, score
    return best


def _guards_subject(proposal: Proposal) -> bool:
    """Guard 3 applies to a lasting fact with a single-valued subject and a value."""
    return (
        proposal.durability == "lasting"
        and proposal.kind != IDENTITY_CANDIDATE
        and proposal.subject_key in SINGLE_VALUED_SUBJECTS
        and proposal.value_text is not None
    )


def decide(
    proposal: Proposal,
    target: KnownMemory | None,
    same: KnownMemory | None,
    similar: Sequence[KnownMemory],
    rivals: Sequence[KnownMemory],
) -> Decision:
    """Returns what `proposal` does (docs/memory-engine.md, Consistency rules).

    Requires: `target` is the item's target and in use (active or uncertain), or None; `same` is the memory in use
    with the item's fingerprint, or None; `similar` are memories in use that may be near duplicates; `rivals` are the
    active lasting memories with the item's subject (guard 3), empty when it is not single-valued. All belong to the
    item's conversation.

    Order: the effective relation (_effective), then deduplication, then what the relation and guard 3 ask of each
    memory, then the lock (guard 4: the memory is left alone and the new one is `uncertain` with `needs_review`) and
    the inference limit (guard 2: the memory keeps its status and joins the group, the inference is `uncertain`). A
    correction that supersedes its target is stored `user_confirmed` with confidence 1.0.
    """
    relation, target = _effective(proposal, target)
    if relation == "duplicate":
        return Decision(confirm=target)
    replaces_target = target is not None and relation in (UPDATE, CORRECTION)
    twin = _duplicate_of(proposal, same, similar, target.id if replaces_target and target is not None else None)
    if twin is not None:
        return Decision(confirm=twin)

    wanted: list[tuple[KnownMemory, bool, str]] = []
    if target is not None:
        wanted.append((target, replaces_target, relation))
    if _guards_subject(proposal):
        for rival in rivals:
            if target is not None and rival.id == target.id:
                continue
            if values_differ(rival.value_text, proposal.value_text, proposal.language):
                reason = SUBJECT_CHANGE if proposal.change_cue else SUBJECT_CONFLICT
                wanted.append((rival, proposal.change_cue, reason))

    # A correction is the user's own statement, so the inference limit does not hold it back.
    inference = proposal.provenance == "inferred" and relation != CORRECTION
    supersede: list[Change] = []
    demote: list[Change] = []
    group: list[Change] = []
    uncertain = needs_review = False
    for memory, replaces, reason in wanted:
        if memory.locked:
            uncertain = needs_review = True
        elif inference and memory.provenance in PROTECTED_PROVENANCES:
            uncertain = True
            group.append(Change(memory, INFERENCE_CONFLICT))
        elif replaces:
            supersede.append(Change(memory, reason))
        else:
            uncertain = True
            demote.append(Change(memory, reason))

    target_replaced = target is not None and any(change.memory.id == target.id for change in supersede)
    if target is not None and not target_replaced and target.fingerprint == proposal.fingerprint:
        # The same words as a target that stays in use: the unique fingerprint index allows one, so it is a duplicate.
        return Decision(confirm=target)

    provenance, confidence = proposal.provenance, proposal.confidence
    if relation == CORRECTION and target_replaced:
        provenance, confidence = "user_confirmed", CONFIRMED_CONFIDENCE
    members = (*demote, *group)
    return Decision(
        status="uncertain" if uncertain else "active",
        provenance=provenance,
        confidence=confidence,
        needs_review=needs_review,
        supersede=tuple(supersede),
        demote=tuple(demote),
        group=tuple(group),
        in_conflict=bool(members),
        conflict_group_id=next((m.memory.conflict_group_id for m in members if m.memory.conflict_group_id), None),
    )
