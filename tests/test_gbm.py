from paramsight.generic_restored_basemodel.generic_basemodel import (
    C1,
    C2,
    GbmTest,
    GbmTest2,
)


def test_main():
    test = GbmTest2[C2, str](
        x1=GbmTest[str, C2](x1="12a", x2=C2(x=3)),
        x2=GbmTest[str, C2](x1="3", x2=C2(x=4, z="def")),
    )
    dumped = test.model_dump()
    loaded = GbmTest2.model_validate(dumped)
    print(loaded)
    assert loaded == test
