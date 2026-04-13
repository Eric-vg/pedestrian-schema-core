#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from transforms.convert_fairfield_to_v1_5 import load_geojson, resolve_input_path


FOOTPATH_FAMILY_TYPES = {"footpath", "fp\\", "f"}


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze metadata-level binding signals between Fairfield polygon and line source datasets.",
        epilog=(
            "Example:\n"
            "  python raw/fairfield_metadata_binding/analyze_polygon_edge_metadata_binding.py "
            "--network \"Fairfield_footpath_network_data 1.geojson\" "
            "--polygons \"Fairfield_footpath_polygon_data.geojson\" "
            "--output \"output/fairfield_metadata_binding_analysis.json\""
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--network", required=True)
    parser.add_argument("--polygons", required=True)
    parser.add_argument("--output", required=True)
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


def safe_value(value: Any) -> Any:
    # Preserve exact JSON numeric/string equality semantics for this diagnostic.
    return value


def build_feature_stats(features: list[dict[str, Any]], value_fields: list[str]) -> dict[str, int]:
    stats = {"feature_count": len(features)}
    for field_name in value_fields:
        stats[f"with_{field_name}_count"] = sum(
            1 for feature in features if (feature.get("properties") or {}).get(field_name) is not None
        )
    return stats


def classify_polygon_result(result: dict[str, Any]) -> str:
    if result["exact_both_match_count"] == 1:
        return "unique_exact_both_match"
    if result["exact_both_match_count"] > 1:
        return "multiple_exact_both_matches"
    return "no_exact_both_match"


def analyze_metadata_binding(
    network_features: list[dict[str, Any]],
    polygon_features: list[dict[str, Any]],
) -> dict[str, Any]:
    polygon_results: list[dict[str, Any]] = []
    unique_exact_both_matches: list[dict[str, Any]] = []
    multiple_exact_both_matches: list[dict[str, Any]] = []
    no_exact_both_match_examples: list[dict[str, Any]] = []

    polygon_ids = [(feature.get("properties") or {}).get("id") for feature in polygon_features]
    non_null_polygon_ids = [polygon_id for polygon_id in polygon_ids if polygon_id is not None]
    duplicated_polygon_ids = sorted(
        polygon_id for polygon_id, count in collections.Counter(non_null_polygon_ids).items() if count > 1
    )

    normalized_line_groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for feature in network_features:
        properties = feature.get("properties") or {}
        normalized_type = normalize_type(properties.get("type"))
        normalized_line_groups[normalized_type].append(feature)

    for feature in polygon_features:
        properties = feature.get("properties") or {}
        polygon_id = properties.get("id")
        polygon_type = properties.get("type")
        normalized_polygon_type = normalize_type(polygon_type)
        polygon_distance = safe_value(properties.get("distance"))
        polygon_width = safe_value(properties.get("width"))
        polygon_distance_6dp = normalize_float_for_match(polygon_distance)
        polygon_width_6dp = normalize_float_for_match(polygon_width)

        candidate_lines = normalized_line_groups.get(normalized_polygon_type, [])
        exact_length_match_edge_ids = []
        exact_width_match_edge_ids = []
        exact_both_match_edge_ids = []

        for line_feature in candidate_lines:
            line_properties = line_feature.get("properties") or {}
            line_id = line_properties.get("id")
            line_length = safe_value(line_properties.get("length"))
            line_width = safe_value(line_properties.get("width"))
            line_length_6dp = normalize_float_for_match(line_length)
            line_width_6dp = normalize_float_for_match(line_width)

            length_match = polygon_distance_6dp is not None and line_length_6dp is not None and polygon_distance_6dp == line_length_6dp
            width_match = polygon_width_6dp is not None and line_width_6dp is not None and polygon_width_6dp == line_width_6dp

            if length_match:
                exact_length_match_edge_ids.append(line_id)
            if width_match:
                exact_width_match_edge_ids.append(line_id)
            if length_match and width_match:
                exact_both_match_edge_ids.append(line_id)

        result = {
            "polygon_id": polygon_id,
            "polygon_type": polygon_type,
            "normalized_polygon_type": normalized_polygon_type,
            "polygon_distance": polygon_distance,
            "polygon_width": polygon_width,
            "polygon_distance_6dp": polygon_distance_6dp,
            "polygon_width_6dp": polygon_width_6dp,
            "candidate_line_count": len(candidate_lines),
            "exact_length_match_count": len(exact_length_match_edge_ids),
            "exact_width_match_count": len(exact_width_match_edge_ids),
            "exact_both_match_count": len(exact_both_match_edge_ids),
            "exact_both_match_edge_ids": exact_both_match_edge_ids,
            "match_status": "",
        }
        result["match_status"] = classify_polygon_result(result)
        polygon_results.append(result)

        if result["match_status"] == "unique_exact_both_match":
            unique_exact_both_matches.append(result)
        elif result["match_status"] == "multiple_exact_both_matches":
            multiple_exact_both_matches.append(result)
        elif len(no_exact_both_match_examples) < 50:
            no_exact_both_match_examples.append(result)

    summary = {
        "polygon_total_count": len(polygon_features),
        "line_total_count": len(network_features),
        "polygon_with_distance_count": sum(
            1 for feature in polygon_features if (feature.get("properties") or {}).get("distance") is not None
        ),
        "polygon_with_width_count": sum(
            1 for feature in polygon_features if (feature.get("properties") or {}).get("width") is not None
        ),
        "line_with_length_count": sum(
            1 for feature in network_features if (feature.get("properties") or {}).get("length") is not None
        ),
        "line_with_width_count": sum(
            1 for feature in network_features if (feature.get("properties") or {}).get("width") is not None
        ),
        "unique_exact_both_match_count": len(unique_exact_both_matches),
        "multiple_exact_both_matches_count": len(multiple_exact_both_matches),
        "no_exact_both_match_count": sum(
            1 for result in polygon_results if result["match_status"] == "no_exact_both_match"
        ),
        "polygon_id_is_null_count": sum(1 for polygon_id in polygon_ids if polygon_id is None),
        "polygon_id_duplicated_count": len(duplicated_polygon_ids),
        "polygon_id_duplicated_values": duplicated_polygon_ids,
    }

    return {
        "summary": summary,
        "matching_rule": {
            "type": "rounded_exact_match_6dp",
            "description": "Metadata equality is evaluated after rounding both polygon and line numeric values to 6 decimal places.",
            "distance_length_rule": "round(polygon.distance, 6) == round(line.length, 6)",
            "width_width_rule": "round(polygon.width, 6) == round(line.width, 6)",
        },
        "polygon_level_results": polygon_results,
        "unique_exact_both_matches": unique_exact_both_matches,
        "multiple_exact_both_matches": multiple_exact_both_matches,
        "no_exact_both_match_examples": no_exact_both_match_examples,
        "input_field_coverage": {
            "polygon_features": build_feature_stats(polygon_features, ["id", "distance", "width"]),
            "line_features": build_feature_stats(network_features, ["id", "length", "width"]),
        },
    }


def main() -> None:
    args = parse_args()
    raw_base = Path("data/raw/data base")

    network_path = resolve_input_path(args.network, raw_base)
    polygon_path = resolve_input_path(args.polygons, raw_base)
    output_path = Path(args.output)

    network_data = load_geojson(network_path)
    polygon_data = load_geojson(polygon_path)

    network_features = network_data.get("features") or []
    polygon_features = polygon_data.get("features") or []

    report = analyze_metadata_binding(network_features, polygon_features)
    report["input_files"] = {
        "network": network_path.name,
        "polygons": polygon_path.name,
    }

    save_json(output_path, report)
    print(f"Saved metadata binding analysis to {output_path}")
    print(
        "Unique exact both matches="
        f"{report['summary']['unique_exact_both_match_count']}; "
        "multiple exact both matches="
        f"{report['summary']['multiple_exact_both_matches_count']}; "
        "no exact both match="
        f"{report['summary']['no_exact_both_match_count']}"
    )


if __name__ == "__main__":
    main()
