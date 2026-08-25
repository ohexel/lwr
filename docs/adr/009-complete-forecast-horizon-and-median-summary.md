# ADR 009: Complete forecast horizon and median-based neighborhood summary

Status: Accepted on the optional 25-point forecast feature branch.

## Context

Analysts need one complete neighborhood temperature trajectory for the next
24 hours, including the current model hour, and a compact per-PLR comparison
with familiar local historical conditions. The existing project already
provides PLR names, population values, and historical median references, but
previously retained only six selected forecast lead times.

## Decision

Support exactly 25 forecast leads, 0 through 24, for a single DWD model run.
Keep the existing single-partition serving views unchanged and add separate
views for plotting observations and one-row-per-PLR summaries.

Publish only the newest model run for which every installed PLR has all 25
lead times. Express public timestamps in Berlin local time. Compare each
forecast temperature with its PLR-specific historical median for that local
calendar hour; preserve the sign in both maximum and summed differences.
Choose the earliest local timestamp whenever a maximum is tied.

Process manual horizons sequentially, skip already validated partitions, and
cap sensor source discovery and run submissions at five ready partitions per
evaluation.

## Consequences

A complete horizon exposes 13,550 plotting observations and 542 neighborhood
summary rows. A partially acquired newer model run cannot make a previously
complete horizon disappear. Population rejection remains explicit.

The forecast and summary views read only existing forecast facts and compact
historical-reference medians. Individual yearly HOSTRADA trajectories are a
separate, explicitly optional extension: they use dedicated storage, require
the original historical hourly observations, and never alter either existing
view or ordinary snapshot-based installation.
