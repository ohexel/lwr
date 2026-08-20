import pandas as pd

def test_population_quality_accounting_logic():
    frame = pd.DataFrame(
        {
            "plr_id": ["a", "b", "c"],
            "population_status": [
                "available",
                "available",
                "rejected_source_record",
            ],
            "population_total": [100, 200, None],
            "population_65plus": [20, 40, None],
            "share_65plus": [0.2, 0.2, None],
            "population_rejection_reason": [
                None,
                None,
                "missing_population_total",
            ],
        }
    )
    rejected = pd.DataFrame({"plr_id": ["c"]})
    final_rejected_ids = set(
        frame.loc[
            frame["population_status"] == "rejected_source_record",
            "plr_id",
        ]
    )
    registry_ids = set(rejected["plr_id"])
    available = frame.loc[frame["population_status"] == "available"]
    final_rejected = frame.loc[
        frame["population_status"] == "rejected_source_record"
    ]
    cols = ["population_total", "population_65plus", "share_65plus"]

    assert final_rejected_ids == registry_ids
    assert not available[cols].isna().any().any()
    assert final_rejected[cols].isna().all(axis=None)
    assert not final_rejected[
        "population_rejection_reason"
    ].isna().any()
