import dagster as dg

from src.build_plr_population_65plus import (
    ACCEPTED_POPULATION_PATH,
    POPULATION_VALIDATION_SUMMARY_PATH,
    REJECTED_POPULATION_PATH,
    build_population_quality_outputs,
)
from src.validate_plr_population import (
    validate_population_quality_artifacts,
)


STATIC_DEMOGRAPHY_GROUP = "static_demography"


@dg.multi_asset(
    outs={
        "plr_population_65plus": dg.AssetOut(
            group_name=STATIC_DEMOGRAPHY_GROUP,
            description=(
                "Accepted normalized PLR population "
                "records safe for analytical joins."
            ),
        ),
        "plr_population_rejected": dg.AssetOut(
            group_name=STATIC_DEMOGRAPHY_GROUP,
            description=(
                "Rejected normalized PLR population "
                "records retained for inspection."
            ),
        ),
    },
    compute_kind="python",
)
def plr_population_quality_gate(
    context: dg.AssetExecutionContext,
):
    accepted, rejected, summary = (
        build_population_quality_outputs()
    )

    metadata = (
        validate_population_quality_artifacts(
            accepted,
            rejected,
        )
    )

    context.log.info(
        "Population quality gate: "
        "%s source, %s accepted, %s rejected",
        metadata["source_row_count"],
        metadata["accepted_row_count"],
        metadata["rejected_row_count"],
    )

    yield dg.Output(
        None,
        output_name="plr_population_65plus",
        metadata={
            "path": str(
                ACCEPTED_POPULATION_PATH
            ),
            "source_row_count": (
                metadata["source_row_count"]
            ),
            "accepted_row_count": (
                metadata["accepted_row_count"]
            ),
            "rejected_row_count": (
                metadata["rejected_row_count"]
            ),
            "validation_summary_path": str(
                POPULATION_VALIDATION_SUMMARY_PATH
            ),
        },
    )

    yield dg.Output(
        None,
        output_name="plr_population_rejected",
        metadata={
            "path": str(
                REJECTED_POPULATION_PATH
            ),
            "rejected_row_count": (
                metadata["rejected_row_count"]
            ),
            "rejection_reasons": (
                dg.MetadataValue.json(
                    metadata[
                        "rejection_reasons"
                    ]
                )
            ),
        },
    )
