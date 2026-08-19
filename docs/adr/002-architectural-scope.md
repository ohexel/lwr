# Architectural scope
At its simplest level, this project can be accomplished locally using only Python, strict naming conventions for scripts and outputs, and Markdown or JSON for human-readable output artefacts. Adding tools or frameworks should respond to a specific need or challenge. I describe four ideal-typical architectures and the corresponding problems that they solve. The target architecture for the capstone project is v3. 

**Architecture v1**: Python-only. Python does everything from ingestion to reporting. The pipeline exists only in the directory structure and naming conventions. Output artefacts are largely meant to be easy to discover and understand for humans. The volume and velocity of the data makes this possible. This is a good starting point to make sure that the initial logic of the business requirements holds up. This versions answers the questions: what are the data? how do I transform them? do they look like what I expected (before and after transformation)? what are examples of possible outputs?

**Architecture v2**: add Dagster to make the pipeline more robust. A pipeline that exists purely thanks to directory structure and naming conventions more than likely breaks as soon as we add more collaborators, need more complicated scheduling than "just run it manually", need backfills, handle pipeline failures automatically, or provide some high-level summary of which stages of the pipeline ran, when, successfully or not, and which outputs were produced. Enter Dagster. Dagster integrates well with a Python-first pipeline, handles all the mentioned use cases well, and plays well with other technologies that I might want to introduce at later stages. This version of the architecture answers the questions: what stages of the pipeline actually ran and what happened? Can I please schedule this for 2am and then every two hours? And please retry at least five times if one run fails but also keep doing the other scheduled runs independently.

**Architecture v3**: add PostgreSQL/PostGIS to unify the transformation and data contract logic and expose a conventional analytical surface. I don't add PostgreSQL/PostGIS because the volume or velocity of the data requires it. I could extend the project vertically or horizontally by extending it beyond Berlin, adding historical data, or multiplying indicators. At this stage, however, I am interested in PostgreSQL/PostGIS for its data modelling capabilities and as a serving framework for analysis. Whereas data contracts were implemented via custom-written tests using `pytest` before, they are expressed as SQL constraints. This version provides data contracts and consisten and persistent analytical state in a well-known format.

**Architecture v4**: add dbt because the transformation graph has grown to the point where observability and discoverability benefit from it. Adding dbt would make sense if I implement the vertical or horizontal extensions mentioned earlier. If I implement a broader set of indicators with a broader and deeper range of derived measures or if I extend this beyond Berlin (or with deep historical time series), adding dbt might have the benefit of making the pipeline more understandable and more manageable for end users or collaborators that are not elbow-deep in the muck.

In short, the four architectures correspond to:

```
Python + files + pytest
Python + Dagster + files + pytest
Python + Dagster + PostgreSQL/PostGIS 
Python + Dagster + PostgreSQL/PostGIS + dbt + dagster-dbt + dbt tests
```

Throughout, we must ensure documentation, testing, reproducibility, and observability. This will be accomplished with the tools appropriate to the respective complexity level, the priority being low-complexity tools and frameworks. In other words, if it can be accomplished with bash, JSON, or pytest instead of something fancier, that is what I will be using.

# v1: A minimal manual pipeline using Python
The minimum prototype can be achieved exclusively with Python. The data volume (and velocity) is low. Transformations are easily feasible in Python. Runs are manual. Consistent naming of scripts and outputs is sufficient coordinate the pipeline and document the outputs.

```
Official sources
      |
      v
Python ingestion scripts
      |
      v
data/raw/
      |
      v
Python transformation scripts
      |
      v
data/processed/
      |
      v
Python aggregation scripts
      |
      v
data/analysis_ready/
      |
      v
Parquet / CSV / plots
```

The drawbacks are that the workflow exists only by convention, that robustness (retries, scheduling, run history, backfills) has to be implemented manually as part of the scripts, and that ...

# v2: Adding Dagster for a more resilient, automated pipeline 
Add explicit orchestration, lineage, scheduling, run history: Dagster.

```
                    DAGSTER
                       |
        +--------------+--------------+
        |              |              |
        v              v              v
    geospatial     Population      Weather
      asset          asset          asset
        |              |              |
        v              v              v
      raw            raw            raw
        |              |              |
        +-------+      |      +-------+
                v      v      v
               processed assets
                      |
                      v
            analysis-ready assets
```

# v3: Adding Postgres for persistent analytical state
Add durable relational/spatial state and a conventional analytical interface: PostgreSQL/PostGIS.

```
                         DAGSTER
                            |
       +--------------------+--------------------+
       |                    |                    |
       v                    v                    v
Python ingestion       Python decoding      Python loading
       |                    |                    |
       +--------------------+--------------------+
                            |
                            v
                  PostgreSQL / PostGIS
                            |
                  +---------+---------+
                  |                   |
                  v                   v
              silver schema        gold schema
                  |                   |
        geometry / facts       analytical tables
                  |                   |
                  +---------+---------+
                            |
                            v
                     SQL consumers
```

# v4: add dbt when increased project scope and complexity requires it
```
                         DAGSTER
                            |
             +--------------+--------------+
             |                             |
             v                             v
       Python assets                   dbt assets
             |                             |
API / files / GRIB / NetCDF                |
             |                             |
             v                             |
       PostgreSQL / PostGIS                |
             |                             |
          raw/silver ----------------------+
                                           |
                                           v
                                    SQL transformations
                                           |
                                     dbt tests/docs
                                           |
                                           v
                                      gold models
```
