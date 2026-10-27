import pytest

from api.appliances.eprel_sync import load_config
from api.weather.tests.conftest import db_conn  # noqa: F401  (rolled-back test database)


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def wm_record():
    """A washingmachines2019 record from EPREL (2026-10-26), trimmed."""
    return {
        "eprelRegistrationNumber": "352460",
        "modelIdentifier": "GC3V708S2",
        "supplierOrTrademark": "LG Electronics",
        "productGroup": "washingmachines2019",
        "energyClass": "A",
        "energyConsPerCycle": 0.473,
        "programmeDurationRated": 218,
        "ratedCapacity": 8,
        "onMarketStartDate": [2020, 9, 24],
        "onMarketEndDate": [2050, 12, 31],
        "organisation": {"organisationName": "LG Electronics Deutschland GmbH"},
    }


@pytest.fixture
def oven_record():
    """An ovens record from EPREL (2026-10-26), trimmed."""
    return {
        "eprelRegistrationNumber": "247001",
        "modelIdentifier": "FSS57100GAC 7738987655",
        "supplierOrTrademark": "Beko",
        "energyClass": "A",
        "numberCavities": 1,
        "cavities": [
            {
                "volume": 65,
                "energyConsumptionCycle": 0.78,
                "energyConsumptionCycleFanForced": None,
                "heatSourceType": "ELECTRIC",
            }
        ],
        "onMarketStartDate": [2019, 1, 1],
        "onMarketEndDate": None,
    }
