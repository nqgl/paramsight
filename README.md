
# paramsight

**Make classmethods aware of generic aliases in Python 3.13+**

`paramsight` enables:
- runtime type parameter lookup, located on the current class relative to some base class -- enabling consistent behavior relative to the base class even when many type parameters are used across the inheritance hierarchy. 
- in order for that to be useful, adds a decorator that makes classmethods receive the actual generic alias (e.g., `MyClass[int]`) rather than just the bare class, so that the lookup function can be used from a classmethod

## Requirements

- Python 3.13 or greater (maybe works on 3.12?)

## Installation
clone then
```bash
pip install -e .
```

## Quick Start
```python
from paramsight import takes_alias, get_resolved_typevars_for_base

class Container[T = str]:
    @takes_alias
    @classmethod
    def describe(cls):
        print(f"I am {cls}")
        # cls is the actual Container[int], not just Container


    @takes_alias
    @classmethod
    def get_contained_type(cls):
        return get_resolved_typevars_for_base(cls, Container) # searches relative to the given base (Container), so it will still find the correct value when searching from subclasses
        
# This now works!
Container[int].describe()  # Output: I am Container[int]
assert Container[int].get_contained_type() == (int,)
assert Container.get_contained_type() == (str,) # resolves defaults

# also works on instances
assert Container[int]().get_contained_type() == (int,)
assert Container().get_contained_type() == (str,)
```

## Features

- takes_alias
- get_resolved_typevars_for_base

### `@takes_alias`
The `@takes_alias` decorator transforms classmethods to receive the specialized generic alias:
```python
from paramsight import takes_alias, get_resolved_typevars_for_base

class Base[T]:
    @takes_alias
    @classmethod
    def get_type_info(cls):
        # Get the actual type parameter
        return get_resolved_typevars_for_base(cls, Base)

class Derived[T](Base[T]):
    pass

# Both work correctly
print(Base[str].get_type_info())     # (str,)
print(Derived[int].get_type_info())  # (int,)
```

### `get_resolved_typevars_for_base`: TypeVar Resolution

Get resolved type parameters from complex inheritance hierarchies:
```python
from paramsight import get_resolved_typevars_for_base

class A[T]: pass
class B[X, Y](A[Y]): pass  
class C[Z](B[str, Z]): pass

# Resolves the type parameter that flows to A
print(get_resolved_typevars_for_base(C[int], A))  # (int,)
```
### Compatibility

Written/tested for compatibility with:

- **Pydantic**
- **attrs** 
- **PyTorch nn.Module**
- **Plain Python classes**


## ⚠️ Super() Injection Behavior

**`@takes_alias` automatically injects a custom `super` implementation into decorated methods' local scope.** This enables `super()` calls to work correctly with generic aliases but means the `super` in your method is not the built-in:
```python
class Parent[T]:
    @takes_alias
    @classmethod  
    def method(cls):
        print(f"parent sees {cls}")
        return "parent"

class Child[T](Parent[T]):
    @takes_alias
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

**Why this is necessary:** Standard `super()` doesn't errors when recieving a generic alias, so we provide a compatible version that maintains the generic context through inheritance chains.

The alternative looks like this:
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
yeah, I'll pass.

This can be disabled by setting the skip_super_injection flag when calling takes_alias.

### Other Considerations

1. **Source Code Required**: The decorator needs access to source code for AST rewriting - won't work with compiled/cython extensions
2. **Python 3.13+ Only**: Uses new generic syntax and internals introduced in Python 3.13
3. **Performance**: Minimal overhead for class creation, but there is some introspection cost at decoration time
4. **Threading**: The decorator modifications happen at import time and are thread-safe thereafter

## How It Works

takes_alias
- When `__set_name__`  is called on the decorator, the class's existing `__init_subclass__` and `__class_getitem__` are wrapped. `__init_subclass__` is wrapped to reinstall the behavior on subclasses, while `__class_getitem__` is wrapped to return a custom generic alias proxy subclass of typing._GenericAlias (except on pydantic models -- those are unmodified)
- **Generic Alias Proxy**: Wraps generic aliases in a proxy that intercepts attribute access, checks if accessed attribute is one of the decorated takes_alias methods -- if so, pass in the proxy alias, otherwise retain normal behavior.
- **AST Rewriting**: Modifies decorated methods to inject the custom `super` by recompiling the function with updated local variables


### Complex Inheritance
```python
class Multi[T1, T2]: 
    @takes_alias
    @classmethod
    def which_types(cls):
        return get_resolved_typevars_for_base(cls, Multi)

class Derived[X](Multi[X, str]):
    pass

print(Derived[int].which_types())  # (int, str)
```

## API Reference

### Decorators

- `@takes_alias` - Makes a classmethod receive generic aliases instead of bare classes

### Type Resolution

- `get_resolved_typevars_for_base(cls, base_class)` - Get resolved type parameters for a base class

## Limitations

This library is on the edge of what's possible with Python 3.13's enhanced generics system. Use with appropriate caution in production systems.

- No support for Python < 3.13
- Requires source code access (no compiled extensions)
- The AST rewriting approach may interact unexpectedly with other decorators that do similar transformations (possibly even just with other decorators?)
- Nothing has been optimized for speed -- if this becomes actually relevant for anyone, please let me know.

