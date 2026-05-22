from datetime import date, time
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from payroll_app.time_off_utils import (
    equivalent_days,
    entry_covers_date,
    time_ranges_overlap,
    validate_time_off_entry,
)


def _entry(**kwargs):
    defaults = {
        'start_date': date(2026, 5, 22),
        'end_date': date(2026, 5, 22),
        'coverage': 'full_day',
        'start_day_coverage': 'full_day',
        'end_day_coverage': 'full_day',
        'start_time': None,
        'end_time': None,
        'end_start_time': None,
        'end_end_time': None,
    }
    defaults.update(kwargs)
    obj = SimpleNamespace(**defaults)
    obj.is_single_day = obj.start_date == obj.end_date
    return obj


class TimeOffUtilsTests(SimpleTestCase):
    def test_half_day_am_does_not_block_pm_slot(self):
        entry = _entry(coverage='half_day_am')
        self.assertFalse(
            entry_covers_date(
                entry,
                date(2026, 5, 22),
                time(12, 0),
                time(17, 0),
            )
        )

    def test_half_day_am_blocks_am_slot(self):
        entry = _entry(coverage='half_day_am')
        self.assertTrue(
            entry_covers_date(
                entry,
                date(2026, 5, 22),
                time(8, 0),
                time(11, 0),
            )
        )

    def test_custom_hours_equivalent(self):
        entry = _entry(
            coverage='custom',
            start_time=time(10, 0),
            end_time=time(14, 0),
        )
        self.assertEqual(equivalent_days(entry), Decimal('0.50'))

    def test_multi_day_with_partial_end(self):
        entry = _entry(
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 3),
            start_day_coverage='full_day',
            end_day_coverage='half_day_am',
        )
        self.assertEqual(equivalent_days(entry), Decimal('2.50'))

    def test_validate_custom_requires_times(self):
        entry = _entry(coverage='custom')
        errors = validate_time_off_entry(entry)
        self.assertIn('start_time', errors)

    def test_time_ranges_overlap(self):
        day = date(2026, 1, 1)
        self.assertTrue(
            time_ranges_overlap(day, (time(9, 0), time(12, 0)), (time(11, 0), time(13, 0)))
        )
        self.assertFalse(
            time_ranges_overlap(day, (time(9, 0), time(12, 0)), (time(12, 0), time(17, 0)))
        )
