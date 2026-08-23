from src.icon_grid_contract import (
    ICON_D2_GRID_CONTRACT,
)


def test_icon_d2_grid_contract_cardinalities() -> None:
    assert ICON_D2_GRID_CONTRACT.vertex_count == 272_089
    assert ICON_D2_GRID_CONTRACT.cell_count == 542_040
    assert ICON_D2_GRID_CONTRACT.vertices_per_cell == 3
    assert (
        ICON_D2_GRID_CONTRACT.topology_row_count
        == 1_626_120
    )
    assert (
        ICON_D2_GRID_CONTRACT.field_point_count
        == ICON_D2_GRID_CONTRACT.cell_count
    )


def test_icon_d2_grid_contract_uses_canonical_raw_path() -> None:
    assert str(ICON_D2_GRID_CONTRACT.source_path) == (
        "data/raw/icon_d2_grid/"
        "icon_grid_0047_R19B07_L.nc.bz2"
    )
