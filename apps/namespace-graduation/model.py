"""The authoritative in-memory object model, and the diff every equality assertion reports through."""

import math
from dataclasses import dataclass, field
from typing import Any

VECTOR_REL_TOL = 1e-6
VECTOR_ABS_TOL = 1e-6


@dataclass(frozen=True)
class ExpectedObject:
    uuid: str
    properties: dict[str, Any]
    vectors: dict[str, list[float]]


@dataclass(frozen=True)
class Diff:
    kind: str  # missing | unexpected | property_mismatch | vector_mismatch
    collection: str
    uuid: str
    field: str = ""
    expected: Any = None
    actual: Any = None

    def render(self) -> str:
        base = f"{self.kind} collection={self.collection} id={self.uuid}"
        if self.field:
            return f"{base} field={self.field} expected={self.expected!r} actual={self.actual!r}"
        return base


def vectors_equal(a: dict[str, list[float]], b: dict[str, list[float]]) -> bool:
    """Vectors round-trip through float32 storage, so equality is elementwise isclose."""
    if set(a) != set(b):
        return False
    for name, left in a.items():
        right = b[name]
        if len(left) != len(right):
            return False
        for x, y in zip(left, right):
            if not math.isclose(x, y, rel_tol=VECTOR_REL_TOL, abs_tol=VECTOR_ABS_TOL):
                return False
    return True


def normalise_vectors(obj_json: dict[str, Any]) -> dict[str, list[float]]:
    """Map a raw REST object onto the model's vector shape.

    `vectors` passes through and wins when both forms are present; a legacy top-level `vector`
    becomes the named default. The client side needs no normalisation: obj.vector is already a dict.
    """
    named = obj_json.get("vectors")
    if isinstance(named, dict) and named:
        return {name: list(values) for name, values in named.items()}
    legacy = obj_json.get("vector")
    if legacy:
        return {"default": list(legacy)}
    return {}


@dataclass
class ExpectedSet:
    """One collection's expected contents. Exactly one writer: its load task, or the seeder.

    Mutations are recorded only after the server acknowledges them.
    """

    collection: str
    _objects: dict[str, ExpectedObject] = field(default_factory=dict)

    def record_insert(self, obj: ExpectedObject) -> None:
        self._objects[obj.uuid] = obj

    def record_update(self, obj: ExpectedObject) -> None:
        self._objects[obj.uuid] = obj

    def record_delete(self, uuid: str) -> None:
        self._objects.pop(uuid, None)

    def snapshot(self) -> dict[str, ExpectedObject]:
        return dict(self._objects)

    def uuids(self) -> list[str]:
        return list(self._objects)

    def __len__(self) -> int:
        return len(self._objects)

    def diff(self, actual: dict[str, ExpectedObject]) -> list[Diff]:
        diffs: list[Diff] = []
        expected = self.snapshot()
        for uuid, want in expected.items():
            have = actual.get(uuid)
            if have is None:
                diffs.append(Diff("missing", self.collection, uuid))
                continue
            # Two-sided: a property the model never wrote is a divergence, not a detail.
            for name in sorted(set(want.properties) | set(have.properties)):
                if have.properties.get(name) != want.properties.get(name):
                    diffs.append(
                        Diff(
                            "property_mismatch",
                            self.collection,
                            uuid,
                            name,
                            want.properties.get(name),
                            have.properties.get(name),
                        )
                    )
            if not vectors_equal(want.vectors, have.vectors):
                diffs.append(
                    Diff(
                        "vector_mismatch",
                        self.collection,
                        uuid,
                        "vectors",
                        {k: len(v) for k, v in want.vectors.items()},
                        {k: len(v) for k, v in have.vectors.items()},
                    )
                )
        for uuid in actual:
            if uuid not in expected:
                diffs.append(Diff("unexpected", self.collection, uuid))
        return diffs
