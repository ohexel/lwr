import dagster as dg

from src.dagster_pipeline.assets.database_analytical import (
    ANALYTICAL_ASSETS,
)
from src.dagster_pipeline.assets.database_bridge import (
    BRIDGE_ASSETS,
)
from src.dagster_pipeline.assets.database_population import (
    POPULATION_ASSETS,
)
from src.dagster_pipeline.assets.database_spatial import (
    SPATIAL_ASSETS,
)
from src.dagster_pipeline.assets.database_weather_mask import (
    WEATHER_MASK_ASSETS,
)
from src.dagster_pipeline.assets.database_weather_normalized import (
    WEATHER_NORMALIZED_ASSETS,
)
from src.dagster_pipeline.assets.database_weather_raw import (
    WEATHER_RAW_ASSETS,
)
from src.dagster_pipeline.assets.icon_d2_ruc import (
    RAW_ICON_D2_RUC_ASSETS,
)
from src.dagster_pipeline.checks.database_analytical import (
    ANALYTICAL_CHECKS,
)
from src.dagster_pipeline.checks.database_bridge import (
    BRIDGE_CHECKS,
)
from src.dagster_pipeline.checks.database_population import (
    POPULATION_CHECKS,
)
from src.dagster_pipeline.checks.database_spatial import (
    SPATIAL_CHECKS,
)
from src.dagster_pipeline.checks.database_weather_mask import (
    WEATHER_MASK_CHECKS,
)
from src.dagster_pipeline.checks.database_weather_normalized import (
    WEATHER_NORMALIZED_CHECKS,
)
from src.dagster_pipeline.checks.database_weather_raw import (
    WEATHER_RAW_CHECKS,
)
from src.dagster_pipeline.jobs import (
    ICON_D2_RUC_FORECAST_JOB,
    ICON_D2_RUC_RAW_ACQUISITION_JOB,
)
from src.dagster_pipeline.sensors.dwd_weather import (
    dwd_icon_d2_ruc_availability_sensor,
)


defs = dg.Definitions(
    assets=[
        *RAW_ICON_D2_RUC_ASSETS,
        *POPULATION_ASSETS,
        *SPATIAL_ASSETS,
        *BRIDGE_ASSETS,
        *WEATHER_MASK_ASSETS,
        *WEATHER_RAW_ASSETS,
        *WEATHER_NORMALIZED_ASSETS,
        *ANALYTICAL_ASSETS,
    ],
    asset_checks=[
        *POPULATION_CHECKS,
        *SPATIAL_CHECKS,
        *BRIDGE_CHECKS,
        *WEATHER_MASK_CHECKS,
        *WEATHER_RAW_CHECKS,
        *WEATHER_NORMALIZED_CHECKS,
        *ANALYTICAL_CHECKS,
    ],
    jobs=[
        ICON_D2_RUC_RAW_ACQUISITION_JOB,
        ICON_D2_RUC_FORECAST_JOB,
    ],
    sensors=[
        dwd_icon_d2_ruc_availability_sensor,
    ],
)
