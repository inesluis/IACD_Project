from datetime import date

import pandas as pd
import pytest

from api.weather.plot_tomorrow import tomorrow_only


def three_days_from(start):
    time = pd.date_range(start, periods=72, freq="h", tz="UTC", name="time")
    return pd.DataFrame({"energy_kwh": 1.0}, index=time)


@pytest.mark.parametrize(
    "today, tomorrow",
    [
        (date(2026, 10, 21), date(2026, 10, 22)),  # ordinary day
        (date(2026, 10, 25), date(2026, 10, 26)),  # clocks go back: today has 25 hours
        (date(2026, 3, 29), date(2026, 3, 30)),    # clocks go forward: today has 23 hours
    ],
)
def test_tomorrow_is_the_next_local_day(today, tomorrow):
    forecast = three_days_from(pd.Timestamp(today) - pd.Timedelta(days=1))

    day = tomorrow_only(forecast, today=today)

    assert len(day) == 24
    assert set(day.index.date) == {tomorrow}
