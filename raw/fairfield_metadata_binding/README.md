# Fairfield Metadata Binding Tools

This directory contains Fairfield-specific polygon-edge metadata matching and rekey experiment scripts.
They are used to analyze and recover metadata-level binding relationships between the raw polygon and line source datasets.

## Scripts

`analyze_polygon_edge_metadata_binding.py`

- Checks the metadata matching potential between `polygon.distance` / `polygon.width` and `line.length` / `line.width`.
- Used to assess whether polygon-edge bindings can be recovered from source metadata before relying on geometric inference.

`rekey_fairfield_polygon_ids_from_metadata.py`

- Applies the verified metadata exact-match rule to the raw polygon dataset.
- Produces safe and hard rekey experiment outputs for inspecting how null and duplicated polygon ids behave after metadata-based recovery.

## Safe vs Hard Rekey

`safe`

- Preserves the original polygon `id`
- Adds `original_polygon_id`, `matched_edge_source_id`, and `metadata_binding_method`

`hard`

- Rewrites polygon `id` to the matched edge source id
- Still preserves `original_polygon_id`, `matched_edge_source_id`, and `metadata_binding_method`

## Minimal Commands

```bash
python raw/fairfield_metadata_binding/analyze_polygon_edge_metadata_binding.py --network "Fairfield_footpath_network_data 1.geojson" --polygons "Fairfield_footpath_polygon_data.geojson" --output "output/fairfield_metadata_binding_analysis_6dp.json"
```

```bash
python raw/fairfield_metadata_binding/rekey_fairfield_polygon_ids_from_metadata.py --network "Fairfield_footpath_network_data 1.geojson" --polygons "Fairfield_footpath_polygon_data.geojson" --safe-output "output/Fairfield_footpath_polygon_data_rekeyed_safe.geojson" --hard-output "output/Fairfield_footpath_polygon_data_rekeyed_hard.geojson" --report "output/fairfield_polygon_rekey_report.json"
```
