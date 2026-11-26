from datetime import date, timedelta
from functools import lru_cache
from itertools import chain

from lunar_python import Lunar, Solar

WEEKDAYS = "一二三四五六日"
SOLAR_FESTIVALS = (
    ("元旦", 1, 1),
    ("劳动节", 5, 1),
    ("国庆节", 10, 1),
)
LUNAR_FESTIVALS = (
    ("春节", 1, 1),
    ("元宵节", 1, 15),
    ("端午节", 5, 5),
    ("中秋节", 8, 15),
    ("重阳节", 9, 9),
)


def _solar_date(solar: Solar) -> date:
    return date(solar.getYear(), solar.getMonth(), solar.getDay())


@lru_cache(maxsize=4)
def _solar_festivals(year: int) -> tuple[tuple[str, date], ...]:
    terms = Solar.fromYmd(year, 5, 1).getLunar().getJieQiTable()
    fixed = tuple(
        (name, date(year, month, day)) for name, month, day in SOLAR_FESTIVALS
    )
    return (*fixed, ("清明节", _solar_date(terms["清明"])))


@lru_cache(maxsize=4)
def _lunar_festivals(year: int) -> tuple[tuple[str, date], ...]:
    festivals = tuple(
        (name, _solar_date(Lunar.fromYmd(year, month, day).getSolar()))
        for name, month, day in LUNAR_FESTIVALS
    )
    spring_festival = festivals[0][1]
    return (*festivals, ("除夕", spring_festival - timedelta(days=1)))


def build_calendar(day: date, limit: int) -> dict:
    """Return the lunar date and upcoming festival dates for this calendar day."""
    lunar = Solar.fromYmd(day.year, day.month, day.day).getLunar()
    weekday = day.weekday()
    calendar = {
        "lunar": f"{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}",
        "weekday": f"星期{WEEKDAYS[weekday]}",
        "festivals": [],
    }
    if limit <= 0:
        return calendar

    lunar_year = lunar.getYear()
    candidates = chain(
        _solar_festivals(day.year),
        _solar_festivals(day.year + 1),
        _lunar_festivals(lunar_year),
        _lunar_festivals(lunar_year + 1),
        (("周末", day + timedelta(days=max(0, 5 - weekday))),),
    )
    upcoming = {}
    for name, festival_day in sorted(candidates, key=lambda item: item[1]):
        if festival_day >= day:
            upcoming.setdefault(name, festival_day)

    calendar["festivals"] = [
        {
            "name": name,
            "date": festival_day.isoformat(),
            "days_left": (festival_day - day).days,
        }
        for name, festival_day in list(upcoming.items())[:limit]
    ]
    return calendar
