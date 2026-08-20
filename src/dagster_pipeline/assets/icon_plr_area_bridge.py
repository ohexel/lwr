import dagster as dg

from src.validate_icon_plr_area_bridge import (
    ICON_PLR_AREA_BRIDGE_PATH,
    read_and_validate_icon_plr_area_bridge,
)


STATIC_SPATIAL_GROUP = "static_spatial"


@dg.asset(
    name="icon_plr_area_bridge",
    group_name=STATIC_SPATIAL_GROUP,
    compute_kind="python",
    description=(
        "Validated static area-intersection bridge "
        "between ICON D2 grid cells and Berlin PLRs."
    ),
)
def icon_plr_area_bridge(
    context: dg.AssetExecutionContext,
) -> dg.MaterializeResult:
    """
    Register and validate the persisted static bridge.

    The expensive geometry construction remains ordinary Python and is
    not rerun for every weather partition. This asset represents the
    durable normalized bridge used by downstream analytical assets.
    """
    context.log.info(
        "Validating static ICON↔PLR area bridge: %s",
        ICON_PLR_AREA_BRIDGE_PATH,
    )

    _, metadata = (
        read_and_validate_icon_plr_area_bridge()
    )

    context.log.info(
        "Validated ICON↔PLR bridge: "
        "%s rows, %s PLRs, %s ICON cells",
        metadata["row_count"],
        metadata["plr_count"],
        metadata["icon_cell_count"],
    )

    return dg.MaterializeResult(
        metadata={
            "path": str(
                ICON_PLR_AREA_BRIDGE_PATH
            ),
            "row_count": (
                metadata["row_count"]
            ),
            "plr_count": (
                metadata["plr_count"]
            ),
            "icon_cell_count": (
                metadata["icon_cell_count"]
            ),
            "min_fraction_of_plr_sum": (
                metadata[
                    "min_fraction_of_plr_sum"
                ]
            ),
            "max_fraction_of_plr_sum": (
                metadata[
                    "max_fraction_of_plr_sum"
                ]
            ),
            "max_fraction_of_plr_deviation": (
                metadata[
                    "max_fraction_of_plr_deviation"
                ]
            ),
        }
    )
