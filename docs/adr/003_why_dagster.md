# Add Dagster for source-aware weather orchestration
Dagster offers many nice features. However, the central benefit of Dagster for this project comes from a data challenge. 

## Context
Version 1 of the pipeline architecture uses ordinary Python scripts and explicit files. This keeps the transformation logic inspectable, but the ICON D2 RUC weather source introduces an operational problem that is awkward to solve with simple scheduled scripts.

DWD publishes forecast runs on a rolling Open Data endpoint. A new model run may appear before every field required by the analytical pipeline is available (i.e. `api_endpoint/todays_date/run_9am/` may exist but subdirectories or files for specific indicators will appear only progressively and with a variable delay). Older forecast files remain available only for a limited period. A blind hourly schedule could therefore start too early and ingest an incomplete forecast set, while a missed ingestion opportunity may later become impossible to recover from the upstream source.

## Decision
Use Dagster as the orchestration and asset-lineage layer for version 2 of the architecture.

A DWD availability sensor will be a first-class component. It will check whether all required ICON D2 RUC fields for a forecast partition are available before launching a run. A forecast partition is identified by model run time and lead time. The initial implementation uses lead time `PT000H00M`, while the design allows additional lead times later without changing the core ingestion model.

Raw GRIB files are retained locally after successful ingestion. This creates an important distinction between two kinds of backfill:

- **Acquisition backfill** depends on the limited rolling DWD source window and is only possible while the original forecast files remain upstream.
- **Reprocessing backfill** uses locally retained raw files and can rerun normalization and downstream transformations independently of DWD retention.

Dagster will manage partition history, triggering, lineage, run status, and reprocessing. Existing Python functions remain responsible for ingestion, validation, profiling, spatial transformation, and analytical logic.

## Consequences

Dagster adds some project complexity, but it addresses a concrete source-system problem and directly supports the capstone requirements for orchestration, monitoring, and backfilling.

The architecture remains deliberately simple in other respects: explicit file paths are retained, transformation code remains ordinary Python, and Dagster-specific checks and tests are limited to high-value operational behavior.
