# Pedestrian Schema Core Repository

## Overview

This repository is a cleaned core repository for the pedestrian schema project. It retains only the materials required to document, inspect, and reproduce the Fairfield case study workflow in its current form.

The repository focuses on a pedestrian network data model expressed through GeoJSON and JSON Schema, using a Node-Edge-Zone representation of pedestrian infrastructure. Within this core archive, the Fairfield case study serves as the main worked example for the conversion and validation workflow associated with schema version 1.5.

This is not the full research process repository. Older schema versions, historical conversion scripts, superseded case study outputs, and legacy validator files have been intentionally removed so that the remaining contents reflect the current core artifacts only.

## Repository Structure

### `schema/`

Contains the current schema definition:

- `schema/pedestrian-network-schema-v1_5.json`: JSON Schema for the pedestrian network data model used by the retained Fairfield workflow.

### `transforms/`

Contains the active workflow scripts:

- `transforms/convert_fairfield_to_v1_5.py`: converts the retained Fairfield raw inputs into the schema v1.5 dataset and produces supporting conversion outputs.
- `transforms/validate_fairfield_v1_5.py`: validates a converted Fairfield dataset against the schema and writes a validation report.

### `data/raw/fairfield/`

Contains the three retained raw Fairfield input datasets used by the current case study workflow:

- footpath network input
- footpath polygon input
- objects-on-footpath point input

### `raw/fairfield_metadata_binding/`

Contains supporting analysis tools for metadata-first polygon-edge binding. These files are retained because they support interpretation and inspection of the Fairfield polygon-edge association workflow.

### `results/fairfield_case_study/`

Contains the retained formal Fairfield case study outputs. These are the reference artifacts preserved in this core repository as the current case study results.

### `output/`

Intended as a temporary run-output directory for generated files such as converted datasets, reports, and binding review artifacts. This directory is distinct from `results/fairfield_case_study/`: `output/` is for script-generated working outputs, while `results/fairfield_case_study/` stores the retained case study results. Temporary output files are typically not intended for version control.

## Core Workflow

The current workflow represented by this repository is:

1. Start from the retained raw Fairfield inputs in `data/raw/fairfield/`.
2. Convert the raw network, polygon, and point datasets into the pedestrian schema v1.5 representation.
3. Validate the converted dataset against `schema/pedestrian-network-schema-v1_5.json`.
4. Use metadata-first polygon-edge binding support from `raw/fairfield_metadata_binding/` to inspect and stabilize polygon-edge associations within the Fairfield case study.

## How to Run

The commands below assume execution from the repository root.

### Conversion

```bash
python transforms/convert_fairfield_to_v1_5.py --network "data/raw/fairfield/Fairfield_footpath_network_data 1.geojson" --polygons "data/raw/fairfield/Fairfield_footpath_polygon_data.geojson" --points "data/raw/fairfield/Fairfield_objects_on_footpath_point_data.geojson" --schema "schema/pedestrian-network-schema-v1_5.json" --output "output/fairfield_pedestrian_network_v1_5.geojson" --report "output/fairfield_conversion_report.json" --binding-review "output/fairfield_polygon_edge_binding_review.json"
```

### Validation

```bash
python transforms/validate_fairfield_v1_5.py --input "output/fairfield_pedestrian_network_v1_5.geojson" --schema "schema/pedestrian-network-schema-v1_5.json" --report "output/fairfield_validation_report.json"
```

## Retained Fairfield Case Study Outputs

The directory `results/fairfield_case_study/` contains the retained Fairfield final output artifacts preserved in this repository. These files are kept as reference artifacts representing the current retained case study state.

They should not be interpreted as the only valid filenames that every script run must reproduce exactly. Instead, they provide a stable reference set for checking the current conversion and validation outcome of the Fairfield case study.

## Current Outcomes

At the current retained stage of the Fairfield case study, the workflow demonstrates:

- stable polygon-edge binding in the retained conversion pipeline
- refinement of ramp-related point inputs into `curb_ramp_node`
- contraction of `intersection` where required by the current workflow logic
- removal of crossing points from the final node output
- substantially reduced unresolved point-derived node noise compared with earlier intermediate workflow states

## Known Limitations

- Some redundant `footpath` segments remain in the retained Fairfield output.
- Topology-derived `curb_ramp_node` classification still depends on heuristic logic rather than a universally reliable rule.
- The Fairfield case study should not be treated as a universally generalizable rule set for all pedestrian network datasets.

## Future Work

- Reduce or consolidate remaining redundant `footpath` segments.
- Further formalize curb ramp classification beyond the current heuristic approach.
- Test the schema and workflow against additional case study areas beyond Fairfield.
- Clarify which transformation rules are Fairfield-specific and which can be generalized.
- Strengthen documentation of intermediate decision logic for conversion and validation reporting.
