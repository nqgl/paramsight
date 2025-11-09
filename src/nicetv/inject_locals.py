import ast
import functools
import inspect
import textwrap
import types
import uuid

_global_salt = uuid.uuid4().hex
_global_inject_locals_name = f"inject_locals_a2a0e5bac6c14b19b51b54615cb6ec2f"


def inject_locals_a2a0e5bac6c14b19b51b54615cb6ec2f(
    *,
    _decorator_name: str = "inject_locals",
    **bindings,
):
    """
    Create a new function equivalent to the original but with local assignments
    inserted at the top of the body for the given bindings.

    Example:
        @inject_locals(debug=True)
        def f(x):
            if debug:
                print("x =", x)
            return x * 2
    """
    inj_check_salt = uuid.uuid4().hex
    inj_check_key = f"_injected_locals{inj_check_salt}"

    def check_function_already_injected(fn):
        if hasattr(fn, inj_check_key):
            return True
        setattr(fn, inj_check_key, True)
        return False

    def decorator(fn):
        if check_function_already_injected(fn):
            return fn

        # Get and parse the original function source
        try:
            src = inspect.getsource(fn)
            # if fn.__module__ == "__main__":
            #     import __main__

            #     src = inspect.getsource(__main__)
            # inspect.getsource(fn)[-100:]
            # inspect.getsourcelines(fn)[0][-1:]
        except OSError as e:
            raise RuntimeError("Source not available; cannot inject locals.") from e

        src = textwrap.dedent(src)
        mod = ast.parse(src)
        mod.body
        fdef = next((n for n in mod.body if isinstance(n, ast.FunctionDef)), None)
        if fdef is None or fdef.name != fn.__name__:
            raise RuntimeError("Could not locate the function definition to rewrite.")

        # Stash binding objects in globals under unique names, then assign to real locals
        new_globals = dict(fn.__globals__)
        new_globals[inj_check_key] = True
        salt = uuid.uuid4().hex
        prologue: list[ast.stmt] = []

        for local_name, obj in bindings.items():
            gname = f"__inj_{local_name}_{salt}"
            new_globals[gname] = obj
            prologue.append(
                ast.Assign(
                    targets=[ast.Name(id=local_name, ctx=ast.Store())],
                    value=ast.Name(id=gname, ctx=ast.Load()),
                    lineno=fdef.body[0].lineno if fdef.body else fdef.lineno,
                    col_offset=0,
                )
            )
        first_matched_dec = -1
        for i, call in enumerate(fdef.decorator_list):
            if isinstance(call, ast.Call):
                name = call.func
            else:
                name = call
            if not isinstance(name, ast.Name):
                continue
            if name.id == _decorator_name:
                first_matched_dec = i
                break
        if first_matched_dec == -1:
            raise RuntimeError("Could not locate the inject_locals decorator.")
        # fdef.decorator_list[first_matched_dec] = ast.Call(
        #     func=ast.Name(id=_global_inject_locals_name, ctx=ast.Load()),
        #     args=[],
        #     keywords=[],
        # )
        # fdef.decorator_list[0].func.id
        fdef.decorator_list.pop(first_matched_dec)
        # call.func._attributes
        # call.func._field_types
        # call.func
        # call._field_types
        fdef.body = prologue + fdef.body.copy()
        new_mod = ast.Module(body=[fdef])
        fixed_mod = ast.fix_missing_locations(new_mod)
        # Compile a new function object
        code = compile(
            fixed_mod,
            filename=inspect.getsourcefile(fn) or "<ast>",
            mode="exec",
        )
        ns: dict[str, object] = {}
        exec(
            code,
            new_globals,
            ns,
        )
        new_fn = ns[fn.__name__]

        # Preserve metadata/defaults
        if isinstance(new_fn, types.FunctionType):
            new_fn.__defaults__ = fn.__defaults__
            new_fn.__kwdefaults__ = fn.__kwdefaults__

        return new_fn

    return decorator


inject_locals = inject_locals_a2a0e5bac6c14b19b51b54615cb6ec2f
