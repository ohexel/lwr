
# Running profiling and tests

From the project root:

```{bash}
uv run python -m src.profile_lor
uv run python -m src.profile_icon_grid
uv run python -m src.profile_icon_t2m
uv run python -m src.profile_icon_plr_bridge
uv run python -m src.profile_afs_population
```

Run the complete test suite with:

```{bash}
uv run python -m pytest -v
```

Individual test modules can also be run separately:

```{bash}
uv run python -m pytest tests/test_lor.py -v
uv run python -m pytest tests/test_icon_grid.py -v
uv run python -m pytest tests/test_icon_t2m.py -v
uv run python -m pytest tests/test_icon_plr_bridge.py -v
uv run python -m pytest tests/test_afs_population.py -v
```
