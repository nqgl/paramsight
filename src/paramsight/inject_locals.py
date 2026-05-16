import ast
import functools
import inspect
import textwrap
import types
import uuid
from collections.abc import Callable
from typing import overload


def _parse_function_absolute(
    fn: types.FunctionType,
) -> tuple[ast.FunctionDef, ast.Module]:
    # 1) Get source + absolute starting line
    lines, start_line = inspect.getsourcelines(fn)  # raises OSError if unavailable
    src = textwrap.dedent("".join(lines))

    # 2) Parse, then shift all node line numbers to absolute positions
    mod = ast.parse(src, filename=inspect.getsourcefile(fn) or "<ast>")
    ast.increment_lineno(mod, start_line - 1)

    # 3) Locate the exact def
    fdef = next((n for n in mod.body if isinstance(n, ast.FunctionDef)), None)
    if fdef is None or fdef.name != fn.__name__:
        raise RuntimeError("Could not locate the function definition to rewrite.")
    return fdef, mod


def _strip_our_decorators(
    fdef: ast.FunctionDef,
    decorator_names: list[str] | tuple[str, ...],  # | Callable[[str], bool] ?
) -> None:
    idx = -1
    decorator_name = decorator_names[0]
    fdec_names = [
        dec.func.id
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
        else dec.id
        if isinstance(dec, ast.Name)
        else None
        for dec in fdef.decorator_list
    ]
    for i, dec in enumerate(fdef.decorator_list):
        func = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(func, ast.Name) and func.id == decorator_name:
            idx = i
            break
    if idx == -1:
        raise RuntimeError("Could not locate the inject_locals decorator.")
    selected_decorators = fdec_names[idx : idx + len(decorator_names)]
    if tuple(selected_decorators) != tuple(decorator_names):
        raise RuntimeError(
            f"Could not locate the expceted decorators for inject_locals to remove."
            f"expected {decorator_names}, got {selected_decorators}"
        )
    fdef.decorator_list = fdef.decorator_list[idx + len(decorator_names) :]


def inject_locals(
    *,
    _decorator_names: list[str] | tuple[str, ...] = ("inject_locals",),
    **bindings,
):
    inj_check_salt = uuid.uuid4().hex
    inj_check_key = f"_injected_locals{inj_check_salt}"

    def check_function_already_injected(fn: object) -> bool:
        if hasattr(fn, inj_check_key):
            return True
        try:
            setattr(fn, inj_check_key, True)
        except Exception:
            pass
        return False

    def _decorate_function(fn: types.FunctionType) -> types.FunctionType:
        # Defensively unwrap functools.wraps-style chains, but only while the
        # wrapped object is itself a plain function. We rebuild below via
        # fn.__code__ / __globals__ / __closure__, which only exist on a
        # FunctionType; if __wrapped__ is a method / partial / other callable,
        # stop and operate on what we have rather than asserting/crashing.
        while isinstance(
            (wrapped := getattr(fn, "__wrapped__", None)), types.FunctionType
        ):
            fn = wrapped
        if check_function_already_injected(fn):  # TODO Remove?
            return fn

        try:
            src = inspect.getsource(fn)
        except OSError as e:
            raise RuntimeError("Source not available; cannot inject locals.") from e

        src = textwrap.dedent(src)
        fdef, _ = _parse_function_absolute(fn)

        if fdef is None or fdef.name != fn.__name__:
            raise RuntimeError("Could not locate the function definition to rewrite.")

        # Remove our decorator so the regenerated function doesn't recurse.
        _strip_our_decorators(fdef, _decorator_names)
        mod_globals = fn.__globals__
        reg_name = "_inj_registry"
        registry: dict[str, object] = mod_globals.setdefault(reg_name, {})

        # Anchor for locations (keeps tracebacks pointing to real lines)
        anchor: ast.AST = fdef.body[0] if fdef.body else fdef

        # Build prologue with absolute locations
        reg_key = f"{fn.__qualname__}:{uuid.uuid4().hex}"
        registry[reg_key] = dict(bindings)

        prologue: list[ast.stmt] = []
        for local_name in bindings:
            assign = ast.Assign(
                targets=[ast.Name(id=local_name, ctx=ast.Store())],
                value=ast.Subscript(
                    value=ast.Subscript(
                        value=ast.Name(id=reg_name, ctx=ast.Load()),
                        slice=ast.Constant(reg_key),
                        ctx=ast.Load(),
                    ),
                    slice=ast.Constant(local_name),
                    ctx=ast.Load(),
                ),
            )
            prologue.append(ast.copy_location(assign, anchor))

        had_class_freevar = "__class__" in fn.__code__.co_freevars
        assert had_class_freevar
        # harmless read so the compiler emits a __class__ freevar
        touch = ast.Expr(value=ast.Name(id="__class__", ctx=ast.Load()))
        fdef.body.insert(0, ast.copy_location(touch, anchor))

        # Prepend prologue *after* the __class__ touch
        #   (so traces still land on real lines)
        fdef.body = prologue + fdef.body

        # ---- Compile with accurate linenos ----

        dummy_cls = ast.ClassDef(
            name=f"__InjHost_{uuid.uuid4().hex}",
            bases=[],
            keywords=[],
            body=[fdef],
            decorator_list=[],
        )
        ast.copy_location(
            dummy_cls, fdef
        )  # class gets same starting line as the method
        mod2 = ast.Module(body=[dummy_cls], type_ignores=[])

        ast.fix_missing_locations(mod2)

        code = compile(
            mod2,
            filename=inspect.getsourcefile(fn) or "<ast>",  # shows up in tracebacks
            mode="exec",
        )
        ns: dict[str, object] = {}
        exec(code, mod_globals, ns)

        tmp = (
            ns[dummy_cls.name].__dict__[fn.__name__]  # method inside dummy class
            if had_class_freevar
            else ns[fn.__name__]
        )
        assert isinstance(tmp, types.FunctionType)

        # Rebuild function, preserving the original closure if needed

        if "__class__" not in tmp.__code__.co_freevars:
            raise RuntimeError(
                "Rewritten function lost the __class__ freevar; "
                "ensure the AST references __class__ at least once."
            )
        if fn.__closure__ is None:
            raise RuntimeError(
                "Original function had __class__ freevar but no closure."
            )
        new_fn = types.FunctionType(
            tmp.__code__,
            mod_globals,
            name=fn.__name__,
            argdefs=fn.__defaults__,
            closure=fn.__closure__,
        )

        new_fn.__kwdefaults__ = fn.__kwdefaults__
        new_fn.__annotations__ = dict(getattr(fn, "__annotations__", {}))
        new_fn.__qualname__ = fn.__qualname__
        f = functools.update_wrapper(new_fn, fn)
        assert f is new_fn
        return new_fn

    @overload
    def decorator(obj: classmethod) -> classmethod: ...
    @overload
    def decorator(obj: staticmethod) -> staticmethod: ...
    @overload
    def decorator[**P, R](obj: Callable[P, R]) -> Callable[P, R]: ...
    @overload
    def decorator(obj: object) -> object: ...
    def decorator(obj: object) -> object:
        if isinstance(obj, classmethod):
            inner = _decorate_function(obj.__func__)
            return classmethod(inner)
        if isinstance(obj, staticmethod):
            inner = _decorate_function(obj.__func__)
            return staticmethod(inner)
        if isinstance(obj, types.FunctionType):
            return _decorate_function(obj)
        raise TypeError(
            "@inject_locals can only decorate functions, classmethods, or staticmethods"
        )

    return decorator
