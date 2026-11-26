import unittest
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from calendar_data import build_calendar
from schedule import group_origins, next_run


class CalendarTests(unittest.TestCase):
    def test_autumn_uses_actual_dates_into_next_year(self):
        calendar = build_calendar(date(2026, 10, 19), 3)
        self.assertEqual(calendar["weekday"], "星期一")
        self.assertEqual(
            calendar["festivals"],
            [
                {"name": "周末", "date": "2026-10-24", "days_left": 5},
                {"name": "元旦", "date": "2027-01-01", "days_left": 74},
                {"name": "除夕", "date": "2027-02-05", "days_left": 109},
            ],
        )

    def test_eve_handles_both_lunar_month_lengths(self):
        for day, lunar in (
            (date(2024, 2, 9), "腊月三十"),
            (date(2025, 1, 28), "腊月廿九"),
        ):
            with self.subTest(day=day):
                calendar = build_calendar(day, 2)
                self.assertEqual(calendar["lunar"], lunar)
                self.assertEqual(calendar["festivals"][0]["name"], "除夕")
                self.assertEqual(calendar["festivals"][0]["days_left"], 0)
                self.assertEqual(calendar["festivals"][1]["name"], "春节")
                self.assertEqual(calendar["festivals"][1]["days_left"], 1)

    def test_records_qingming_solar_term_and_the_sunday(self):
        festivals = build_calendar(date(2024, 4, 4), 2)["festivals"]
        self.assertEqual(
            festivals[0], {"name": "清明节", "date": "2024-04-04", "days_left": 0}
        )
        festivals = build_calendar(date(2024, 4, 7), 1)["festivals"]
        self.assertEqual(
            festivals, [{"name": "周末", "date": "2024-04-07", "days_left": 0}]
        )

    def test_leap_month_and_disabled_festival_list(self):
        calendar = build_calendar(date(2025, 7, 25), 0)
        self.assertEqual(calendar["lunar"], "闰六月初一")
        self.assertEqual(calendar["festivals"], [])


class ScheduleTests(unittest.TestCase):
    def test_local_time_is_strictly_in_the_future(self):
        zone = ZoneInfo("Asia/Shanghai")
        at = time(8, 30)
        self.assertEqual(
            next_run(datetime(2026, 12, 31, 8, tzinfo=zone), at),
            datetime(2026, 12, 31, 8, 30, tzinfo=zone),
        )
        self.assertEqual(
            next_run(datetime(2026, 12, 31, 8, 30, tzinfo=zone), at),
            datetime(2027, 1, 1, 8, 30, tzinfo=zone),
        )

    def test_spring_forward_skips_missing_time(self):
        zone = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 8, 1, tzinfo=zone)
        self.assertEqual(
            next_run(now, time(2, 30)), datetime(2026, 3, 9, 2, 30, tzinfo=zone)
        )

    def test_fall_back_only_uses_first_occurrence(self):
        zone = ZoneInfo("America/New_York")
        now = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=0)
        self.assertEqual(
            next_run(now, time(1, 30)).astimezone(UTC),
            datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        )
        now = now.replace(fold=1)
        self.assertEqual(
            next_run(now, time(1, 30)), datetime(2026, 11, 2, 1, 30, tzinfo=zone)
        )

    def test_umo_preserves_the_opaque_session_id(self):
        values = [
            "111",
            "qq:FriendMessage:111",
            "qq:PrivateMessage:111",
            "qq:GroupMessage:111",
            "qq:GroupMessage:111",
            " :GroupMessage:111",
            "qq:GroupMessage: ",
            "sandbox:GroupMessage:room: alpha ",
        ]
        self.assertEqual(
            group_origins(values),
            ("qq:GroupMessage:111", "sandbox:GroupMessage:room: alpha "),
        )


if __name__ == "__main__":
    unittest.main()
