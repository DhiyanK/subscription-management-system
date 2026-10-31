from calendar import monthrange
from datetime import datetime, timedelta


def add_interval(value: datetime, interval: str) -> datetime:
    """Advance a billing date, clamping dates that do not exist in the target month."""
    if interval == "daily":
        return value + timedelta(days=1)
    if interval == "weekly":
        return value + timedelta(weeks=1)
    if interval == "monthly":
        months_to_add = 1
    elif interval == "yearly":
        months_to_add = 12
    else:
        raise ValueError(f"Unsupported billing interval: {interval}")

    month_index = value.year * 12 + (value.month - 1) + months_to_add
    year, month = divmod(month_index, 12)
    month += 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)
