import unittest
from pathlib import Path

from scheduler.io import load_main_workbook


class InputTest(unittest.TestCase):
    def test_sample_workbook(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "sample_data"
            / "SchEDU_input_68_1.xlsx"
        )
        bundle = load_main_workbook(path)
        self.assertFalse(bundle.has_errors)
        self.assertEqual(bundle.source_format, "canonical")
        self.assertGreater(len(bundle.department_courses), 0)
        self.assertGreater(len(bundle.rooms), 0)


if __name__ == "__main__":
    unittest.main()
