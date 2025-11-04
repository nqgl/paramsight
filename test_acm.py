# ------------------ DEMO ------------------


from mapytype import aliasclassmethod


class C[T: float = int]:
    def __init__(self):
        self.value = 1

    @aliasclassmethod
    def check_class(cls):
        print(f"cls {cls}")

    @classmethod
    def normal(cls):
        print(f"normal sees {cls}")


c = C[int]()
print(c.__orig_class__)
C.check_class()  # -> cls <class '__main__.C'>
C[int].check_class()  # -> cls C[int]
C[int].normal()  # -> normal sees <class '__main__.C'>
type(c)
c.check_class()
c.normal()
