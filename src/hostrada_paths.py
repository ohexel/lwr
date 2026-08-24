"""Canonical local files and public source URLs for one HOSTRADA month."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.hostrada_contract import HostradaMonthKey, get_hostrada_field


HOSTRADA_BASE_URL = (
    "https://opendata.dwd.de/climate_environment/CDC/"
    "grids_germany/hourly/hostrada"
)


@dataclass(frozen=True)
class HostradaPaths:
    project_root: Path = Path(".")

    def source_file(
        self,
        month: HostradaMonthKey,
        variable_name: str,
    ) -> Path:
        field = get_hostrada_field(variable_name)
        return (
            self.project_root
            / "data"
            / "raw"
            / "hostrada"
            / field.source_directory
            / month.source_filename(variable_name)
        )


def hostrada_source_url(
    month: HostradaMonthKey,
    variable_name: str,
) -> str:
    field = get_hostrada_field(variable_name)
    return (
        f"{HOSTRADA_BASE_URL}/{field.source_directory}/"
        f"{month.source_filename(variable_name)}"
    )
