import unittest

from scheduler.time_utils import parse_fixed_time, parse_session_hours, time_label


class TimeUtilsTest(unittest.TestCase):
    def test_parse_fixed_time(self):
        self.assertEqual(
            parse_fixed_time("MW 09.00-10.30"),
            [(0, 2, 5), (2, 2, 5)],
        )

    def test_parse_session_hours(self):
        self.assertEqual(parse_session_hours("1.5/1.5"), [3, 3])
        self.assertEqual(parse_session_hours(3), [6])

    def test_time_label(self):
        self.assertEqual(time_label(8, 3), "13:00-14:30")


if __name__ == "__main__":
    unittest.main()
