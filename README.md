
# paramsight

[![CI](https://github.com/nqgl/paramsight/actions/workflows/ci.yml/badge.svg)](https://github.com/nqgl/paramsight/actions/workflows/ci.yml)

**Consistent runtime type parameter lookup + generic alias access inside classmethods**

`paramsight` enables:
- runtime type parameter lookup (e.g. extracting the `int` from a specialized generic type `MyClass[int]`).
  - queried relative to a given base class -- enabling consistent behavior relative to the base class even when many type parameters are used across the inheritance hierarchy.
- in order for that to be useful, adds a decorator that makes classmethods receive the actual generic alias (e.g., `MyClass[int]`) rather than just the bare class, so that the lookup function can be used from a classmethod.

## Requirements

- Python 3.13 or greater (maybe works on 3.12?)

## Installation
Clone the repository, then
```bash
pip install -e .
```

## Example

```python
from typing import get_args
from paramsight import takes_alias, get_args_at_base, TypeVarValue

class Box[T]:
    # a typed accessor — statically `type[T]`
    value_type = TypeVarValue[T]()

    # @takes_alias makes `cls` the actual alias (Box[int]), not the bare Box
    @takes_alias
    @classmethod
    def contained_type(cls):
        # resolved relative to a base, so subclasses get the right answer too
        return get_args_at_base(cls, Box)

assert Box[int].value_type is int                # via the descriptor (statically: type[int])
assert Box[int].contained_type() == (int,)       # via the classmethod
assert Box[int]().contained_type() == (int,)     # works on instances

class IntBox(Box[int]): ...
assert IntBox.value_type is int                  # inherited, still resolves correctly

# get_args_at_base resolves against ANY base in the hierarchy — not just the
# immediate alias, which is all typing.get_args can see:
class NestedList[T](list[list[T]]): ...
assert get_args(NestedList[int]) == (int,)                       # typing.get_args can't tell you about `list`'s args here
assert get_args_at_base(NestedList[int], list) == (list[int],)   # ...but paramsight can
assert get_args_at_base(NestedList[int], NestedList) == (int,)   # (and NestedList's own typevar)
```

`TypeVarValue` is fully understood by Pyright (and therefore Pylance, which is built on Pyright) — e.g. the checker knows `isinstance(obj, box.value_type)` narrows `obj` to that box's element type. (This is verified in the test suite with `typing.assert_type`.) See the dedicated `TypeVarValue` section below for the details, caveats, and the pydantic note. This level of static typing isn't achievable with `get_args_at_base`, which returns an untyped tuple.

## Features

- `get_args_at_base` — resolve a base's type parameters from anywhere in the hierarchy (like `typing.get_args`, but for an ancestor base)
- `@takes_alias` — make classmethods receive the generic alias (`Foo[int]`) instead of the bare class
- `get_typevar_value` — resolve a single, named typevar of a base by identity
- `TypeVarValue` — a property-style descriptor that exposes a class's resolved typevar, statically typed `type[T]` (and `TypeVarValueOption`, its `type[T] | None` sibling)

(There's also `GenericBaseModel` — an experimental *application* built on top of the above, not a core primitive. See [its section below](#genericbasemodel-experimental-application).)

### `@takes_alias`
The `@takes_alias` decorator transforms classmethods to receive the specialized generic alias:
```python
from paramsight import takes_alias, get_args_at_base

class Base[T]:
    @takes_alias
    @classmethod
    def get_type_info(cls):
        # Get the actual type parameter
        return get_args_at_base(cls, Base)

class Derived[T](Base[T]):...

# Both work correctly
print(Base[str].get_type_info())     # (str,)
print(Derived[int].get_type_info())  # (int,)
```

### `get_args_at_base`: TypeVar Resolution

Get resolved type parameters from complex inheritance hierarchies:
```python
from paramsight import get_args_at_base

class A[T]: ...
class B[X, Y](A[Y]): ...  
class C[Z](B[str, Z]): ...

# Resolves the type parameter that flows to A
print(get_args_at_base(C[int], A))  # (int,)
```

> Think of `get_args_at_base(cls, base)` as `typing.get_args`, but evaluated at an ancestor base rather than at the immediate generic alias.
> `get_typevar_value(cls, base, typevar)` looks up a *single* typevar of `base` by identity instead of returning all of them positionally.

### `TypeVarValue`: a typed accessor for a class's resolved typevar

`TypeVarValue` is a property-style descriptor. Put it on a generic class, parameterized with one of that class's own typevars, and reading the attribute on a specialization gives you the resolved type — with the static type narrowed to `type[T]`:

```python
from paramsight import TypeVarValue

class Box[T]:
    value_type = TypeVarValue[T]()

assert Box[int].value_type is int      # the type checker sees: type[int]
assert Box[str].value_type is str      # the type checker sees: type[str]
assert Box[int]().value_type is int    # works on instances too
```

Because the attribute can only be reached *through* a class (or one of its subclasses / specializations), the class you reach it through is — by construction — the one whose specialization gets resolved. There's no separate "target" argument to wire wrong, so the static type (derived from that same receiver) can't disagree with the runtime answer.

Inheritance and nested specializations work without ceremony:

```python
class CfgBase[X]: ...
class ArchBase[CfgT]:
    cfg_type = TypeVarValue[CfgT]()
class Arch[T](ArchBase[CfgBase[T]]): ...

assert Arch[int].cfg_type == CfgBase[int]   # resolves the nested CfgBase[...]
```

A worked example — a typed `isinstance` gate:

```python
class Validated[T]:
    expected_type = TypeVarValue[T]()

    @takes_alias                       # so cls is the alias, e.g. Validated[int]
    @classmethod
    def ensure(cls, val: object) -> T:
        assert isinstance(val, cls.expected_type)
        return val

Validated[int].ensure(3)               # -> 3
Validated[int].ensure("nope")          # AssertionError
```

Notes:
- Use it alongside `@takes_alias` methods, or read it directly on aliases. Inside a *plain* `@classmethod`, `cls` is the bare class, so the descriptor would see no specialization — the same constraint `@takes_alias` already has.
- A typevar **default** resolves the same as an explicit argument. The raw `__default__` is stored in *source* form (`T = None` keeps the `None` singleton, `T = "Fwd"` keeps the bare string), so paramsight normalizes it exactly the way subscription does — `None` → `NoneType`, `"Fwd"` → `ForwardRef('Fwd')`. The upshot is that `Box` and `Box[None]` never disagree:
  ```python
  class Box[T = None]:
      value_type = TypeVarValue[T]()

  assert Box.value_type is Box[None].value_type   # both NoneType
  ```
- On a bare, unspecialized generic class with **no** typevar default, the typevar is genuinely unresolved — `TypeVarValue` promises `type[T]`, so it raises `LookupError` rather than hand back a sentinel. If "unresolved" is a state you want to *handle* instead of an error, use [`TypeVarValueOption`](#typevarvalueoption) below. (The low-level `get_args_at_base` / `get_typevar_value` still return `typing.NoDefault` here — the sentinel lives at the reflection layer; the descriptors are the boundary that turns it into a raise or a `None`.)
- On a pydantic `BaseModel`, register the descriptor so pydantic doesn't treat it as a field:
  ```python
  class MyModel[T](BaseModel):
      model_config = ConfigDict(ignored_types=(TypeVarValue,))
      value_type = TypeVarValue[T]()
  ```
- The type argument must be one of the owning class's own `TypeVar`s; otherwise `TypeVarValue` raises `TypeError` at class-definition time (for PEP 695 classes; for old-style `Generic[T]` classes the same error surfaces at first access).

The untyped equivalent is `get_typevar_value(cls, base, typevar)`.

#### `TypeVarValueOption`

Same descriptor, typed `type[T] | None` instead of `type[T]`. Where `TypeVarValue` *raises* on an unbound typevar with no default, `TypeVarValueOption` yields `None`, making "unresolved" a first-class state you check with the canonical optional idiom:

```python
from paramsight import TypeVarValueOption

class Box[T]:
    value_type = TypeVarValueOption[T]()

assert Box[int].value_type is int      # the type checker sees: type[int] | None
assert Box.value_type is None          # T unbound, no default

t = Box[int].value_type
if t is not None:                      # narrows to type[int]
    ...
```

The `None` is unambiguous: because a default of `None` resolves to `NoneType` (a truthy class), the `None` singleton never appears as a resolved value — so `None` from the descriptor means exactly "nothing to resolve." The trade-off is the usual optional tax: every read site sees `type[T] | None` and must narrow, even where the typevar is plainly bound. Reach for `TypeVarValue` unless you genuinely branch on absence.

### Compatibility

Written/tested for compatibility with:

- **Pydantic**
- **attrs** 
- **PyTorch nn.Module**
- **Plain Python classes**


## `GenericBaseModel` (experimental application)

> **Status:** an application built on top of the library, not a core primitive. It's a neat demonstration of what's possible — but it touches more moving parts (pydantic validation hooks, dynamic type re-import on load); read the caveats before relying on it.

A plain pydantic generic model loses its parameterization when serialized — `MyModel[int](...).model_dump()` round-trips back through `MyModel.model_validate(...)` as the *unspecialized* model. `GenericBaseModel` fixes that: it serializes the resolved type parameters alongside the data, and when you validate the bare class it dispatches to the right specialized alias.

```python
from paramsight.generic_restored_basemodel.generic_basemodel import GenericBaseModel

class Pair[A, B](GenericBaseModel):
    a: A
    b: B

original = Pair[int, str](a=1, b="x")
restored = Pair.model_validate(original.model_dump())
assert restored == original                 # the [int, str] specialization survived
assert type(restored) is Pair[int, str]
```

The type info rides along under a `generic_type` field (a tuple of `TypeRef`s — module + qualified name, recursively for nested generic args). Models with no generic parameters serialize nothing extra. Built on `@takes_alias` + `get_args_at_base`.

**Caveats:**
- The bare model dispatches to a specialized alias via a `@model_validator(mode="wrap")` that reconstructs `GenericBaseModel[...]` from the carried type info and re-validates against it. This uses only public pydantic APIs (`model_validator`, `__class_getitem__`, `model_validate`), so it isn't especially fragile — but the wrap validator is inherited, so it adds a (cheap) layer of indirection to every subclass's validation.
- **Don't deserialize untrusted input with it.** A `TypeRef` is `module + qualified name`, and loading one calls `importlib.import_module` + `getattr` — so a malicious payload can trigger arbitrary imports / attribute lookups *before* the declared-parameter `issubclass`/`isinstance` check runs.
- A `TypeRef` also keeps a `source_backup` and warns (or, in strict mode, raises) if the referenced object's source code has changed since serialization. Useful for catching drift, but an unusual behavior to be aware of.
- Not re-exported from the package root — import it from `paramsight.generic_restored_basemodel.generic_basemodel`.

## Super() Injection

By default, inside a `@takes_alias` decorated method, `super()` will not work. However, `paramsight` can address this by injecting a patched `super` function. To enable this injection, use `@takes_alias(patch_super=True)`.

**`@takes_alias` can automatically inject a custom `super` implementation into decorated methods' local scope.** This enables `super()` calls to work correctly with generic aliases but means the `super` in your method is not the built-in:
```python
class Parent[T]:
    @takes_alias
    @classmethod  
    def method(cls):
        print(f"parent sees {cls}")
        return "parent"

class Child[T](Parent[T]):
    @takes_alias(patch_super=True)
    @classmethod
    def method(cls):
        # This 'super' is NOT the built-in super!
        # It's automatically injected by @takes_alias
        result = super().method()  # Works with generic aliases
        return f"child + {result}"

Child[int].method()  
# > parent sees Child[int]
# Returns: "child + parent"
```

**Why this is necessary:** Standard `super()` errors when recieving a generic alias, so we provide a compatible version that maintains the generic context through inheritance chains.

The alternative would require handling the cases manually, and is ugly:
```py
class Base[T]:
  @takes_alias
  @classmethod
  def method(cls, arg): ...

class C[T](Base):
  @takes_alias
  @classmethod
  def method(cls, arg):
    if isinstance(cls, type):
      result = super().method(arg)
    elif is_aliasclassmethod(super(C, cls.__origin__).method.__func__)
      result = super(C, cls.__origin__).method.__func__(cls, arg)
    else:
      result = super(C, cls.__origin__).method(arg)
```

### Other Considerations

1. **Source Code Required**: The `super` injection needs to do AST rewriting and may not work with compiled/cython extensions.
2. There may be considerations when using it in a multithreaded context.

## How It Works

- **Class-creation hook**: `@takes_alias` produces a *descriptor* — a subclass of `classmethod`. When its `__set_name__` runs as the owning class is created, the class's existing `__init_subclass__` and `__class_getitem__` are wrapped: `__init_subclass__` so the behavior is reinstalled on subclasses, and `__class_getitem__` so it returns a custom generic-alias proxy (a subclass of `typing._GenericAlias`) — except on pydantic models, which are left unmodified. A `TypeVarValue` descriptor triggers the same wrapping from *its* `__set_name__`, so a class can opt in via either.
- **Generic-alias proxy**: wraps `Foo[int]`-style aliases in a proxy that intercepts attribute access — if the accessed attribute is one of the alias-aware descriptors (`@takes_alias` methods or `TypeVarValue`), it hands over the proxy alias; otherwise behavior is unchanged.
- **AST rewriting to patch `super()`** (only with `patch_super=True`): modifies decorated methods to inject the custom `super` by recompiling the function with updated local variables.


## API Reference

### Runtime TypeVar Resolution

- `get_args_at_base(cls, base, return_bound_as_fallback=False)` — resolved type parameters of `base`, as seen from `cls`, positionally (like `typing.get_args`, but for an ancestor base)
- `get_typevar_value(cls, base, typevar, return_bound_as_fallback=False)` — resolve one typevar of `base` by identity (untyped; returns `Any`)
- `TypeVarValue[T]()` — property-style descriptor on a generic class; reading it yields the resolved value of `T`, statically typed `type[T]`; raises `LookupError` if `T` is unbound with no default
- `TypeVarValueOption[T]()` — same, but statically typed `type[T] | None` and yields `None` (instead of raising) when `T` is unbound with no default

### Accessing Generic Information in Classmethods

- `@takes_alias` — makes a classmethod receive generic aliases instead of bare classes
- `@takes_alias(patch_super=True)` — additionally injects a generic-aware `super` (see [Super() Injection](#super-injection))


### Pydantic helpers

- `paramsight.generic_restored_basemodel.generic_basemodel.GenericBaseModel` — pydantic `BaseModel` whose subclasses preserve their type parameters through serialization

## Limitations

This library leans on the details of Python 3.13's generics machinery. Use with appropriate caution in production systems.

**General:**
- Python 3.13+ (may work on 3.12).
- Not optimized for speed — resolution walks the inheritance tree (results are cached via `functools.cache`, so it's unbounded; an `lru_cache` swap is trivial if that matters to you). If performance is relevant for your use, please let me know.
- The generic-alias proxy intercepts attribute access on `Foo[int]`-style aliases. It's meant to be transparent, but it is a wrapper rather than a real `typing._GenericAlias`.

**When using `super()` injection (`@takes_alias(patch_super=True)`):**
- Requires source-code access
- The AST rewriting may have unforeseen interactions

The core — `get_args_at_base` / `get_typevar_value`, `@takes_alias` *without* `patch_super`, and `TypeVarValue` — does no AST rewriting and does not need source access.

