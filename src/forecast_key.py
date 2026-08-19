from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re


DWD_RUN_FORMAT = "%Y-%m-%dT%H:%M"
RUN_LABEL_FORMAT = "%Y%m%dT%H%M"

_LEAD_TIME_PATTERN = re.compile(
    r"^PT(?P<hours>\d{3})H(?P<minutes>\d{2})M$"
)


def parse_dwd_run_time(value: str) -> datetime:
    """
    Parse a DWD ICON_D2_RUC run timestamp.

    DWD run directory timestamps are interpreted as UTC even though the
    directory label itself does not include a timezone suffix.
    """
    parsed = datetime.strptime(value, DWD_RUN_FORMAT)
    return parsed.replace(tzinfo=timezone.utc)


def parse_lead_time(value: str) -> timedelta:
    """
    Parse the DWD lead-time label used by this project.

    Supported canonical form:
        PT000H00M
        PT012H00M
        PT001H30M
    """
    match = _LEAD_TIME_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(
            "Lead time must use canonical DWD form "
            "'PTxxxHxxM', for example PT000H00M or PT012H00M"
        )

    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))

    if minutes >= 60:
        raise ValueError(
            "Lead-time minutes must be between 00 and 59"
        )

    return timedelta(hours=hours, minutes=minutes)


def format_lead_time(value: timedelta) -> str:
    """
    Format a non-negative lead time using the project's canonical DWD form.
    """
    total_seconds = int(value.total_seconds())

    if value.total_seconds() != total_seconds:
        raise ValueError(
            "Lead time must resolve to whole seconds"
        )

    if total_seconds < 0:
        raise ValueError(
            "Lead time cannot be negative"
        )

    if total_seconds % 60 != 0:
        raise ValueError(
            "Lead time must resolve to whole minutes"
        )

    total_minutes = total_seconds // 60
    hours, minutes = divmod(total_minutes, 60)

    if hours > 999:
        raise ValueError(
            "Lead time exceeds the supported three-digit hour format"
        )

    return f"PT{hours:03d}H{minutes:02d}M"


@dataclass(frozen=True)
class ForecastKey:
    """
    Canonical identity for one ICON_D2_RUC forecast partition.

    A forecast is identified by:
        - model run time in UTC
        - forecast lead time

    valid_time is derived rather than stored independently.
    """

    run_time: datetime
    lead_time: timedelta

    def __post_init__(self) -> None:
        if self.run_time.tzinfo is None:
            raise ValueError(
                "run_time must be timezone-aware"
            )

        normalized_run_time = self.run_time.astimezone(timezone.utc)

        if normalized_run_time.second != 0:
            raise ValueError(
                "run_time must resolve to a whole minute"
            )

        if normalized_run_time.microsecond != 0:
            raise ValueError(
                "run_time must resolve to a whole minute"
            )

        if self.lead_time.total_seconds() < 0:
            raise ValueError(
                "lead_time cannot be negative"
            )

        if self.lead_time.total_seconds() % 60 != 0:
            raise ValueError(
                "lead_time must resolve to whole minutes"
            )

        # Normalize all stored run times to UTC.
        object.__setattr__(
            self,
            "run_time",
            normalized_run_time,
        )

        # Validate that the lead time can be represented by our canonical
        # filesystem / partition label.
        format_lead_time(self.lead_time)

    @classmethod
    def from_dwd_labels(
        cls,
        *,
        run_time: str,
        lead_time: str,
    ) -> "ForecastKey":
        return cls(
            run_time=parse_dwd_run_time(run_time),
            lead_time=parse_lead_time(lead_time),
        )

    @property
    def valid_time(self) -> datetime:
        return self.run_time + self.lead_time

    @property
    def run_label(self) -> str:
        return self.run_time.strftime(RUN_LABEL_FORMAT)

    @property
    def lead_time_label(self) -> str:
        return format_lead_time(self.lead_time)

    @property
    def run_time_partition_key(self) -> str:
        """
        Canonical run-time dimension used by future Dagster partitions.
        """
        return self.run_label

    @property
    def lead_time_partition_key(self) -> str:
        """
        Canonical lead-time dimension used by future Dagster partitions.
        """
        return self.lead_time_label


@dataclass(frozen=True)
class ProjectPaths:
    """
    Explicit file layout for forecast-partitioned project assets.

    The path structure mirrors ForecastKey so the data remains understandable
    without Dagster.
    """

    project_root: Path = Path(".")

    @property
    def data_root(self) -> Path:
        return self.project_root / "data"

    @property
    def reports_root(self) -> Path:
        return self.project_root / "reports"

    @staticmethod
    def indicator_directory(indicator: str) -> str:
        """
        Convert DWD indicator names to the project's lowercase directory form.

        Examples:
            T_2M -> t_2m
            RELHUM_2M -> relhum_2m
        """
        normalized = indicator.strip().lower()

        if not normalized:
            raise ValueError(
                "indicator cannot be blank"
            )

        if not re.fullmatch(r"[a-z0-9_]+", normalized):
            raise ValueError(
                "indicator directory name may contain only "
                "letters, digits, and underscores"
            )

        return normalized

    def raw_icon_field(
        self,
        *,
        indicator: str,
        forecast: ForecastKey,
    ) -> Path:
        indicator_dir = self.indicator_directory(indicator)
        return (
            self.data_root
            / "raw"
            / "icon_d2_ruc"
            / indicator_dir
            / forecast.run_label
            / forecast.lead_time_label
            / f"{indicator_dir}.grib2"
        )

    def normalized_icon_field(
        self,
        *,
        indicator: str,
        forecast: ForecastKey,
    ) -> Path:
        indicator_dir = self.indicator_directory(indicator)
        return (
            self.data_root
            / "normalized"
            / "icon_d2_ruc"
            / indicator_dir
            / forecast.run_label
            / forecast.lead_time_label
            / f"{indicator_dir}.parquet"
        )

    def analytical_plr_weather(
        self,
        *,
        forecast: ForecastKey,
    ) -> Path:
        return (
            self.data_root
            / "analytical"
            / "plr_weather"
            / forecast.run_label
            / forecast.lead_time_label
            / "plr_weather.parquet"
        )

    def analytical_plr_weather_population(
        self,
        *,
        forecast: ForecastKey,
    ) -> Path:
        return (
            self.data_root
            / "analytical"
            / "plr_weather_population"
            / forecast.run_label
            / forecast.lead_time_label
            / "plr_weather_population.parquet"
        )

    def icon_profile(
        self,
        *,
        indicator: str,
        forecast: ForecastKey,
    ) -> Path:
        indicator_dir = self.indicator_directory(indicator)
        return (
            self.reports_root
            / "profiling"
            / "icon_d2_ruc"
            / indicator_dir
            / forecast.run_label
            / forecast.lead_time_label
            / "profile.json"
        )
