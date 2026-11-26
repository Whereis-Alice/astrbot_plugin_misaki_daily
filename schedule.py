from datetime import UTC, datetime, time, timedelta


def next_run(now: datetime, at: time) -> datetime:
    """Return the next occurrence of a local schedule in the timezone of now."""
    if now.tzinfo is None or now.utcoffset() is None or at.tzinfo is not None:
        raise ValueError("需要带时区的当前时间和不带时区的推送时间")

    zone = now.tzinfo
    instant = now.astimezone(UTC)
    day = now.date()
    while True:
        wall_time = datetime.combine(day, at)
        candidate = wall_time.replace(tzinfo=zone, fold=0)
        candidate_utc = candidate.astimezone(UTC)
        restored_wall_time = candidate_utc.astimezone(zone).replace(tzinfo=None)
        if candidate_utc > instant and restored_wall_time == wall_time:
            return candidate
        day += timedelta(days=1)


def group_origins(values: list[str]) -> tuple[str, ...]:
    """Return distinct AstrBot GroupMessage origins in configuration order."""
    origins = {}
    for value in values:
        if not isinstance(value, str):
            continue
        # Mirror MessageSession.from_str and preserve its opaque session ID.
        parts = value.split(":", 2)
        if len(parts) != 3:
            continue
        platform, message_type, session = parts
        if platform.strip() and message_type == "GroupMessage" and session.strip():
            origins[value] = None
    return tuple(origins)
