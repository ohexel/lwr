# Known bugs
## Interaction between dagster context and `__future__.annotations`
**Problem**: If you use `from __future__ import annotations`, Python stores `context: dg.AssetCheckExecutionContext` as a string annotation. Dagster 1.13.15 then fails to recognize it as the valid context type.

**Solution**: Do not use `from __future__ import annotations` in modules containing `@dg.asset`, `@dg.asset_check`, `@sensor`, or similar Dagster-decorated functions whose context parameters are type-annotated.
