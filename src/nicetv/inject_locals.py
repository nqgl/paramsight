import ast
import functools
import inspect
import textwrap
import types
import uuid


def _parse_function_absolute(fn: object) -> tuple[ast.FunctionDef, ast.Module]:
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


def _strip_our_decorator(fdef: ast.FunctionDef, decorator_name: str) -> None:
    idx = -1
    for i, dec in enumerate(fdef.decorator_list):
        func = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(func, ast.Name) and func.id == decorator_name:
            idx = i
            break
    if idx == -1:
        raise RuntimeError("Could not locate the inject_locals decorator.")
    fdef.decorator_list.pop(idx)


from typing import Callable, overload


def inject_locals(
    *,
    _decorator_name: str = "inject_locals",
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
        if check_function_already_injected(fn):
            return fn

        try:
            src = inspect.getsource(fn)
        except OSError as e:
            raise RuntimeError("Source not available; cannot inject locals.") from e

        src = textwrap.dedent(src)
        # fdef = next((n for n in mod.body if isinstance(n, ast.FunctionDef)), None)
        fdef, _ = _parse_function_absolute(fn)

        if fdef is None or fdef.name != fn.__name__:
            raise RuntimeError("Could not locate the function definition to rewrite.")

        # Remove our decorator so the regenerated function doesn't recurse.
        _strip_our_decorator(fdef, _decorator_name)
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
        if had_class_freevar:
            # harmless read so the compiler emits a __class__ freevar
            touch = ast.Expr(value=ast.Name(id="__class__", ctx=ast.Load()))
            fdef.body.insert(0, ast.copy_location(touch, anchor))

        # Prepend prologue *after* the __class__ touch
        #   (so traces still land on real lines)
        fdef.body = prologue + fdef.body
        fdef.decorator_list = []  # strip others; we'll rewrap later

        # ---- Compile with accurate linenos ----
        if had_class_freevar:
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
        else:
            mod2 = ast.Module(body=[fdef], type_ignores=[])

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

        # new_globals = dict(fn.__globals__)
        # new_globals[inj_check_key] = True

        # # Anchor for locations
        # anchor: ast.AST = fdef.body[0] if fdef.body else fdef

        # # Build prologue with locations
        # salt = uuid.uuid4().hex
        # prologue: list[ast.stmt] = []
        # for local_name, obj in bindings.items():
        #     gname = f"_inj_{local_name}_{salt}"
        #     new_globals[gname] = obj

        #     assign = ast.Assign(
        #         targets=[ast.Name(id=local_name, ctx=ast.Store())],
        #         value=ast.Name(id=gname, ctx=ast.Load()),
        #         type_comment=None,
        #     )
        #     assign = ast.copy_location(assign, anchor)
        #     prologue.append(assign)

        # had_class_freevar = "__class__" in fn.__code__.co_freevars

        # # If we need the __class__ cell, ensure the function body references it
        # if had_class_freevar:
        #     cls_ref = ast.Expr(value=ast.Name(id="__class__", ctx=ast.Load()))
        #     cls_ref = ast.copy_location(cls_ref, anchor)
        #     fdef.body.insert(0, cls_ref)

        # # Prepend prologue
        # fdef.body = prologue + fdef.body

        # # We’ll compile either as a top-level function or inside a dummy class
        # ns: dict[str, object] = {}
        # if had_class_freevar:
        #     # Strip any remaining decorators during the temporary compile
        #     fdef.decorator_list = []

        #     dummy_cls_name = f"_InjHost_{uuid.uuid4().hex}"
        #     cls = ast.ClassDef(
        #         name=dummy_cls_name,
        #         bases=[],
        #         keywords=[],
        #         body=[fdef],
        #         decorator_list=[],
        #     )
        #     cls = ast.copy_location(cls, fdef)  # give the class a location

        #     mod2 = ast.Module(body=[cls], type_ignores=[])
        #     ast.fix_missing_locations(mod2)

        #     code = compile(
        #         mod2,
        #         filename=inspect.getsourcefile(fn) or "<ast>",
        #         mode="exec",
        #     )
        #     exec(code, new_globals, ns)
        #     tmp_cls = ns[dummy_cls_name]
        #     tmp = tmp_cls.__dict__[fn.__name__]
        # else:
        #     fdef.decorator_list = []
        #     mod2 = ast.Module(body=[fdef], type_ignores=[])
        #     ast.fix_missing_locations(mod2)

        #     code = compile(
        #         mod2,
        #         filename=inspect.getsourcefile(fn) or "<ast>",
        #         mode="exec",
        #     )
        #     exec(code, new_globals, ns)
        #     tmp = ns[fn.__name__]

        # Rebuild function, preserving the original closure if needed
        if had_class_freevar:
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
        else:
            new_fn = tmp
            new_fn.__defaults__ = fn.__defaults__

        new_fn.__kwdefaults__ = fn.__kwdefaults__
        new_fn.__annotations__ = dict(getattr(fn, "__annotations__", {}))
        new_fn.__qualname__ = fn.__qualname__
        return functools.update_wrapper(new_fn, fn)

    @overload
    def decorator[**P, R](obj: classmethod) -> classmethod: ...
    @overload
    def decorator[**P, R](obj: staticmethod) -> staticmethod: ...
    @overload
    def decorator[**P, R](obj: Callable[P, R]) -> Callable[P, R]: ...
    @overload
    def decorator[**P, R](obj: object) -> object: ...
    def decorator[**P, R](obj: object) -> object:
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
