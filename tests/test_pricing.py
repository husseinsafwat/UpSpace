import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pricing import (DISCOUNT_EGP, DISCOUNT_PCT, hall_billed_hours, hall_time_cost,  # noqa: E402
                     room_time_cost, validate_discount)


class RoomPricing(unittest.TestCase):
    def test_exact_minutes(self):
        self.assertEqual(room_time_cost(95, 120), 190)
        self.assertEqual(room_time_cost(1, 60), 1)
        self.assertEqual(room_time_cost(0, 100), 0)

    def test_discount_egp(self):
        self.assertEqual(room_time_cost(60, 100, DISCOUNT_EGP, 20), 80)
        self.assertEqual(room_time_cost(90, 100, DISCOUNT_EGP, 20), 120)

    def test_discount_pct(self):
        self.assertEqual(room_time_cost(60, 100, DISCOUNT_PCT, 25), 75)
        self.assertEqual(room_time_cost(30, 100, DISCOUNT_PCT, 10), 45)

    def test_validate(self):
        self.assertIsNotNone(validate_discount(DISCOUNT_PCT, 120, 100))
        self.assertIsNotNone(validate_discount(DISCOUNT_EGP, 150, 100))
        self.assertIsNone(validate_discount(DISCOUNT_EGP, 50, 100))


class HallPricing(unittest.TestCase):
    def test_billed_hours(self):
        cases = {0: 1, 20: 1, 59: 1, 60: 1, 85: 1, 89: 1, 90: 2, 119: 2,
                 145: 2, 150: 3, 160: 3, 240: 4, 600: 10}
        for minutes, hours in cases.items():
            self.assertEqual(hall_billed_hours(minutes), hours, minutes)

    def test_one_chair(self):
        self.assertEqual(hall_time_cost(20, 1), 15)
        self.assertEqual(hall_time_cost(85, 1), 15)    # 1h25 -> 1h
        self.assertEqual(hall_time_cost(90, 1), 30)    # 1h30 -> 2h
        self.assertEqual(hall_time_cost(160, 1), 40)   # 2h40 -> 3h
        self.assertEqual(hall_time_cost(240, 1), 50)
        self.assertEqual(hall_time_cost(600, 1), 50)   # capped

    def test_chairs(self):
        self.assertEqual(hall_time_cost(160, 3), 120)
        self.assertEqual(hall_time_cost(900, 16), 800)


if __name__ == "__main__":
    unittest.main()
