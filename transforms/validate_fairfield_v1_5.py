#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from convert_fairfield_to_v1_5 import (
    line_length_m,
    load_geojson,
    point_to_linestring_distance_m,
    point_to_ring_distance_m,
    polygon_centroid,
    polygon_outer_ring,
    resolve_input_path,
)


GEOMETRY_NEAREST_PATTERN = re.compile(r"geometry_nearest_edge_distance_m=([0-9]+(?:\.[0-9]+)?)")


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_notes_kv(notes: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not notes:
        return parsed
    for part in notes.split(";"):
        cleaned = part.strip()
        if not cleaned or "=" not in cleaned:
            continue
        key, value = cleaned.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        parsed[key] = value
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Fairfield-specific validation checks on a converted pedestrian-network-schema v1.5 dataset.",
        epilog=(
            "Example:\n"
            "  python transforms/validate_fairfield_v1_5.py "
            "--input \"output/fairfield_pedestrian_network_v1_5_refined.geojson\" "
            "--schema \"pedestrian-network-schema-v1_5.json\" "
            "--report \"output/fairfield_validation_report_refined.json\""
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--binding-distance-threshold", type=float, default=5.0)
    parser.add_argument("--edge-length-diff-threshold", type=float, default=0.01)
    parser.add_argument("--object-edge-anchor-threshold", type=float, default=5.0)
    parser.add_argument("--object-zone-anchor-threshold", type=float, default=10.0)
    return parser.parse_args()


def validate_schema(dataset: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    validator = Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(dataset), key=lambda item: list(item.path)):
        path = ".".join(str(part) for part in error.path) or "<root>"
        errors.append({"path": path, "message": error.message})
    return {
        "passed": len(errors) == 0,
        "error_count": len(errors),
        "errors": errors[:100],
    }


def build_feature_indexes(
    features: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]], dict[str, int]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_role: dict[str, dict[str, dict[str, Any]]] = {
        "Node": {},
        "Edge": {},
        "Zone": {},
        "Object": {},
    }
    role_counts: collections.Counter[str] = collections.Counter()

    for feature in features:
        properties = feature.get("properties") or {}
        feature_id = properties.get("id")
        role = properties.get("feature_role")
        if feature_id is None or role is None:
            continue
        by_id[feature_id] = feature
        if role in by_role:
            by_role[role][feature_id] = feature
        role_counts[role] += 1

    return by_id, by_role, dict(role_counts)


def run_reference_checks(
    by_role: dict[str, dict[str, dict[str, Any]]],
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    missing_edge_node_refs = []
    missing_zone_edge_refs = []
    missing_object_anchor_refs = []

    node_ids = set(by_role["Node"])
    edge_ids = set(by_role["Edge"])
    zone_ids = set(by_role["Zone"])

    for edge_id, feature in by_role["Edge"].items():
        properties = feature["properties"]
        for endpoint_field in ("u_id", "v_id"):
            endpoint_id = properties.get(endpoint_field)
            if endpoint_id not in node_ids:
                item = {"edge_id": edge_id, "field": endpoint_field, "missing_node_id": endpoint_id}
                missing_edge_node_refs.append(item)
                errors.append({"type": "missing_node_reference", **item})

    for zone_id, feature in by_role["Zone"].items():
        properties = feature["properties"]
        if properties.get("zone_type") != "footpath_area":
            continue
        edge_id = properties.get("corresponding_edge_id")
        if edge_id not in edge_ids:
            item = {"zone_id": zone_id, "missing_edge_id": edge_id}
            missing_zone_edge_refs.append(item)
            errors.append({"type": "missing_corresponding_edge_reference", **item})

    for object_id, feature in by_role["Object"].items():
        properties = feature["properties"]
        anchor_specs = (
            ("anchor_node_id", node_ids),
            ("anchor_edge_id", edge_ids),
            ("anchor_zone_id", zone_ids),
        )
        for field_name, valid_ids in anchor_specs:
            anchor_id = properties.get(field_name)
            if anchor_id is None:
                continue
            if anchor_id not in valid_ids:
                item = {"object_id": object_id, "field": field_name, "missing_target_id": anchor_id}
                missing_object_anchor_refs.append(item)
                errors.append({"type": "missing_object_anchor_reference", **item})

    return {
        "missing_edge_node_reference_count": len(missing_edge_node_refs),
        "missing_zone_edge_reference_count": len(missing_zone_edge_refs),
        "missing_object_anchor_reference_count": len(missing_object_anchor_refs),
        "missing_edge_node_references": missing_edge_node_refs,
        "missing_zone_edge_references": missing_zone_edge_refs,
        "missing_object_anchor_references": missing_object_anchor_refs,
    }


def extract_geometry_nearest_bindings(
    zones: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    distance_threshold: float,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    bindings = []
    threshold_exceeded = []

    for zone_id, feature in zones.items():
        properties = feature["properties"]
        notes = properties.get("notes", "")
        parsed_notes = parse_notes_kv(notes)
        inference_method = parsed_notes.get("inference_method")
        has_old_style_geometry_distance = "geometry_nearest_edge_distance_m=" in notes
        if inference_method != "geometry_scored_nearest" and not has_old_style_geometry_distance:
            continue
        recorded_distance = None
        if parsed_notes.get("geometry_nearest_edge_distance_m") is not None:
            try:
                recorded_distance = float(parsed_notes["geometry_nearest_edge_distance_m"])
            except ValueError:
                recorded_distance = None
        elif has_old_style_geometry_distance:
            match = GEOMETRY_NEAREST_PATTERN.search(notes)
            recorded_distance = float(match.group(1)) if match else None
        ring = polygon_outer_ring(feature["geometry"])
        centroid = polygon_centroid(ring)
        edge_id = properties.get("corresponding_edge_id")
        edge_feature = edges.get(edge_id)
        recomputed_distance = None
        if edge_feature is not None:
            recomputed_distance = point_to_linestring_distance_m(
                centroid,
                edge_feature["geometry"]["coordinates"],
            )
        item = {
            "zone_id": zone_id,
            "polygon_source_id": parsed_notes.get("source_id") or extract_source_id_from_notes(notes),
            "source_type": parsed_notes.get("source_raw_type") or parsed_notes.get("source_type") or extract_source_type(properties),
            "corresponding_edge_id": edge_id,
            "inference_method": inference_method or ("legacy_geometry_nearest" if has_old_style_geometry_distance else None),
            "recorded_centroid_to_edge_distance_m": recorded_distance,
            "ring_to_edge_min_distance_m": safe_float_from_notes(parsed_notes.get("ring_to_edge_min_distance_m")),
            "midpoint_to_polygon_distance_m": safe_float_from_notes(parsed_notes.get("midpoint_to_polygon_distance_m")),
            "matching_score": safe_float_from_notes(parsed_notes.get("matching_score")),
            "recomputed_centroid_to_edge_distance_m": round(recomputed_distance, 3) if recomputed_distance is not None else None,
        }
        bindings.append(item)
        if recomputed_distance is not None and recomputed_distance > distance_threshold:
            threshold_exceeded.append(item)
            warnings.append({"type": "polygon_edge_binding_distance", **item})

    return {
        "geometry_nearest_binding_count": len(bindings),
        "distance_threshold_m": distance_threshold,
        "bindings_exceeding_threshold_count": len(threshold_exceeded),
        "bindings_exceeding_threshold": threshold_exceeded,
        "geometry_nearest_bindings": bindings,
    }


def extract_source_id_from_notes(notes: str) -> str | None:
    return parse_notes_kv(notes).get("source_id")


def safe_float_from_notes(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def extract_source_type(properties: dict[str, Any]) -> str | None:
    notes = properties.get("notes", "")
    parsed_notes = parse_notes_kv(notes)
    if parsed_notes.get("source_raw_type") is not None:
        return parsed_notes["source_raw_type"]
    if parsed_notes.get("source_type") is not None:
        return parsed_notes["source_type"]
    for part in notes.split(";"):
        cleaned = part.strip()
        if cleaned.startswith("source_raw_type="):
            return cleaned.split("=", 1)[1]
        if cleaned.startswith("source_type="):
            return cleaned.split("=", 1)[1]
    zone_type = properties.get("zone_type")
    if zone_type == "footpath_area":
        return "footpath"
    return None


def run_node_classification_checks(
    nodes: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    subtype_counts: collections.Counter[str] = collections.Counter()
    origin_counts: collections.Counter[str] = collections.Counter()
    origin_subtype_counts: collections.Counter[str] = collections.Counter()
    missing_node_origin = []

    for node_id, feature in nodes.items():
        properties = feature["properties"]
        notes = properties.get("notes", "")
        parsed_notes = parse_notes_kv(notes)
        node_type = properties.get("node_type")
        node_origin = parsed_notes.get("node_origin")

        if node_type is not None:
            subtype_counts[node_type] += 1
        if node_origin is not None:
            origin_counts[node_origin] += 1
            if node_type is not None:
                origin_subtype_counts[f"{node_origin}_{node_type}"] += 1
        else:
            item = {"node_id": node_id, "node_type": node_type, "notes": notes}
            missing_node_origin.append(item)
            warnings.append({"type": "missing_node_origin", **item})

    return {
        "node_count": len(nodes),
        "topology_derived_node_count": origin_counts.get("topology_derived", 0),
        "source_point_derived_node_count": origin_counts.get("source_point_derived", 0),
        "missing_node_origin_count": len(missing_node_origin),
        "missing_node_origin": missing_node_origin,
        "intersection_count": subtype_counts.get("intersection", 0),
        "curb_ramp_node_count": subtype_counts.get("curb_ramp_node", 0),
        "dead_end_count": subtype_counts.get("dead_end", 0),
        "other_node_count": subtype_counts.get("other", 0),
        "topology_derived_intersection_count": origin_subtype_counts.get("topology_derived_intersection", 0),
        "topology_derived_curb_ramp_node_count": origin_subtype_counts.get("topology_derived_curb_ramp_node", 0),
        "topology_derived_dead_end_count": origin_subtype_counts.get("topology_derived_dead_end", 0),
        "source_point_derived_curb_ramp_node_count": origin_subtype_counts.get(
            "source_point_derived_curb_ramp_node", 0
        ),
        "source_point_derived_other_count": origin_subtype_counts.get("source_point_derived_other", 0),
    }


def run_topology_curb_ramp_node_checks(
    nodes: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    topology_curb_ramp_nodes = []
    missing_inference_method = []
    missing_classification_reason = []
    missing_connected_edge_types = []
    crossing_relation_status_counts: collections.Counter[str] = collections.Counter()
    local_warnings = []

    for node_id, feature in nodes.items():
        properties = feature["properties"]
        if properties.get("node_type") != "curb_ramp_node":
            continue

        notes = properties.get("notes", "")
        parsed_notes = parse_notes_kv(notes)
        if parsed_notes.get("node_origin") != "topology_derived":
            continue

        item = {
            "node_id": node_id,
            "inference_method": parsed_notes.get("inference_method"),
            "classification_reason": parsed_notes.get("classification_reason"),
            "connected_edge_types": parsed_notes.get("connected_edge_types"),
            "crossing_relation_status": parsed_notes.get("crossing_relation_status"),
            "notes": notes,
        }
        topology_curb_ramp_nodes.append(item)

        if parsed_notes.get("crossing_relation_status") is not None:
            crossing_relation_status_counts[parsed_notes["crossing_relation_status"]] += 1
        if parsed_notes.get("inference_method") is None:
            missing_inference_method.append(item)
            warning_item = {"type": "topology_curb_ramp_node_missing_inference_method", **item}
            local_warnings.append(warning_item)
            warnings.append(warning_item)
        if parsed_notes.get("classification_reason") is None:
            missing_classification_reason.append(item)
            warning_item = {"type": "topology_curb_ramp_node_missing_classification_reason", **item}
            local_warnings.append(warning_item)
            warnings.append(warning_item)
        if parsed_notes.get("connected_edge_types") is None:
            missing_connected_edge_types.append(item)
            warning_item = {"type": "topology_curb_ramp_node_missing_connected_edge_types", **item}
            local_warnings.append(warning_item)
            warnings.append(warning_item)

    return {
        "topology_derived_curb_ramp_node_count": len(topology_curb_ramp_nodes),
        "missing_inference_method_count": len(missing_inference_method),
        "missing_classification_reason_count": len(missing_classification_reason),
        "missing_connected_edge_types_count": len(missing_connected_edge_types),
        "crossing_relation_status_counts": dict(crossing_relation_status_counts),
        "topology_derived_curb_ramp_nodes": topology_curb_ramp_nodes,
        "warnings": local_warnings,
    }


def run_placeholder_node_checks(
    nodes: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    ramp_placeholders = []
    other_placeholders = []
    legacy_crossing_placeholders = []

    for node_id, feature in nodes.items():
        properties = feature["properties"]
        if properties.get("node_type") != "other":
            continue
        notes = properties.get("notes", "")
        parsed_notes = parse_notes_kv(notes)
        source_type = parsed_notes.get("source_type")
        if source_type is None:
            if "source_type=ramp" in notes:
                source_type = "ramp"
            elif "source_type=crossing" in notes:
                source_type = "crossing"
        item = {
            "node_id": node_id,
            "connected_edge_count": properties.get("connected_edge_count"),
            "notes": notes,
            "source_type": source_type,
            "associated_edge_id": parsed_notes.get("associated_edge_id"),
            "inference_method": parsed_notes.get("inference_method"),
            "connected_edge_count_semantics": parsed_notes.get("connected_edge_count_semantics"),
        }
        if parsed_notes.get("connected_edge_count_semantics") == "schema_minimum_placeholder_not_confirmed_topology":
            warnings.append(
                {
                    "type": "placeholder_connected_edge_count_semantics",
                    "node_id": node_id,
                    "source_type": source_type,
                    "connected_edge_count": properties.get("connected_edge_count"),
                    "connected_edge_count_semantics": parsed_notes.get("connected_edge_count_semantics"),
                }
            )
        if source_type == "ramp":
            ramp_placeholders.append(item)
        elif source_type == "crossing":
            legacy_crossing_placeholders.append(item)
        else:
            other_placeholders.append(item)

    return {
        "other_node_count": len(ramp_placeholders) + len(other_placeholders) + len(legacy_crossing_placeholders),
        "ramp_placeholder_count": len(ramp_placeholders),
        "remaining_other_node_count": len(other_placeholders),
        "legacy_crossing_placeholder_count": len(legacy_crossing_placeholders),
        "crossing_origin_placeholder_expected_count": 0,
        "crossing_origin_placeholder_interpretation": (
            "Crossing-origin placeholder nodes are no longer expected output in the "
            "current Fairfield conversion flow. A count of 0 is the expected outcome."
        ),
        "other_other_node_count": len(other_placeholders),
        "ramp_placeholders": ramp_placeholders,
        "legacy_crossing_placeholders": legacy_crossing_placeholders,
        "other_nodes": other_placeholders,
    }


def run_ramp_node_outcome_checks(nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ramp_origin_curb_ramp_nodes = []
    ramp_origin_placeholders = []
    ramp_origin_other_nodes = []
    status_counts: collections.Counter[str] = collections.Counter()

    for node_id, feature in nodes.items():
        properties = feature["properties"]
        notes = properties.get("notes", "")
        parsed_notes = parse_notes_kv(notes)
        source_type = parsed_notes.get("source_type")
        if source_type != "ramp":
            continue

        item = {
            "node_id": node_id,
            "node_type": properties.get("node_type"),
            "associated_edge_id": parsed_notes.get("associated_edge_id"),
            "inference_method": parsed_notes.get("inference_method"),
            "crossing_relation_status": parsed_notes.get("crossing_relation_status"),
            "upgrade_reason": parsed_notes.get("upgrade_reason"),
            "notes": notes,
        }

        if properties.get("node_type") == "curb_ramp_node":
            ramp_origin_curb_ramp_nodes.append(item)
            if parsed_notes.get("crossing_relation_status") in {"resolved", "partial", "unresolved"}:
                status_counts[parsed_notes["crossing_relation_status"]] += 1
        elif properties.get("node_type") == "other":
            ramp_origin_placeholders.append(item)
        else:
            ramp_origin_other_nodes.append(item)

    return {
        "ramp_origin_curb_ramp_node_count": len(ramp_origin_curb_ramp_nodes),
        "ramp_origin_placeholder_count": len(ramp_origin_placeholders),
        "ramp_origin_other_node_count": len(ramp_origin_other_nodes),
        "ramp_origin_curb_ramp_node_resolved_count": status_counts.get("resolved", 0),
        "ramp_origin_curb_ramp_node_partial_count": status_counts.get("partial", 0),
        "ramp_origin_curb_ramp_node_unresolved_count": status_counts.get("unresolved", 0),
        "ramp_origin_curb_ramp_nodes": ramp_origin_curb_ramp_nodes,
        "ramp_origin_placeholders": ramp_origin_placeholders,
        "ramp_origin_other_nodes": ramp_origin_other_nodes,
    }


def run_curb_ramp_node_checks(
    nodes: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    point_derived_curb_ramp_nodes = []
    ramp_origin_curb_ramp_nodes = []
    missing_inference_method = []
    missing_crossing_relation_status = []
    missing_source_type = []
    local_warnings = []

    for node_id, feature in nodes.items():
        properties = feature["properties"]
        if properties.get("node_type") != "curb_ramp_node":
            continue

        notes = properties.get("notes", "")
        parsed_notes = parse_notes_kv(notes)
        if parsed_notes.get("node_origin") != "source_point_derived":
            continue
        item = {
            "node_id": node_id,
            "node_origin": parsed_notes.get("node_origin"),
            "source_type": parsed_notes.get("source_type"),
            "inference_method": parsed_notes.get("inference_method"),
            "crossing_relation_status": parsed_notes.get("crossing_relation_status"),
            "associated_edge_id": parsed_notes.get("associated_edge_id"),
            "notes": notes,
        }
        point_derived_curb_ramp_nodes.append(item)

        if parsed_notes.get("source_type") is None:
            missing_source_type.append(item)
            warning_item = {"type": "curb_ramp_node_missing_source_type", **item}
            local_warnings.append(warning_item)
            warnings.append(warning_item)

        if parsed_notes.get("source_type") == "ramp":
            ramp_origin_curb_ramp_nodes.append(item)
            if parsed_notes.get("inference_method") is None:
                missing_inference_method.append(item)
                warning_item = {"type": "curb_ramp_node_missing_inference_method", **item}
                local_warnings.append(warning_item)
                warnings.append(warning_item)
            if parsed_notes.get("crossing_relation_status") is None:
                missing_crossing_relation_status.append(item)
                warning_item = {"type": "curb_ramp_node_missing_crossing_relation_status", **item}
                local_warnings.append(warning_item)
                warnings.append(warning_item)

    return {
        "point_derived_curb_ramp_node_count": len(point_derived_curb_ramp_nodes),
        "ramp_origin_curb_ramp_node_count": len(ramp_origin_curb_ramp_nodes),
        "missing_inference_method_count": len(missing_inference_method),
        "missing_crossing_relation_status_count": len(missing_crossing_relation_status),
        "missing_source_type_count": len(missing_source_type),
        "point_derived_curb_ramp_nodes": point_derived_curb_ramp_nodes,
        "warnings": local_warnings,
    }


def run_edge_length_checks(
    edges: dict[str, dict[str, Any]],
    diff_threshold: float,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    total_abs_diff = 0.0
    max_abs_diff = 0.0
    over_threshold = []

    for edge_id, feature in edges.items():
        properties = feature["properties"]
        stored_length = float(properties.get("length_m", 0.0))
        derived_length = line_length_m(feature["geometry"]["coordinates"])
        abs_diff = abs(stored_length - derived_length)
        total_abs_diff += abs_diff
        max_abs_diff = max(max_abs_diff, abs_diff)
        if abs_diff > diff_threshold:
            item = {
                "edge_id": edge_id,
                "stored_length_m": round(stored_length, 6),
                "derived_length_m": round(derived_length, 6),
                "absolute_difference_m": round(abs_diff, 6),
            }
            over_threshold.append(item)
            warnings.append({"type": "edge_length_difference", **item})

    edge_count = len(edges)
    return {
        "edge_count": edge_count,
        "difference_threshold_m": diff_threshold,
        "mean_absolute_difference_m": (total_abs_diff / edge_count) if edge_count else 0.0,
        "max_absolute_difference_m": max_abs_diff,
        "edges_exceeding_threshold_count": len(over_threshold),
        "edges_exceeding_threshold": over_threshold,
    }


def run_object_anchor_checks(
    objects: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    zones: dict[str, dict[str, Any]],
    edge_threshold: float,
    zone_threshold: float,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    with_edge_anchor = 0
    with_zone_anchor = 0
    with_any_anchor = 0
    unanchored_objects = []
    distant_edge_anchors = []
    distant_zone_anchors = []

    for object_id, feature in objects.items():
        properties = feature["properties"]
        point = feature["geometry"]["coordinates"]
        has_anchor = False

        edge_id = properties.get("anchor_edge_id")
        if edge_id is not None:
            has_anchor = True
            with_edge_anchor += 1
            edge_feature = edges.get(edge_id)
            if edge_feature is not None:
                distance = point_to_linestring_distance_m(point, edge_feature["geometry"]["coordinates"])
                if distance > edge_threshold:
                    item = {
                        "object_id": object_id,
                        "anchor_edge_id": edge_id,
                        "distance_m": round(distance, 3),
                    }
                    distant_edge_anchors.append(item)
                    warnings.append({"type": "object_edge_anchor_distance", **item})

        zone_id = properties.get("anchor_zone_id")
        if zone_id is not None:
            has_anchor = True
            with_zone_anchor += 1
            zone_feature = zones.get(zone_id)
            if zone_feature is not None:
                ring = polygon_outer_ring(zone_feature["geometry"])
                distance = point_to_ring_distance_m(point, ring)
                if distance > zone_threshold:
                    item = {
                        "object_id": object_id,
                        "anchor_zone_id": zone_id,
                        "distance_m": round(distance, 3),
                    }
                    distant_zone_anchors.append(item)
                    warnings.append({"type": "object_zone_anchor_distance", **item})

        if properties.get("anchor_node_id") is not None:
            has_anchor = True

        if has_anchor:
            with_any_anchor += 1
        else:
            item = {"object_id": object_id}
            unanchored_objects.append(item)
            warnings.append({"type": "object_without_anchor", **item})

    object_count = len(objects)
    return {
        "object_count": object_count,
        "edge_anchor_coverage": (with_edge_anchor / object_count) if object_count else 0.0,
        "zone_anchor_coverage": (with_zone_anchor / object_count) if object_count else 0.0,
        "any_anchor_coverage": (with_any_anchor / object_count) if object_count else 0.0,
        "unanchored_object_count": len(unanchored_objects),
        "unanchored_objects": unanchored_objects,
        "edge_anchor_distance_threshold_m": edge_threshold,
        "zone_anchor_distance_threshold_m": zone_threshold,
        "distant_edge_anchor_count": len(distant_edge_anchors),
        "distant_edge_anchors": distant_edge_anchors,
        "distant_zone_anchor_count": len(distant_zone_anchors),
        "distant_zone_anchors": distant_zone_anchors,
    }


def build_summary(
    dataset: dict[str, Any],
    role_counts: dict[str, int],
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    node_classification_checks: dict[str, Any],
    ramp_node_outcome_checks: dict[str, Any],
) -> dict[str, Any]:
    features = dataset.get("features") or []
    return {
        "dataset_name": dataset.get("name"),
        "network_id": dataset.get("network_id"),
        "schema_version": dataset.get("schema_version"),
        "feature_count": len(features),
        "feature_role_counts": role_counts,
        "topology_derived_curb_ramp_node_count": node_classification_checks["topology_derived_curb_ramp_node_count"],
        "source_point_derived_curb_ramp_node_count": node_classification_checks[
            "source_point_derived_curb_ramp_node_count"
        ],
        "topology_derived_intersection_count": node_classification_checks["topology_derived_intersection_count"],
        "source_point_derived_other_count": node_classification_checks["source_point_derived_other_count"],
        "ramp_origin_curb_ramp_node_count": ramp_node_outcome_checks["ramp_origin_curb_ramp_node_count"],
        "ramp_origin_placeholder_count": ramp_node_outcome_checks["ramp_origin_placeholder_count"],
        "warning_count": len(warnings),
        "error_count": len(errors),
    }


def main() -> None:
    args = parse_args()
    schema_base = Path("schema")

    input_path = resolve_input_path(args.input)
    schema_path = resolve_input_path(args.schema, schema_base)
    report_path = Path(args.report)

    dataset = load_geojson(input_path)
    schema = load_geojson(schema_path)
    features = dataset.get("features") or []
    by_id, by_role, role_counts = build_feature_indexes(features)

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    schema_validation = validate_schema(dataset, schema)
    if not schema_validation["passed"]:
        for item in schema_validation["errors"]:
            errors.append({"type": "schema_validation", **item})

    reference_checks = run_reference_checks(by_role, warnings, errors)
    polygon_edge_binding_checks = extract_geometry_nearest_bindings(
        by_role["Zone"],
        by_role["Edge"],
        args.binding_distance_threshold,
        warnings,
    )
    node_classification_checks = run_node_classification_checks(by_role["Node"], warnings)
    topology_curb_ramp_node_checks = run_topology_curb_ramp_node_checks(by_role["Node"], warnings)
    placeholder_node_checks = run_placeholder_node_checks(by_role["Node"], warnings)
    ramp_node_outcome_checks = run_ramp_node_outcome_checks(by_role["Node"])
    curb_ramp_node_checks = run_curb_ramp_node_checks(by_role["Node"], warnings)
    edge_length_checks = run_edge_length_checks(
        by_role["Edge"],
        args.edge_length_diff_threshold,
        warnings,
    )
    object_anchor_checks = run_object_anchor_checks(
        by_role["Object"],
        by_role["Edge"],
        by_role["Zone"],
        args.object_edge_anchor_threshold,
        args.object_zone_anchor_threshold,
        warnings,
    )

    report = {
        "summary": build_summary(
            dataset,
            role_counts,
            warnings,
            errors,
            node_classification_checks,
            ramp_node_outcome_checks,
        ),
        "schema_validation": schema_validation,
        "reference_checks": reference_checks,
        "polygon_edge_binding_checks": polygon_edge_binding_checks,
        "node_classification_checks": node_classification_checks,
        "topology_curb_ramp_node_checks": topology_curb_ramp_node_checks,
        "ramp_node_outcome_checks": ramp_node_outcome_checks,
        "curb_ramp_node_checks": curb_ramp_node_checks,
        "placeholder_node_checks": placeholder_node_checks,
        "edge_length_checks": edge_length_checks,
        "object_anchor_checks": object_anchor_checks,
        "warnings": warnings,
        "errors": errors,
    }

    save_json(report_path, report)
    print(f"Saved validation report to {report_path}")
    print(
        f"Schema passed={schema_validation['passed']}; "
        f"warnings={len(warnings)}; errors={len(errors)}"
    )


if __name__ == "__main__":
    main()
