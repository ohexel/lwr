from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IconGridContract:
    dwd_grid_number: str
    dwd_grid_name: str
    source_grid_id: str
    source_path: Path
    source_url: str
    vertex_count: int
    cell_count: int
    vertices_per_cell: int

    @property
    def topology_row_count(self) -> int:
        return self.cell_count * self.vertices_per_cell

    @property
    def field_point_count(self) -> int:
        """Weather fields contain one value per ICON grid cell."""
        return self.cell_count


ICON_D2_GRID_CONTRACT = IconGridContract(
    dwd_grid_number="0047",
    dwd_grid_name="R19B07_L",
    source_grid_id="icon_grid_0047_R19B07_L",
    source_path=Path(
        "data/raw/icon_d2_grid/"
        "icon_grid_0047_R19B07_L.nc.bz2"
    ),
    source_url=(
        "https://opendata.dwd.de/weather/lib/cdo/"
        "icon_grid_0047_R19B07_L.nc.bz2"
    ),
    vertex_count=272_089,
    cell_count=542_040,
    vertices_per_cell=3,
)
