# External artifacts and release preparation

The operational repository contains source code, the canonical database
schema, reproducible Python dependencies, small population CSV and PLR-name
workbook fallbacks, and
machine-readable artifact manifests. It intentionally excludes large
downloadable archives.

## Release artifacts

| Archive | Role | Required? | Exact size | SHA-256 |
| --- | --- | --- | ---: | --- |
| `hostrada-reference-1995-2025.pgcustom` | Precomputed 1995–2025 PLR and Berlin historical reference. | Required for the normal operational installation. | 231,645,982 bytes | `a4552e534c59a44529849c010b5771598fc41b3cd3ae1023d03f07ec79825145` |
| `static-inputs.tar.xz` | Verified LOR, population, and ICON-grid fallback. | Optional; required for a fully offline static bootstrap. | 198,011,500 bytes | `04f492c291fb96285568a68645de5262f09b63d77d1fd97c9fe5b15f57c1aae6` |

Authoritative repository metadata:

- `snapshots/hostrada-reference-1995-2025.manifest.json`
- `snapshots/static-inputs.manifest.json`

Distribute the archives as release attachments, institutional downloads, or
another documented artifact location. Do not commit the `.pgcustom` or
`.tar.xz` files. Their local paths are supplied explicitly to the bootstrap;
no particular artifact directory is required.

## Verify received archives

Inspect both archive hashes:

```bash
sha256sum \
  /path/to/hostrada-reference-1995-2025.pgcustom \
  /path/to/static-inputs.tar.xz
```

Compare the results to the committed manifests above. The HOSTRADA archive can
also be checked directly through the project command:

```bash
uv run --env-file .env python -m src.hostrada_snapshot verify \
  --archive /path/to/hostrada-reference-1995-2025.pgcustom
```

Inspect the static archive's embedded source manifest:

```bash
uv run --env-file .env python -m src.static_snapshot inspect \
  --archive /path/to/static-inputs.tar.xz
```

The committed static manifest additionally records the archive-wide SHA-256.
The archive's own manifest records and enforces the checksum of every
individual restored source.

## HOSTRADA archive contract

The archive is PostgreSQL custom-format, data-only, and contains exactly the
two operational reference tables:

- `analytical.hostrada_plr_hourly_reference`: 4,747,920 rows.
- `analytical.hostrada_berlin_hourly_reference`: 8,760 rows.

It is valid only for:

- Current Berlin PLR geography dated 2023-01-01.
- Exactly 542 PLRs.
- Sorted PLR identifier SHA-256
  `9c48883d882ff09c5b363f96eeb3c05e930b02d6a92218548b2a1a73a7ea1bdd`.
- Reference years 1995–2025.
- `Europe/Berlin` calendar grouping without February 29.
- 271,559 included Berlin historical observations.

The importer verifies archive bytes before static processing, confirms the
installed geography fingerprint, restores both tables atomically, and validates
reference completeness without requiring historical source manifests.

## Static fallback contract

The optional archive contains:

| Source | Uncompressed size | Purpose |
| --- | ---: | --- |
| `icon_grid_0047_R19B07_L.nc.bz2` | 201,429,184 bytes | Canonical forecast-grid definition. |
| `lor_planungsraum.geojson` | 18,248,622 bytes | Exact 542-PLR geography. |
| `EWR_L21_202512E_Matrix.csv` | 117,296 bytes | Official population input. |

The archive contains original static source files, not prebuilt database
tables. A snapshot-based bootstrap still processes the ICON grid and constructs
its spatial bridge; the observed one-time clean-room installation took about
ten minutes.

The 64 KB PLR-name workbook is checked into the repository separately. It does
not alter the existing static archive or require regenerating that archive.

## Publishing checklist

1. Run the default unit suite and the operational acceptance suite.
2. Verify that `.env`, local raw data, Dagster state, and both large archives
   are absent from Git.
3. Verify the downloaded HOSTRADA archive against its committed manifest.
4. Verify the static archive against `snapshots/static-inputs.manifest.json`
   when distributing it.
5. Publish the archive files alongside the same repository revision as their
   committed manifests.
6. Publish the source attributions and links in
   [data-sources.md](data-sources.md).
7. Disclose and recheck the documented assumption that DWD's general CC BY 4.0
   geodata terms also cover the complete ICON grid before publicly distributing
   the optional static archive.
8. Choose an explicit license for the project's own source code if public
   reuse is intended; upstream data licenses do not make that decision for the
   repository owner.
9. Perform a fresh-clone install and process one live forecast using the
   commands in [../runbook.md](../runbook.md).

If any archive is regenerated, update its committed size, checksum, source
metadata, and any affected geography/reference contract in the same reviewed
change. Never overwrite an existing published archive with different bytes
without publishing matching manifest changes.
