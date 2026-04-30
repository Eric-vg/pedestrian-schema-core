# Pedestrian Schema Core Repository

## Overview

This repository is a narrow, working core for the Fairfield pedestrian network case study.

It contains:

- one active schema: `schema/pedestrian-network-schema-v1_5.json`
- one active conversion script: `transforms/convert_fairfield_to_v1_5.py`
- one active validation script: `transforms/validate_fairfield_v1_5.py`
- the retained Fairfield raw inputs under `data/raw/fairfield/`
- Fairfield-specific metadata-binding support scripts under `raw/fairfield_metadata_binding/`
- committed reference outputs under `output/`

The data model is GeoJSON plus JSON Schema, with four feature roles:

- `Node`
- `Edge`
- `Zone`
- `Object`

This is not a general framework or a packaged library. It is a small, script-driven repository centered on converting one retained raw Fairfield dataset into schema `v1.5.0` and checking the result.

## What Is Tracked

The tracked project files are intentionally limited:

- `schema/pedestrian-network-schema-v1_5.json`
- `transforms/convert_fairfield_to_v1_5.py`
- `transforms/validate_fairfield_v1_5.py`
- `raw/fairfield_metadata_binding/README.md`
- `raw/fairfield_metadata_binding/analyze_polygon_edge_metadata_binding.py`
- `raw/fairfield_metadata_binding/rekey_fairfield_polygon_ids_from_metadata.py`
- `data/raw/fairfield/Fairfield_footpath_network_data 1.geojson`
- `data/raw/fairfield/Fairfield_footpath_polygon_data.geojson`
- `data/raw/fairfield/Fairfield_objects_on_footpath_point_data.geojson`
- `output/fairfield_pedestrian_network_v1_5_refined.geojson`
- `output/fairfield_conversion_report_refined.json`
- `output/fairfield_validation_report_refined.json`
- `output/fairfield_polygon_edge_binding_review_refined.json`

`output/` is version-controlled in the current repository state. Running the conversion or validation commands against the same filenames will update tracked artifacts.

## Repository Layout

### `schema/`

Contains the current JSON Schema:

- `schema/pedestrian-network-schema-v1_5.json`

The schema is draft 2020-12 based and defines a top-level `FeatureCollection` with `NodeFeature`, `EdgeFeature`, `ZoneFeature`, and `ObjectFeature`.

### `transforms/`

Contains the active Fairfield workflow:

- `transforms/convert_fairfield_to_v1_5.py`
- `transforms/validate_fairfield_v1_5.py`

`convert_fairfield_to_v1_5.py`:

- reads raw Fairfield network, polygon, and point GeoJSON
- normalizes source types such as dirty footpath labels (`fp\`, `f`)
- derives topology nodes from line endpoints
- converts raw lines into schema edges
- binds polygons to edges
- maps points into object features or conservative node outcomes
- optionally validates the assembled dataset against the schema if `jsonschema` is installed
- writes:
  - converted dataset
  - conversion report
  - polygon-edge binding review

`validate_fairfield_v1_5.py`:

- validates the converted dataset against the schema
- checks cross-feature references
- checks polygon-edge binding distances
- summarizes node classification outcomes
- checks ramp-node and crossing-marking outcomes
- checks stored edge lengths against derived geometry lengths
- checks object anchors
- writes a structured validation report

### `data/raw/fairfield/`

Contains the retained raw Fairfield inputs:

- `Fairfield_footpath_network_data 1.geojson`
- `Fairfield_footpath_polygon_data.geojson`
- `Fairfield_objects_on_footpath_point_data.geojson`

Current source counts from the committed conversion report:

- network features: `233`
- polygon features: `162`
- point features: `990`

Source type counts:

- network: `160` `footpath`, `71` `crossing`, `1` `fp\`, `1` `f`
- polygons: `160` `footpath`, `1` `fp\`, `1` `f`
- points: `107` `ramp`, `36` `crossing`, `486` `pole`, `232` `tree`, `51` `bin`, `62` `bench`, `16` `bike_rack`

### `raw/fairfield_metadata_binding/`

Contains Fairfield-only support scripts for metadata-level polygon-edge binding work:

- `analyze_polygon_edge_metadata_binding.py`
- `rekey_fairfield_polygon_ids_from_metadata.py`

These scripts are not part of the main conversion path, but they document and test the metadata-first binding logic used to understand the raw Fairfield polygon-to-line relationship.

For the currently committed refined output, the conversion report records:

- `162 / 162` polygons matched
- `162 / 162` unique exact metadata matches at 6 decimal places
- binding success rate: `1.0`

### `output/`

Contains committed reference artifacts for the current refined Fairfield run:

- `output/fairfield_pedestrian_network_v1_5_refined.geojson`
- `output/fairfield_conversion_report_refined.json`
- `output/fairfield_validation_report_refined.json`
- `output/fairfield_polygon_edge_binding_review_refined.json`

This directory now serves two roles:

- a place where the scripts write outputs
- a committed reference snapshot of the current refined Fairfield result

If you rerun the scripts using these filenames, you are updating tracked files.

## Workflow Summary

The retained workflow is:

1. Read the three Fairfield raw source datasets.
2. Convert them into schema `v1.5.0`.
3. Write the dataset, conversion report, and polygon-edge binding review.
4. Run Fairfield-specific validation over the converted dataset.

The current conversion logic is deliberately conservative:

- ordinary two-edge topology connections remain `other` rather than being promoted to `intersection`
- crossing points are treated as auxiliary evidence rather than emitted as final crossing nodes
- ramp points may become `curb_ramp_node`, merge to an existing node, or remain conservative placeholders depending on nearby context

## Dependencies

- Python 3
- `jsonschema`

Install the required validator dependency with:

```bash
pip install jsonschema
```

Notes:

- `convert_fairfield_to_v1_5.py` can run without `jsonschema`, but its inline validation step will be skipped.
- `validate_fairfield_v1_5.py` requires `jsonschema`.
- Commands below assume execution from the repository root.

## How to Run

### Convert Fairfield to Schema v1.5

```bash
python transforms/convert_fairfield_to_v1_5.py --network "data/raw/fairfield/Fairfield_footpath_network_data 1.geojson" --polygons "data/raw/fairfield/Fairfield_footpath_polygon_data.geojson" --points "data/raw/fairfield/Fairfield_objects_on_footpath_point_data.geojson" --schema "schema/pedestrian-network-schema-v1_5.json" --output "output/fairfield_pedestrian_network_v1_5_refined.geojson" --report "output/fairfield_conversion_report_refined.json" --binding-review "output/fairfield_polygon_edge_binding_review_refined.json"
```

### Validate the Converted Dataset

```bash
python transforms/validate_fairfield_v1_5.py --input "output/fairfield_pedestrian_network_v1_5_refined.geojson" --schema "schema/pedestrian-network-schema-v1_5.json" --report "output/fairfield_validation_report_refined.json"
```

### Run Metadata-Binding Diagnostics

```bash
python raw/fairfield_metadata_binding/analyze_polygon_edge_metadata_binding.py --network "data/raw/fairfield/Fairfield_footpath_network_data 1.geojson" --polygons "data/raw/fairfield/Fairfield_footpath_polygon_data.geojson" --output "output/fairfield_metadata_binding_analysis.json"
```

```bash
python raw/fairfield_metadata_binding/rekey_fairfield_polygon_ids_from_metadata.py --network "data/raw/fairfield/Fairfield_footpath_network_data 1.geojson" --polygons "data/raw/fairfield/Fairfield_footpath_polygon_data.geojson" --safe-output "output/Fairfield_footpath_polygon_data_rekeyed_safe.geojson" --hard-output "output/Fairfield_footpath_polygon_data_rekeyed_hard.geojson" --report "output/fairfield_polygon_rekey_report.json"
```

## Current Refined Output

The committed refined dataset currently contains:

- `1450` total features
- `208` `Node`
- `233` `Edge`
- `162` `Zone`
- `847` `Object`

Current node subtype counts:

- `144` `curb_ramp_node`
- `24` `intersection`
- `38` `other`
- `2` `dead_end`

Current validation summary:

- schema validation passed
- `0` errors
- `7` warnings

Current warning breakdown:

- `5` ramp-origin placeholder nodes with `connected_edge_count=1`
- `2` objects without anchors

Additional committed summary values:

- topology-derived `curb_ramp_node`: `133`
- source-point-derived `curb_ramp_node`: `11`
- ramp-origin placeholder nodes: `5`
- crossing edges: `71`
- crossing edges with `crossing_marking=painted`: `33`
- crossing edges with `crossing_marking=other`: `38`

Crossing-point handling recorded by the conversion report:

- source crossing points: `36`
- crossing points near a crossing edge: `33`
- crossing edges upgraded to `crossing_marking=painted`: `33`
- crossing points without nearby crossing-edge support: `3`

Ramp-point handling recorded by the conversion report:

- ramp merged to existing node: `3`
- ramp merged at crossing endpoint: `88`
- ramp upgraded to `curb_ramp_node`: `11`
- ramp left as placeholder node: `5`

## Limitations

- The repository is Fairfield-specific. The rules should not be treated as generally valid for all pedestrian datasets.
- Some ramp-origin placeholders remain unresolved in the committed refined output.
- Two committed object features are still unanchored.
- A substantial part of the workflow logic is heuristic, especially around local point-to-edge and point-to-zone interpretation.
- `output/` is now tracked, so routine script runs can create large diffs in committed artifacts.
