import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

with open("reports/profiling/afs_population_2025-12-31.json") as f:
    afs_population = json.load(f)

with open("reports/profiling/icon_plr_bridge_profile.json") as f:
    bridge = json.load(f)

with open("reports/profiling/icon_grid_profile.json") as f:
    grid = json.load(f)

with open("reports/profiling/lor_profile.json") as f:
    lor = json.load(f)

env = Environment(loader = FileSystemLoader("reports/templates"))
template = env.get_template("data_quality_arch1.md.j2")
rendered = template.render(
        afs_population = afs_population,
        bridge = bridge,
        grid = grid,
        lor = lor)
Path("reports/generated/data_quality_arch_v1.md").write_text(rendered)
