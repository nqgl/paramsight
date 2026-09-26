import inspect
import sys
import typing
from collections.abc import Callable

import pytest

from paramsight.generic_restored_basemodel.typeref import ObjRef


def reference_factory() -> int:
    raise AssertionError("Loading a reference must not invoke its factory")


class NonCallable:
    pass


NON_CALLABLE = NonCallable()


@pytest.mark.parametrize(
    "reference_type",
    [
        ObjRef[Callable[[], int]],
        ObjRef[typing.Callable[[], int]],
        ObjRef[Callable[..., object]],
        ObjRef[typing.Callable],
        ObjRef[Callable],
    ],
)
def test_callable_references_load_and_roundtrip_without_invocation(reference_type):
    ref = reference_type.from_obj(reference_factory)
    assert ref.get_obj() is reference_factory
    restored = reference_type.model_validate_json(ref.model_dump_json())
    assert restored.get_t() == ref.get_t()
    assert restored.get_obj(strict=True) is reference_factory


def test_parameterized_callable_rejects_noncallable_object():
    ref = ObjRef[Callable[[], int]](
        module=__name__,
        obj_name="NON_CALLABLE",
        source_backup=inspect.getsource(sys.modules[__name__]),
    )
    with pytest.raises(ValueError, match="loaded object is not of type"):
        ref.get_obj()


def test_callable_check_is_not_signature_validation():
    # Constructors are callable too. Neither class annotations nor its
    # constructor are inspected/invoked to promise argument or return types.
    ref = ObjRef[Callable[[], object]].from_obj(NonCallable)
    assert ref.get_obj() is NonCallable


def test_concrete_type_guard_remains_enforced():
    ref = ObjRef[int].from_obj(reference_factory)
    with pytest.raises(ValueError, match="loaded object is not of type"):
        ref.get_obj()


def test_type_reference_guard_remains_enforced():
    assert ObjRef[type[NonCallable]].from_obj(NonCallable).get_obj() is NonCallable
    with pytest.raises(ValueError, match="loaded object is not of type"):
        ObjRef[type[int]].from_obj(NonCallable).get_obj()


def test_callable_source_drift_still_warns_or_fails_strictly():
    ref = ObjRef[Callable[[], int]].from_obj(reference_factory)
    ref.source_backup = "older source"
    with pytest.warns(UserWarning, match="appears to have changed"):
        assert ref.get_obj() is reference_factory
    with (
        pytest.warns(UserWarning, match="appears to have changed"),
        pytest.raises(ValueError, match="source code has changed"),
    ):
        ref.get_obj(strict=True)
