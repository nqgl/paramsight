def is_ta_type(obj):
    from nicetv.aliasclassmethod import _TakesAlias

    return isinstance(obj, _TakesAlias)


def _is_aliasclassmethod(obj):
    return (
        is_ta_type(obj)
        # or isinstance(obj, aliasclassmethod)
        or getattr(obj, "_acm_takes_alias", False)
        or (
            isinstance(obj, classmethod)
            and (
                is_ta_type(obj.__func__)
                or getattr(obj.__func__, "_acm_takes_alias", False)
            )
        )
    )
