def outer():
    def unchanged_inner():
        value = 1
        return value

    def changed_inner():
        value = 2
        return value

    return unchanged_inner() + changed_inner()


changed = 1
