"""Small numeric helpers shared by reporting services."""


def change_pct(current: int, previous: int) -> float | None:
    """Percent change from `previous` to `current`, one decimal. None when `previous` is zero,
    where a percentage would be meaningless."""
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def ratio_pct(part: int, whole: int) -> float | None:
    """`part` as a percentage of `whole`, one decimal. None when `whole` is zero."""
    if whole == 0:
        return None
    return round(part / whole * 100, 1)
