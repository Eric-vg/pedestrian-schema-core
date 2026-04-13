#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import copy
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from transforms.convert_fairfield_to_v1_5 import load_geojson, resolve_input_path


FOOTPATH_FAMILY_TYPES = {"footpath", "fp\\", "f"}
METADATA_BINDING_METHOD = "metadata_exact_match_6dp"


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Experimentally rekey Fairfield polygon source features using 6dp metadata binding to network edges.",
        epilog=(
            "Example:\n"
            "  python raw/fairfield_metadata_binding/rekey_fairfield_polygon_ids_from_metadata.py "
            "--network \"Fairfield_footpath_network_data 1.geojson\" "
            "--polygons \"Fairfield_footpath_polygon_data.geojson\" "
            "--safe-output \"output/Fairfield_footpath_polygon_data_rekeyed_safe.geojson\" "
            "--hard-output \"output/Fairfield_footpath_polygon_data_rekeyed_hard.geojson\" "
            "--report \"output/fairfield_polygon_rekey_report.json\""
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--network", required=True)
    parser.add_argument("--polygons", required=True)
    parser.add_argument("--safe-output", required=True)
    parser.add_argument("--hard-output", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def normalize_type(raw_type: Any) -> str:
    normalized = str(raw_type).strip().lower()
    if normalized in FOOTPATH_FAMILY_TYPES:
        return "footpath_family"
    return normalized


def normalize_float_for_match(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def build_line_groups(network_features: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    line_groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for feature in network_features:
        properties = feature.get("properties") or {}
        normalized_type = normalize_type(properties.get("type"))
        line_groups[normalized_type].append(feature)
    return line_groups


def find_unique_metadata_match(
    polygon_feature: dict[str, Any],
    line_groups: dict[str, list[dict[str, Any]]],
) -> tuple[Any | None, list[Any]]:
    properties = polygon_feature.get("properties") or {}
    normalized_polygon_type = normalize_type(properties.get("type"))
    polygon_distance_6dp = normalize_float_for_match(properties.get("distance"))
    polygon_width_6dp = normalize_float_for_match(properties.get("width"))
    candidate_line_ids = []

    for line_feature in line_groups.get(normalized_polygon_type, []):
        line_properties = line_feature.get("properties") or {}
        line_length_6dp = normalize_float_for_match(line_properties.get("length"))
        line_width_6dp = normalize_float_for_match(line_properties.get("width"))

        length_match = (
            polygon_distance_6dp is not None
            and line_length_6dp is not None
            and polygon_distance_6dp == line_length_6dp
        )
        width_match = (
            polygon_width_6dp is not None
            and line_width_6dp is not None
            and polygon_width_6dp == line_width_6dp
        )

        if length_match and width_match:
            candidate_line_ids.append(line_properties.get("id"))

    if len(candidate_line_ids) == 1:
        return candidate_line_ids[0], candidate_line_ids
    return None, candidate_line_ids


def clone_with_metadata_annotations(
    polygon_data: dict[str, Any],
    line_groups: dict[str, list[dict[str, Any]]],
    hard_rekey: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cloned = copy.deepcopy(polygon_data)
    per_polygon_results: list[dict[str, Any]] = []

    for feature in cloned.get("features") or []:
        properties = feature.get("properties") or {}
        original_polygon_id = properties.get("id")
        matched_edge_source_id, candidate_line_ids = find_unique_metadata_match(feature, line_groups)

        properties["original_polygon_id"] = original_polygon_id
        properties["matched_edge_source_id"] = matched_edge_source_id
        properties["metadata_binding_method"] = METADATA_BINDING_METHOD if matched_edge_source_id is not None else None

        if hard_rekey and matched_edge_source_id is not None:
            properties["id"] = matched_edge_source_id

        per_polygon_results.append(
            {
                "original_polygon_id": original_polygon_id,
                "matched_edge_source_id": matched_edge_source_id,
                "candidate_match_edge_ids": candidate_line_ids,
                "match_status": "matched" if matched_edge_source_id is not None else "unmatched",
            }
        )

    return cloned, per_polygon_results


def count_duplicated_values(values: list[Any]) -> tuple[int, list[Any]]:
    non_null_values = [value for value in values if value is not None]
    duplicated_values = sorted(
        value for value, count in collections.Counter(non_null_values).items() if count > 1
    )
    return len(duplicated_values), duplicated_values


def build_report(
    polygon_features: list[dict[str, Any]],
    safe_results: list[dict[str, Any]],
    hard_results: list[dict[str, Any]],
) -> dict[str, Any]:
    original_ids = [(feature.get("properties") or {}).get("id") for feature in polygon_features]
    original_null_count = sum(1 for value in original_ids if value is None)
    original_duplicate_count, original_duplicate_values = count_duplicated_values(original_ids)

    matched_results = [result for result in safe_results if result["match_status"] == "matched"]
    unmatched_results = [result for result in safe_results if result["match_status"] == "unmatched"]
    matched_edge_ids = [result["matched_edge_source_id"] for result in matched_results]
    rekey_duplicate_count, rekey_duplicate_values = count_duplicated_values(matched_edge_ids)
    rekey_unique_id_count = len({value for value in matched_edge_ids if value is not None})

    null_id_matched = sum(
        1
        for feature, result in zip(polygon_features, safe_results)
        if (feature.get("properties") or {}).get("id") is None and result["matched_edge_source_id"] is not None
    )

    original_id_counts = collections.Counter(value for value in original_ids if value is not None)
    duplicate_id_recovered = 0
    for feature, result in zip(polygon_features, hard_results):
        original_id = (feature.get("properties") or {}).get("id")
        if original_id is None:
            continue
        if original_id_counts.get(original_id, 0) <= 1:
            continue
        if result["matched_edge_source_id"] is not None:
            duplicate_id_recovered += 1

    return {
        "summary": {
            "polygon_total_count": len(polygon_features),
            "successful_match_count": len(matched_results),
            "failed_match_count": len(unmatched_results),
            "original_polygon_id_is_null_count": original_null_count,
            "original_polygon_id_duplicated_count": original_duplicate_count,
            "original_polygon_id_duplicated_values": original_duplicate_values,
            "rekeyed_unique_id_count": rekey_unique_id_count,
            "rekeyed_duplicate_id_count": rekey_duplicate_count,
            "rekeyed_duplicate_id_values": rekey_duplicate_values,
            "metadata_binding_method": METADATA_BINDING_METHOD,
        },
        "null_id_recovery": {
            "original_null_id_count": original_null_count,
            "recovered_with_matched_edge_source_id_count": null_id_matched,
        },
        "duplicate_id_resolution": {
            "original_duplicate_id_group_count": original_duplicate_count,
            "polygons_from_duplicate_id_groups_recovered_count": duplicate_id_recovered,
        },
        "unmatched_examples": unmatched_results[:50],
    }


def main() -> None:
    args = parse_args()
    raw_base = Path("data/raw/data base")

    network_path = resolve_input_path(args.network, raw_base)
    polygon_path = resolve_input_path(args.polygons, raw_base)
    safe_output_path = Path(args.safe_output)
    hard_output_path = Path(args.hard_output)
    report_path = Path(args.report)

    network_data = load_geojson(network_path)
    polygon_data = load_geojson(polygon_path)

    network_features = network_data.get("features") or []
    polygon_features = polygon_data.get("features") or []
    line_groups = build_line_groups(network_features)

    safe_polygon_data, safe_results = clone_with_metadata_annotations(
        polygon_data,
        line_groups,
        hard_rekey=False,
    )
    hard_polygon_data, hard_results = clone_with_metadata_annotations(
        polygon_data,
        line_groups,
        hard_rekey=True,
    )

    report = build_report(polygon_features, safe_results, hard_results)
    report["input_files"] = {
        "network": network_path.name,
        "polygons": polygon_path.name,
    }

    save_json(safe_output_path, safe_polygon_data)
    save_json(hard_output_path, hard_polygon_data)
    save_json(report_path, report)

    print(f"Saved safe rekeyed polygons to {safe_output_path}")
    print(f"Saved hard rekeyed polygons to {hard_output_path}")
    print(f"Saved rekey report to {report_path}")
    print(
        "Successful matches="
        f"{report['summary']['successful_match_count']}; "
        "failed matches="
        f"{report['summary']['failed_match_count']}"
    )


if __name__ == "__main__":
    main()
