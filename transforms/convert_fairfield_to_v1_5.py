#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - optional dependency
    Draft202012Validator = None


DIRTY_FOOTPATH_TYPES = {"fp\\", "f"}
OBJECT_TYPE_MAP = {
    "pole": "pole",
    "tree": "tree",
    "bench": "bench",
    "bin": "bin",
    "bike_rack": "bike_rack",
}
POINT_PLACEHOLDER_TYPES = {"ramp", "crossing"}
ZONE_ANCHOR_MAX_DISTANCE_M = 10.0
EDGE_ANCHOR_MAX_DISTANCE_M = 5.0
POINT_NODE_MERGE_MAX_DISTANCE_M = 2.0
POINT_TO_CROSSING_EDGE_MAX_DISTANCE_M = 3.0
POINT_TO_CROSSING_ENDPOINT_MAX_DISTANCE_M = 3.0
POINT_TO_RAMP_CONTEXT_EDGE_MAX_DISTANCE_M = 2.5
POINT_TO_RAMP_CONTEXT_ZONE_MAX_DISTANCE_M = 2.5


def load_geojson(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_source_type(value: Any) -> str:
    return str(value).strip().lower()


def normalize_footpath_family_type(value: Any) -> str:
    normalized = normalize_source_type(value)
    if normalized == "footpath" or normalized in DIRTY_FOOTPATH_TYPES:
        return "footpath_family"
    return normalized


def normalize_source_id(value: Any) -> str | None:
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or None
    if math.isfinite(as_float) and as_float.is_integer():
        return str(int(as_float))
    return str(as_float)


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(result):
        return result
    return None


def normalize_float_6dp(value: Any) -> float | None:
    normalized = safe_float(value)
    if normalized is None:
        return None
    return round(normalized, 6)


def coordinate_key(coord: list[float], ndigits: int = 8) -> tuple[float, float]:
    return (round(float(coord[0]), ndigits), round(float(coord[1]), ndigits))


def project_lon_lat(coord: list[float] | tuple[float, float], lat0_deg: float) -> tuple[float, float]:
    lon = float(coord[0])
    lat = float(coord[1])
    lat0_rad = math.radians(lat0_deg)
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = math.cos(lat0_rad) * 111_320.0
    return lon * meters_per_deg_lon, lat * meters_per_deg_lat


def distance_between_points_m(a: list[float] | tuple[float, float], b: list[float] | tuple[float, float]) -> float:
    lat0 = (float(a[1]) + float(b[1])) / 2.0
    ax, ay = project_lon_lat(a, lat0)
    bx, by = project_lon_lat(b, lat0)
    return math.hypot(ax - bx, ay - by)


def line_length_m(coords: list[list[float]]) -> float:
    if len(coords) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(coords, coords[1:]):
        total += distance_between_points_m(a, b)
    return total


def point_to_segment_distance_m(point: list[float], start: list[float], end: list[float]) -> float:
    lat0 = (float(point[1]) + float(start[1]) + float(end[1])) / 3.0
    px, py = project_lon_lat(point, lat0)
    sx, sy = project_lon_lat(start, lat0)
    ex, ey = project_lon_lat(end, lat0)
    dx = ex - sx
    dy = ey - sy
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - sx, py - sy)
    t = ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = sx + t * dx
    cy = sy + t * dy
    return math.hypot(px - cx, py - cy)


def point_to_linestring_distance_m(point: list[float], coords: list[list[float]]) -> float:
    if len(coords) == 1:
        return distance_between_points_m(point, coords[0])
    return min(point_to_segment_distance_m(point, a, b) for a, b in zip(coords, coords[1:]))


def linestring_midpoint(coords: list[list[float]]) -> list[float]:
    if not coords:
        return [0.0, 0.0]
    if len(coords) == 1:
        return [float(coords[0][0]), float(coords[0][1])]

    segment_lengths = [distance_between_points_m(a, b) for a, b in zip(coords, coords[1:])]
    total_length = sum(segment_lengths)
    if total_length == 0.0:
        return [float(coords[0][0]), float(coords[0][1])]

    halfway = total_length / 2.0
    traversed = 0.0
    for (start, end), segment_length in zip(zip(coords, coords[1:]), segment_lengths):
        if traversed + segment_length >= halfway:
            fraction = (halfway - traversed) / segment_length if segment_length else 0.0
            return [
                float(start[0]) + (float(end[0]) - float(start[0])) * fraction,
                float(start[1]) + (float(end[1]) - float(start[1])) * fraction,
            ]
        traversed += segment_length
    return [float(coords[-1][0]), float(coords[-1][1])]


def sample_linestring_points(coords: list[list[float]]) -> list[list[float]]:
    if not coords:
        return []
    if len(coords) == 1:
        return [[float(coords[0][0]), float(coords[0][1])]]
    return [
        [float(coords[0][0]), float(coords[0][1])],
        linestring_midpoint(coords),
        [float(coords[-1][0]), float(coords[-1][1])],
    ]


def polygon_outer_ring(geometry: dict[str, Any]) -> list[list[float]]:
    coords = geometry.get("coordinates") or []
    return coords[0] if coords else []


def polygon_centroid(ring: list[list[float]]) -> list[float]:
    if not ring:
        return [0.0, 0.0]
    unique = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    if not unique:
        unique = ring
    lon = sum(float(point[0]) for point in unique) / len(unique)
    lat = sum(float(point[1]) for point in unique) / len(unique)
    return [lon, lat]


def point_in_ring(point: list[float], ring: list[list[float]]) -> bool:
    if len(ring) < 3:
        return False
    x = float(point[0])
    y = float(point[1])
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        intersects = ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
        )
        if intersects:
            inside = not inside
        previous = current
    return inside


def point_to_ring_distance_m(point: list[float], ring: list[list[float]]) -> float:
    if point_in_ring(point, ring):
        return 0.0
    if len(ring) < 2:
        return float("inf")
    return min(point_to_segment_distance_m(point, a, b) for a, b in zip(ring, ring[1:]))


def ring_to_linestring_min_distance_m(ring: list[list[float]], coords: list[list[float]]) -> float:
    if not ring or not coords:
        return float("inf")
    return min(point_to_linestring_distance_m(point, coords) for point in ring)


def format_note_pairs(pairs: list[tuple[str, Any]]) -> str:
    parts = []
    for key, value in pairs:
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return "; ".join(parts)


def resolve_input_path(raw: str, fallback_dir: Path | None = None) -> Path:
    candidate = Path(raw)
    if candidate.exists():
        return candidate
    if fallback_dir is not None:
        candidate = fallback_dir / raw
        if candidate.exists():
            return candidate
    raise FileNotFoundError(raw)


def build_source_stats(features: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts = collections.Counter(
        normalize_source_type((feature.get("properties") or {}).get("type"))
        for feature in features
    )
    geometry_counts = collections.Counter(
        ((feature.get("geometry") or {}).get("type") or "<missing>")
        for feature in features
    )
    return {
        "feature_count": len(features),
        "geometry_type_counts": dict(geometry_counts),
        "source_type_counts": dict(type_counts),
    }


def map_source_line_type_to_edge_type(source_type: str) -> str:
    if source_type == "crossing":
        return "crossing"
    if source_type == "footpath" or source_type in DIRTY_FOOTPATH_TYPES:
        return "footpath"
    return "other"


def classify_topology_node(
    connected_edge_count: int,
    connected_edge_types: list[str],
) -> tuple[str, str, str, str | None]:
    unique_types = sorted(set(connected_edge_types))
    has_crossing = "crossing" in unique_types
    has_path = any(edge_type in {"footpath", "mixedpath"} for edge_type in unique_types)

    if connected_edge_count <= 1:
        return (
            "dead_end",
            "topology_dead_end_classification",
            "single_incident_edge",
            None,
        )

    if has_crossing and has_path:
        return (
            "curb_ramp_node",
            "topology_curb_ramp_node_classification",
            "topology_crossing_path_transition_connection",
            "resolved",
        )

    return (
        "intersection",
        "topology_intersection_classification",
        "ordinary_path_connection_without_explicit_curb_transition_signal",
        None,
    )


def build_nodes_from_network_edges(
    network_features: list[dict[str, Any]],
    report: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[tuple[float, float], str],
    dict[str, list[float]],
    dict[str, int],
]:
    node_id_by_coord: dict[tuple[float, float], str] = {}
    node_coords_by_id: dict[str, list[float]] = {}
    connected_counts: collections.Counter[str] = collections.Counter()
    connected_edge_types_by_node: dict[str, list[str]] = collections.defaultdict(list)
    connected_edge_ids_by_node: dict[str, list[str]] = collections.defaultdict(list)
    edge_contexts: list[dict[str, Any]] = []
    node_classification_summary = report["node_classification_summary"]

    for index, feature in enumerate(network_features, start=1):
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        source_type = normalize_source_type(props.get("type"))
        edge_type = map_source_line_type_to_edge_type(source_type)
        start_key = coordinate_key(coords[0])
        end_key = coordinate_key(coords[-1])
        if start_key not in node_id_by_coord:
            node_id_by_coord[start_key] = f"n{len(node_id_by_coord) + 1}"
            node_coords_by_id[node_id_by_coord[start_key]] = [float(coords[0][0]), float(coords[0][1])]
        if end_key not in node_id_by_coord:
            node_id_by_coord[end_key] = f"n{len(node_id_by_coord) + 1}"
            node_coords_by_id[node_id_by_coord[end_key]] = [float(coords[-1][0]), float(coords[-1][1])]
        u_id = node_id_by_coord[start_key]
        v_id = node_id_by_coord[end_key]
        connected_counts[u_id] += 1
        connected_counts[v_id] += 1
        connected_edge_types_by_node[u_id].append(edge_type)
        connected_edge_types_by_node[v_id].append(edge_type)
        connected_edge_ids_by_node[u_id].append(f"e{index}")
        connected_edge_ids_by_node[v_id].append(f"e{index}")
        edge_contexts.append(
            {
                "source_feature": feature,
                "source_id": normalize_source_id(props.get("id")),
                "source_type": source_type,
                "edge_type": edge_type,
                "coords": [[float(x), float(y)] for x, y, *_ in coords],
                "u_id": u_id,
                "v_id": v_id,
                "edge_id": f"e{index}",
            }
        )

    node_features: list[dict[str, Any]] = []
    for node_id, coords in node_coords_by_id.items():
        count = int(connected_counts[node_id])
        connected_edge_types = connected_edge_types_by_node[node_id]
        node_type, inference_method, classification_reason, crossing_relation_status = classify_topology_node(
            count,
            connected_edge_types,
        )
        node_notes = format_note_pairs(
            [
                ("node_origin", "topology_derived"),
                ("inference_method", inference_method),
                ("classification_reason", classification_reason),
                ("connected_edge_types", ",".join(sorted(set(connected_edge_types)))),
                ("connected_edge_ids", ",".join(sorted(connected_edge_ids_by_node[node_id]))),
                ("crossing_relation_status", crossing_relation_status),
            ]
        )
        node_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coords},
                "properties": {
                    "id": node_id,
                    "feature_role": "Node",
                    "node_type": node_type,
                    "connected_edge_count": count,
                    "source": "Fairfield_footpath_network_data 1.geojson",
                    "status": "existing",
                    "confidence": 1.0,
                    "notes": node_notes,
                },
            }
        )
        node_classification_summary[f"topology_derived_{node_type}"] += 1

    return node_features, edge_contexts, node_id_by_coord, node_coords_by_id, dict(connected_counts)


def edge_notes(source_type: str, source_id: str | None, extra: str | None = None) -> str | None:
    parts: list[tuple[str, Any]] = []
    if source_id is not None:
        parts.append(("source_id", source_id))
    if source_type in DIRTY_FOOTPATH_TYPES:
        parts.append(("source_raw_type", source_type))
        parts.append(("dirty_source_type_normalized_to", "footpath"))
    if extra:
        parts.append(("extra_note", extra))
    return format_note_pairs(parts) or None


def build_edge_metadata_by_id(edge_contexts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    edge_metadata_by_id: dict[str, dict[str, Any]] = {}
    for context in edge_contexts:
        props = context["source_feature"].get("properties") or {}
        edge_metadata_by_id[context["edge_id"]] = {
            "edge_id": context["edge_id"],
            "source_id": context["source_id"],
            "source_type": context["source_type"],
            "normalized_family_type": normalize_footpath_family_type(context["source_type"]),
            "source_length": safe_float(props.get("length")),
            "source_width": safe_float(props.get("width")),
        }
    return edge_metadata_by_id


def find_metadata_exact_match_edge(
    polygon_properties: dict[str, Any],
    edge_metadata_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    polygon_distance = safe_float(polygon_properties.get("distance"))
    polygon_width = safe_float(polygon_properties.get("width"))
    polygon_distance_6dp = normalize_float_6dp(polygon_distance)
    polygon_width_6dp = normalize_float_6dp(polygon_width)
    polygon_type_family = normalize_footpath_family_type(polygon_properties.get("type"))

    if polygon_type_family != "footpath_family":
        return {
            "candidate_edge_ids": [],
            "candidate_count": 0,
            "chosen_edge_id": None,
            "polygon_distance": polygon_distance,
            "polygon_width": polygon_width,
            "polygon_distance_6dp": polygon_distance_6dp,
            "polygon_width_6dp": polygon_width_6dp,
        }

    candidate_records = []
    for metadata in edge_metadata_by_id.values():
        if metadata["normalized_family_type"] != "footpath_family":
            continue
        edge_length_6dp = normalize_float_6dp(metadata["source_length"])
        edge_width_6dp = normalize_float_6dp(metadata["source_width"])
        if (
            polygon_distance_6dp is not None
            and polygon_width_6dp is not None
            and edge_length_6dp is not None
            and edge_width_6dp is not None
            and polygon_distance_6dp == edge_length_6dp
            and polygon_width_6dp == edge_width_6dp
        ):
            candidate_records.append(
                {
                    "edge_id": metadata["edge_id"],
                    "matched_edge_length": metadata["source_length"],
                    "matched_edge_width": metadata["source_width"],
                }
            )

    return {
        "candidate_edge_ids": [record["edge_id"] for record in candidate_records],
        "candidate_count": len(candidate_records),
        "chosen_edge_id": candidate_records[0]["edge_id"] if len(candidate_records) == 1 else None,
        "polygon_distance": polygon_distance,
        "polygon_width": polygon_width,
        "polygon_distance_6dp": polygon_distance_6dp,
        "polygon_width_6dp": polygon_width_6dp,
        "matched_edge_length": candidate_records[0]["matched_edge_length"] if len(candidate_records) == 1 else None,
        "matched_edge_width": candidate_records[0]["matched_edge_width"] if len(candidate_records) == 1 else None,
    }


def score_edge_for_polygon(
    ring: list[list[float]],
    centroid: list[float],
    edge_feature: dict[str, Any],
) -> dict[str, Any]:
    coords = edge_feature["geometry"]["coordinates"]
    centroid_to_edge_distance = point_to_linestring_distance_m(centroid, coords)
    ring_to_edge_min_distance = ring_to_linestring_min_distance_m(ring, coords)
    midpoint_to_polygon_distance = point_to_ring_distance_m(linestring_midpoint(coords), ring)
    sampled_edge_points = sample_linestring_points(coords)
    sample_mean_distance = (
        sum(point_to_ring_distance_m(point, ring) for point in sampled_edge_points) / len(sampled_edge_points)
        if sampled_edge_points
        else float("inf")
    )
    matching_score = (
        ring_to_edge_min_distance * 0.45
        + centroid_to_edge_distance * 0.30
        + midpoint_to_polygon_distance * 0.20
        + sample_mean_distance * 0.05
    )
    return {
        "edge_id": edge_feature["properties"]["id"],
        "centroid_to_edge_distance_m": centroid_to_edge_distance,
        "ring_to_edge_min_distance_m": ring_to_edge_min_distance,
        "midpoint_to_polygon_distance_m": midpoint_to_polygon_distance,
        "sample_mean_distance_m": sample_mean_distance,
        "matching_score": matching_score,
    }


def choose_best_edge_for_polygon(
    ring: list[list[float]],
    centroid: list[float],
    edge_features: list[dict[str, Any]],
    allowed_edge_types: set[str],
) -> dict[str, Any] | None:
    candidates = []
    for edge_feature in edge_features:
        if edge_feature["properties"].get("edge_type") not in allowed_edge_types:
            continue
        candidates.append(score_edge_for_polygon(ring, centroid, edge_feature))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item["matching_score"])


def get_preferred_crossing_edge_context(
    point: list[float],
    edge_features: list[dict[str, Any]],
    node_coords_by_id: dict[str, list[float]],
) -> dict[str, Any] | None:
    best_context = None
    for edge_feature in edge_features:
        properties = edge_feature["properties"]
        if properties.get("edge_type") != "crossing":
            continue
        edge_distance = point_to_linestring_distance_m(point, edge_feature["geometry"]["coordinates"])
        if edge_distance > POINT_TO_CROSSING_EDGE_MAX_DISTANCE_M:
            continue
        endpoint_candidates = []
        for node_field in ("u_id", "v_id"):
            node_id = properties.get(node_field)
            node_coords = node_coords_by_id.get(node_id)
            if node_coords is None:
                continue
            endpoint_candidates.append(
                {
                    "node_id": node_id,
                    "node_distance_m": distance_between_points_m(point, node_coords),
                }
            )
        endpoint_candidates.sort(key=lambda item: item["node_distance_m"])
        best_endpoint = endpoint_candidates[0] if endpoint_candidates else None
        context = {
            "associated_edge_id": properties["id"],
            "edge_distance_m": edge_distance,
            "endpoint_node_id": best_endpoint["node_id"] if best_endpoint else None,
            "endpoint_node_distance_m": best_endpoint["node_distance_m"] if best_endpoint else None,
            "ranking_key": (
                best_endpoint["node_distance_m"] if best_endpoint else float("inf"),
                edge_distance,
            ),
        }
        if best_context is None or context["ranking_key"] < best_context["ranking_key"]:
            best_context = context
    return best_context


def get_ramp_upgrade_context(
    point: list[float],
    edge_features: list[dict[str, Any]],
    zone_features: list[dict[str, Any]],
    crossing_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    pedestrian_edge_id, pedestrian_edge_distance = find_nearest_edge_id(
        point,
        edge_features,
        allowed_edge_types={"footpath", "mixedpath", "crossing"},
    )
    zone_id, zone_distance = find_best_zone_anchor(point, zone_features)
    zone_context_ok = zone_id is not None and zone_distance <= POINT_TO_RAMP_CONTEXT_ZONE_MAX_DISTANCE_M
    pedestrian_edge_context_ok = (
        pedestrian_edge_id is not None
        and pedestrian_edge_distance <= POINT_TO_RAMP_CONTEXT_EDGE_MAX_DISTANCE_M
    )

    if crossing_context is not None:
        return {
            "associated_edge_id": crossing_context["associated_edge_id"],
            "crossing_relation_status": "partial",
            "upgrade_reason": "near_crossing_context_without_stable_endpoint_merge",
            "network_context": "crossing_edge_proximity",
            "crossing_edge_distance_m": crossing_context["edge_distance_m"],
            "pedestrian_edge_id": pedestrian_edge_id,
            "pedestrian_edge_distance_m": pedestrian_edge_distance,
            "zone_id": zone_id,
            "zone_distance_m": zone_distance,
        }

    if pedestrian_edge_context_ok and zone_context_ok:
        return {
            "associated_edge_id": pedestrian_edge_id,
            "crossing_relation_status": "unresolved",
            "upgrade_reason": "clear_ramp_transition_point_in_pedestrian_network_context",
            "network_context": "pedestrian_edge_and_zone_proximity",
            "crossing_edge_distance_m": None,
            "pedestrian_edge_id": pedestrian_edge_id,
            "pedestrian_edge_distance_m": pedestrian_edge_distance,
            "zone_id": zone_id,
            "zone_distance_m": zone_distance,
        }

    return None


def convert_network_edges(
    edge_contexts: list[dict[str, Any]],
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    edge_features: list[dict[str, Any]] = []
    edge_id_by_source_id: dict[str, str] = {}
    mapping_counts = report["mapping_counts"]
    dirty_counts = report["dirty_type_normalization_counts"]
    length_comparison = report["edge_length_comparison"]

    for context in edge_contexts:
        props = context["source_feature"].get("properties") or {}
        source_type = context["source_type"]
        edge_type = context["edge_type"]
        if source_type in DIRTY_FOOTPATH_TYPES:
            dirty_counts[f"{source_type}->footpath"] += 1

        source_length = safe_float(props.get("length"))
        derived_length = line_length_m(context["coords"])
        length_m = derived_length
        length_source = "geometry"

        length_comparison["total_edges"] += 1
        length_comparison["source_length_count"] += 1 if source_length is not None else 0
        length_comparison["geometry_length_written_count"] += 1
        length_comparison["derived_total_m"] += derived_length
        if source_length is not None:
            length_comparison["source_total_m"] += source_length
            length_comparison["absolute_difference_total_m"] += abs(source_length - derived_length)
            length_comparison["max_absolute_difference_m"] = max(
                length_comparison.get("max_absolute_difference_m", 0.0),
                abs(source_length - derived_length),
            )

        notes = edge_notes(source_type, context["source_id"])
        edge_properties: dict[str, Any] = {
            "id": context["edge_id"],
            "feature_role": "Edge",
            "edge_type": edge_type,
            "u_id": context["u_id"],
            "v_id": context["v_id"],
            "length_m": length_m,
            "source": "Fairfield_footpath_network_data 1.geojson",
            "status": "existing",
        }
        width_m = safe_float(props.get("width"))
        if width_m is not None:
            edge_properties["width_m"] = width_m
        if edge_type == "crossing":
            crossing_note = (
                "Fairfield source did not provide crossing control or marking detail; "
                "populated traffic_control=other and crossing_marking=other."
            )
            edge_properties["traffic_control"] = "other"
            edge_properties["crossing_marking"] = "other"
            notes = edge_notes(source_type, context["source_id"], crossing_note)
        elif edge_type == "other":
            notes = edge_notes(
                source_type,
                context["source_id"],
                "source type could not be stably mapped to a defined edge subtype.",
            )
        if notes:
            edge_properties["notes"] = notes

        edge_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": context["coords"]},
                "properties": edge_properties,
            }
        )
        mapping_counts["Edge"][edge_type] += 1
        if context["source_id"] is not None:
            edge_id_by_source_id[context["source_id"]] = context["edge_id"]
        report["source_to_target_counts"]["network_lines"][edge_type] += 1
        report["edge_length_sources"][length_source] += 1

    return edge_features, edge_id_by_source_id


def find_nearest_edge_id(
    point: list[float],
    edge_features: list[dict[str, Any]],
    allowed_edge_types: set[str] | None = None,
) -> tuple[str | None, float]:
    best_id = None
    best_distance = float("inf")
    for feature in edge_features:
        props = feature["properties"]
        if allowed_edge_types is not None and props.get("edge_type") not in allowed_edge_types:
            continue
        coords = feature["geometry"]["coordinates"]
        distance = point_to_linestring_distance_m(point, coords)
        if distance < best_distance:
            best_distance = distance
            best_id = props["id"]
    return best_id, best_distance


def convert_polygon_zones(
    polygon_features: list[dict[str, Any]],
    edge_features: list[dict[str, Any]],
    edge_metadata_by_id: dict[str, dict[str, Any]],
    edge_id_by_source_id: dict[str, str],
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    zone_features: list[dict[str, Any]] = []
    mapping_counts = report["mapping_counts"]
    dirty_counts = report["dirty_type_normalization_counts"]
    binding = report["polygon_edge_binding"]
    edge_by_id = {feature["properties"]["id"]: feature for feature in edge_features}

    for feature in polygon_features:
        props = feature.get("properties") or {}
        source_type = normalize_source_type(props.get("type"))
        source_id = normalize_source_id(props.get("id"))
        notes_pairs: list[tuple[str, Any]] = []
        if source_id is not None:
            notes_pairs.append(("source_id", source_id))
        if source_type in DIRTY_FOOTPATH_TYPES:
            dirty_counts[f"{source_type}->footpath_area"] += 1
            notes_pairs.append(("source_raw_type", source_type))
            notes_pairs.append(("dirty_source_type_normalized_to", "footpath_area"))

        metadata_match = find_metadata_exact_match_edge(props, edge_metadata_by_id)
        corresponding_edge_id = None
        binding_method = None
        best_match: dict[str, Any] | None = None
        if metadata_match["candidate_count"] > 0:
            binding["metadata_exact_6dp"] += 1
        if metadata_match["candidate_count"] == 1:
            binding["metadata_exact_6dp_unique"] += 1
            corresponding_edge_id = metadata_match["chosen_edge_id"]
            binding_method = "metadata_exact_match_6dp"
            notes_pairs.extend(
                [
                    ("inference_method", "metadata_exact_match_6dp"),
                    ("metadata_match_fields", "distance,width"),
                    ("polygon_distance", metadata_match["polygon_distance"]),
                    ("polygon_width", metadata_match["polygon_width"]),
                    ("matched_edge_length", metadata_match["matched_edge_length"]),
                    ("matched_edge_width", metadata_match["matched_edge_width"]),
                ]
            )
        elif metadata_match["candidate_count"] > 1:
            binding["metadata_exact_6dp_multiple"] += 1
            notes_pairs.extend(
                [
                    ("metadata_match_ambiguity", "true"),
                    ("metadata_match_candidate_count", metadata_match["candidate_count"]),
                ]
            )

        if corresponding_edge_id is None:
            source_id_edge_id = edge_id_by_source_id.get(source_id) if source_id is not None else None
            if source_id_edge_id is not None:
                corresponding_edge_id = source_id_edge_id
                binding_method = "source_id"
                notes_pairs.append(("inference_method", "source_id_direct"))

        if corresponding_edge_id is None:
            ring = polygon_outer_ring(feature["geometry"])
            centroid = polygon_centroid(ring)
            best_match = choose_best_edge_for_polygon(
                ring,
                centroid,
                edge_features,
                {"footpath", "crossing"},
            )
            corresponding_edge_id = best_match["edge_id"] if best_match is not None else None
            binding_method = "geometry_nearest" if best_match is not None else "unmatched"
            if best_match is not None:
                notes_pairs.extend(
                    [
                        ("inference_method", "geometry_scored_nearest"),
                        ("geometry_nearest_edge_distance_m", f"{best_match['centroid_to_edge_distance_m']:.3f}"),
                        ("ring_to_edge_min_distance_m", f"{best_match['ring_to_edge_min_distance_m']:.3f}"),
                        ("midpoint_to_polygon_distance_m", f"{best_match['midpoint_to_polygon_distance_m']:.3f}"),
                        ("matching_score", f"{best_match['matching_score']:.3f}"),
                    ]
                )
        binding[binding_method] += 1

        if corresponding_edge_id is None:
            report["skipped_features"].append(
                {
                    "source_file": "Fairfield_footpath_polygon_data.geojson",
                    "source_id": source_id,
                    "reason": "no corresponding edge could be determined",
                }
            )
            continue

        corresponding_edge = edge_by_id[corresponding_edge_id]
        represents_edge_type = corresponding_edge["properties"]["edge_type"]
        if represents_edge_type not in {"footpath", "crossing"}:
            represents_edge_type = "footpath"
            notes_pairs.append(("represents_edge_type_adjustment", "coerced_to_footpath"))

        if props.get("width") is not None:
            notes_pairs.append(("source_width", props.get("width")))
        if props.get("distance") is not None:
            notes_pairs.append(("source_distance", props.get("distance")))

        zone_properties: dict[str, Any] = {
            "id": f"z{len(zone_features) + 1}",
            "feature_role": "Zone",
            "zone_type": "footpath_area",
            "footprint_source": "digitized_area",
            "corresponding_edge_id": corresponding_edge_id,
            "represents_edge_type": represents_edge_type,
            "source": "Fairfield_footpath_polygon_data.geojson",
            "status": "existing",
        }
        if notes_pairs:
            zone_properties["notes"] = format_note_pairs(notes_pairs)

        zone_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": feature["geometry"]["coordinates"]},
                "properties": zone_properties,
            }
        )
        review_record = {
            "polygon_source_id": source_id,
            "zone_id": zone_properties["id"],
            "chosen_corresponding_edge_id": corresponding_edge_id,
            "binding_method": binding_method,
            "source_type": source_type,
        }
        if binding_method == "metadata_exact_match_6dp":
            review_record.update(
                {
                    "polygon_distance": metadata_match["polygon_distance"],
                    "polygon_width": metadata_match["polygon_width"],
                    "matched_edge_length": metadata_match["matched_edge_length"],
                    "matched_edge_width": metadata_match["matched_edge_width"],
                }
            )
        elif binding_method == "geometry_nearest":
            review_record.update(
                {
                    "centroid_to_edge_distance_m": round(best_match["centroid_to_edge_distance_m"], 3),
                    "ring_to_edge_min_distance_m": round(best_match["ring_to_edge_min_distance_m"], 3),
                    "midpoint_to_polygon_distance_m": round(best_match["midpoint_to_polygon_distance_m"], 3),
                    "matching_score": round(best_match["matching_score"], 3),
                }
            )
        report["polygon_edge_binding_review"].append(review_record)
        mapping_counts["Zone"]["footpath_area"] += 1
        report["source_to_target_counts"]["polygons"]["footpath_area"] += 1

    binding["matched"] = binding["metadata_exact_6dp_unique"] + binding["source_id"] + binding["geometry_nearest"]
    binding["total_polygons"] = len(polygon_features)
    return zone_features


def find_nearest_node_id(
    point: list[float],
    node_features: list[dict[str, Any]],
) -> tuple[str | None, float]:
    best_id = None
    best_distance = float("inf")
    for feature in node_features:
        coords = feature["geometry"]["coordinates"]
        distance = distance_between_points_m(point, coords)
        if distance < best_distance:
            best_distance = distance
            best_id = feature["properties"]["id"]
    return best_id, best_distance


def find_best_zone_anchor(
    point: list[float],
    zone_features: list[dict[str, Any]],
) -> tuple[str | None, float]:
    best_id = None
    best_distance = float("inf")
    for feature in zone_features:
        ring = polygon_outer_ring(feature["geometry"])
        distance = point_to_ring_distance_m(point, ring)
        if distance < best_distance:
            best_distance = distance
            best_id = feature["properties"]["id"]
            if distance == 0.0:
                break
    return best_id, best_distance


def make_point_derived_node_feature(
    node_id: str,
    point: list[float],
    node_type: str,
    notes_pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": point},
        "properties": {
            "id": node_id,
            "feature_role": "Node",
            "node_type": node_type,
            "connected_edge_count": 1,
            "source": "Fairfield_objects_on_footpath_point_data.geojson",
            "status": "existing",
            "notes": format_note_pairs(notes_pairs),
        },
    }


def convert_point_features(
    point_features: list[dict[str, Any]],
    node_features: list[dict[str, Any]],
    edge_features: list[dict[str, Any]],
    zone_features: list[dict[str, Any]],
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    object_features: list[dict[str, Any]] = []
    point_node_features: list[dict[str, Any]] = []
    mapping_counts = report["mapping_counts"]
    conservative = report["conservative_mappings"]
    node_classification_summary = report["node_classification_summary"]
    node_coords_by_id = {
        feature["properties"]["id"]: feature["geometry"]["coordinates"] for feature in node_features
    }

    for feature in point_features:
        props = feature.get("properties") or {}
        source_type = normalize_source_type(props.get("type"))
        source_id = normalize_source_id(props.get("id"))
        point = [float(feature["geometry"]["coordinates"][0]), float(feature["geometry"]["coordinates"][1])]

        if source_type in OBJECT_TYPE_MAP:
            object_type = OBJECT_TYPE_MAP[source_type]
            object_properties: dict[str, Any] = {
                "id": f"o{len(object_features) + 1}",
                "feature_role": "Object",
                "object_type": object_type,
                "source": "Fairfield_objects_on_footpath_point_data.geojson",
                "status": "existing",
            }
            if props.get("name"):
                object_properties["name"] = str(props["name"])
            footprint = safe_float(props.get("footprint"))
            if footprint is not None:
                object_properties["footprint_m2"] = footprint

            notes_pairs: list[tuple[str, Any]] = []
            if source_id is not None:
                notes_pairs.append(("source_id", source_id))
            notes_pairs.append(("source_type", source_type))

            zone_id, zone_distance = find_best_zone_anchor(point, zone_features)
            if zone_id is not None and zone_distance <= ZONE_ANCHOR_MAX_DISTANCE_M:
                object_properties["anchor_zone_id"] = zone_id
                report["object_anchor_stats"]["zone_anchor_count"] += 1
                if zone_distance == 0.0:
                    report["object_anchor_stats"]["zone_contains_count"] += 1

            edge_id, edge_distance = find_nearest_edge_id(point, edge_features)
            if edge_id is not None and edge_distance <= EDGE_ANCHOR_MAX_DISTANCE_M:
                object_properties["anchor_edge_id"] = edge_id
                report["object_anchor_stats"]["edge_anchor_count"] += 1

            if notes_pairs:
                object_properties["notes"] = format_note_pairs(notes_pairs)

            object_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": point},
                    "properties": object_properties,
                }
            )
            mapping_counts["Object"][object_type] += 1
            report["source_to_target_counts"]["points"][object_type] += 1
            continue

        if source_type in POINT_PLACEHOLDER_TYPES:
            crossing_context = get_preferred_crossing_edge_context(point, edge_features, node_coords_by_id)
            associated_edge_id = crossing_context["associated_edge_id"] if crossing_context else None
            endpoint_node_id = crossing_context["endpoint_node_id"] if crossing_context else None
            endpoint_node_distance = crossing_context["endpoint_node_distance_m"] if crossing_context else None
            near_crossing = crossing_context is not None

            if (
                source_type == "ramp"
                and endpoint_node_id is not None
                and endpoint_node_distance is not None
                and endpoint_node_distance <= POINT_TO_CROSSING_ENDPOINT_MAX_DISTANCE_M
            ):
                conservative["ramp_near_crossing_endpoint_merge"] += 1
                report["merged_point_placeholders"].append(
                    {
                        "source_id": source_id,
                        "source_type": source_type,
                        "merged_node_id": endpoint_node_id,
                        "associated_edge_id": associated_edge_id,
                        "distance_m": round(endpoint_node_distance, 3),
                        "merge_reason": "ramp_near_crossing_endpoint",
                    }
                )
                continue

            if (
                source_type == "crossing"
                and endpoint_node_id is not None
                and endpoint_node_distance is not None
                and endpoint_node_distance <= POINT_TO_CROSSING_ENDPOINT_MAX_DISTANCE_M
            ):
                conservative["crossing_point_near_crossing_edge_merge"] += 1
                conservative["crossing_point_used_as_auxiliary_evidence"] += 1
                report["merged_point_placeholders"].append(
                    {
                        "source_id": source_id,
                        "source_type": source_type,
                        "merged_node_id": endpoint_node_id,
                        "associated_edge_id": associated_edge_id,
                        "distance_m": round(endpoint_node_distance, 3),
                        "merge_reason": "crossing_point_near_crossing_edge",
                    }
                )
                report["crossing_point_auxiliary_records"].append(
                    {
                        "source_id": source_id,
                        "source_type": "crossing",
                        "associated_edge_id": associated_edge_id,
                        "used_as": "crossing_edge_or_node_supporting_evidence",
                        "outcome": "merged_to_existing_node_context",
                        "merge_reason": "crossing_point_near_crossing_edge",
                    }
                )
                continue

            nearest_node_id, nearest_distance = find_nearest_node_id(point, node_features)
            if nearest_node_id is not None and nearest_distance <= POINT_NODE_MERGE_MAX_DISTANCE_M:
                conservative[f"{source_type}_merged_to_existing_node"] += 1
                if source_type == "crossing":
                    conservative["crossing_point_used_as_auxiliary_evidence"] += 1
                report["merged_point_placeholders"].append(
                    {
                        "source_id": source_id,
                        "source_type": source_type,
                        "merged_node_id": nearest_node_id,
                        "associated_edge_id": associated_edge_id,
                        "distance_m": round(nearest_distance, 3),
                        "merge_reason": "nearest_existing_node",
                    }
                )
                if source_type == "crossing":
                    report["crossing_point_auxiliary_records"].append(
                        {
                            "source_id": source_id,
                            "source_type": "crossing",
                            "associated_edge_id": associated_edge_id,
                            "used_as": "crossing_edge_or_node_supporting_evidence",
                            "outcome": "nearest_existing_node_context",
                            "merge_reason": "nearest_existing_node",
                        }
                    )
                continue

            if source_type == "ramp":
                upgrade_context = get_ramp_upgrade_context(
                    point,
                    edge_features,
                    zone_features,
                    crossing_context,
                )
                if upgrade_context is not None:
                    new_node_id = f"n{len(node_features) + len(point_node_features) + 1}"
                    conservative["ramp_upgraded_to_curb_ramp_node"] += 1
                    report["upgraded_curb_ramp_nodes"].append(
                        {
                            "source_id": source_id,
                            "new_node_id": new_node_id,
                            "associated_edge_id": upgrade_context["associated_edge_id"],
                            "crossing_relation_status": upgrade_context["crossing_relation_status"],
                            "upgrade_reason": upgrade_context["upgrade_reason"],
                        }
                    )
                    point_node_features.append(
                        make_point_derived_node_feature(
                            new_node_id,
                            point,
                            "curb_ramp_node",
                            [
                                ("node_origin", "source_point_derived"),
                                ("source_id", source_id),
                                ("source_type", "ramp"),
                                ("inference_method", "curb_ramp_node_upgrade"),
                                ("crossing_relation_status", upgrade_context["crossing_relation_status"]),
                                ("associated_edge_id", upgrade_context["associated_edge_id"]),
                                ("upgrade_reason", upgrade_context["upgrade_reason"]),
                                ("network_context", upgrade_context["network_context"]),
                                (
                                    "crossing_edge_distance_m",
                                    round(upgrade_context["crossing_edge_distance_m"], 3)
                                    if upgrade_context["crossing_edge_distance_m"] is not None
                                    else None,
                                ),
                                ("pedestrian_edge_id", upgrade_context["pedestrian_edge_id"]),
                                (
                                    "pedestrian_edge_distance_m",
                                    round(upgrade_context["pedestrian_edge_distance_m"], 3)
                                    if upgrade_context["pedestrian_edge_distance_m"] is not None
                                    else None,
                                ),
                                ("zone_id", upgrade_context["zone_id"]),
                                (
                                    "zone_distance_m",
                                    round(upgrade_context["zone_distance_m"], 3)
                                    if upgrade_context["zone_distance_m"] is not None
                                    else None,
                                ),
                                ("connected_edge_count", "1"),
                                (
                                    "connected_edge_count_semantics",
                                    "schema_minimum_inferred_transition_not_confirmed_topology",
                                ),
                            ],
                        )
                    )
                    mapping_counts["Node"]["curb_ramp_node"] += 1
                    node_classification_summary["point_derived_curb_ramp_node"] += 1
                    report["source_to_target_counts"]["points"]["node_curb_ramp_node"] += 1
                    continue

            if source_type == "crossing":
                conservative["crossing_point_not_emitted_as_node"] += 1
                conservative["crossing_point_used_as_auxiliary_evidence"] += 1
                report["crossing_point_auxiliary_records"].append(
                    {
                        "source_id": source_id,
                        "source_type": "crossing",
                        "associated_edge_id": associated_edge_id,
                        "used_as": "crossing_edge_or_node_supporting_evidence",
                        "outcome": "not_emitted_as_final_node",
                        "near_crossing": near_crossing,
                        "reason": "crossing_point_retained_as_auxiliary_evidence_only",
                    }
                )
                continue

            notes_pairs: list[tuple[str, Any]] = []
            if source_id is not None:
                notes_pairs.append(("source_id", source_id))
            if source_type == "ramp":
                notes_pairs.extend(
                    [
                        ("node_origin", "source_point_derived"),
                        ("source_type", "ramp"),
                        ("whether_near_crossing", str(near_crossing).lower()),
                        ("associated_edge_id", associated_edge_id),
                        ("reason_for_placeholder", "no_stable_crossing_endpoint_merge_or_ramp_edge_mapping"),
                    ]
                )
                conservative["ramp_point_placeholder_nodes"] += 1
            point_node_features.append(
                make_point_derived_node_feature(
                    f"n{len(node_features) + len(point_node_features) + 1}",
                    point,
                    "other",
                    notes_pairs
                    + [
                        ("inference_method", "point_placeholder_node"),
                        ("connected_edge_count", "1"),
                        ("connected_edge_count_semantics", "schema_minimum_placeholder_not_confirmed_topology"),
                    ],
                )
            )
            mapping_counts["Node"]["other"] += 1
            node_classification_summary["point_derived_other"] += 1
            report["source_to_target_counts"]["points"]["node_placeholder_other"] += 1
            continue

        report["skipped_features"].append(
            {
                "source_file": "Fairfield_objects_on_footpath_point_data.geojson",
                "source_id": source_id,
                "reason": f"unsupported point type: {source_type}",
            }
        )

    return point_node_features, object_features


def assemble_feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "schema_name": "PedestrianNetwork",
        "network_id": "fairfield_pedestrian_network",
        "schema_version": "1.5.0",
        "dataset_version": "1.0.0",
        "name": "Fairfield pedestrian network converted dataset",
        "creator": "Codex Fairfield v1.5 conversion",
        "created_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "features": features,
    }


def validate_feature_collection(schema: dict[str, Any], dataset: dict[str, Any]) -> list[dict[str, Any]]:
    if Draft202012Validator is None:
        return []
    validator = Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(dataset), key=lambda item: list(item.path)):
        path = ".".join(str(part) for part in error.path) or "<root>"
        errors.append({"path": path, "message": error.message})
    return errors


def build_conversion_report(
    network_path: Path,
    polygon_path: Path,
    point_path: Path,
    dataset: dict[str, Any],
    validation_errors: list[dict[str, Any]],
    report: dict[str, Any],
) -> dict[str, Any]:
    feature_role_counts = collections.Counter(
        feature["properties"]["feature_role"] for feature in dataset["features"]
    )
    node_subtype_counts = collections.Counter(
        feature["properties"].get("node_type")
        for feature in dataset["features"]
        if feature["properties"]["feature_role"] == "Node"
    )
    report["input_files"] = {
        "network": network_path.name,
        "polygons": polygon_path.name,
        "points": point_path.name,
    }
    report["output"] = {
        "feature_count": len(dataset["features"]),
        "feature_role_counts": dict(feature_role_counts),
        "node_count": feature_role_counts.get("Node", 0),
        "edge_count": feature_role_counts.get("Edge", 0),
        "zone_count": feature_role_counts.get("Zone", 0),
        "object_count": feature_role_counts.get("Object", 0),
        "node_subtype_counts": dict(node_subtype_counts),
    }
    binding = report["polygon_edge_binding"]
    total_polygons = binding.get("total_polygons", 0)
    matched = binding.get("matched", 0)
    binding["success_rate"] = (matched / total_polygons) if total_polygons else 0.0
    length_comparison = report["edge_length_comparison"]
    if length_comparison.get("source_length_count", 0):
        length_comparison["mean_absolute_difference_m"] = (
            length_comparison["absolute_difference_total_m"] / length_comparison["source_length_count"]
        )
    else:
        length_comparison["mean_absolute_difference_m"] = 0.0
    report["node_classification_summary"]["final_node_subtype_counts"] = dict(node_subtype_counts)
    report["validation"] = {
        "validator_available": Draft202012Validator is not None,
        "error_count": len(validation_errors),
        "errors": validation_errors[:50],
        "passed": (len(validation_errors) == 0) if Draft202012Validator is not None else None,
        "status": "passed"
        if Draft202012Validator is not None and not validation_errors
        else "failed"
        if Draft202012Validator is not None
        else "skipped",
    }
    report["skipped_feature_count"] = len(report["skipped_features"])
    return report


def initial_report(
    network_features: list[dict[str, Any]],
    polygon_features: list[dict[str, Any]],
    point_features: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "input_feature_stats": {
            "network": build_source_stats(network_features),
            "polygons": build_source_stats(polygon_features),
            "points": build_source_stats(point_features),
        },
        "mapping_counts": {
            "Node": collections.Counter(),
            "Edge": collections.Counter(),
            "Zone": collections.Counter(),
            "Object": collections.Counter(),
        },
        "source_to_target_counts": {
            "network_lines": collections.Counter(),
            "polygons": collections.Counter(),
            "points": collections.Counter(),
        },
        "dirty_type_normalization_counts": collections.Counter(),
        "conservative_mappings": collections.Counter(),
        "node_classification_summary": collections.Counter(),
        "polygon_edge_binding": collections.Counter(),
        "object_anchor_stats": collections.Counter(),
        "edge_length_comparison": collections.Counter(),
        "edge_length_sources": collections.Counter(),
        "polygon_edge_binding_review": [],
        "merged_point_placeholders": [],
        "crossing_point_auxiliary_records": [],
        "upgraded_curb_ramp_nodes": [],
        "skipped_features": [],
    }


def convert_fairfield(
    network_path: Path,
    polygon_path: Path,
    point_path: Path,
    schema_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    network_data = load_geojson(network_path)
    polygon_data = load_geojson(polygon_path)
    point_data = load_geojson(point_path)
    schema = load_geojson(schema_path)

    network_features = network_data.get("features") or []
    polygon_features = polygon_data.get("features") or []
    point_features = point_data.get("features") or []
    report = initial_report(network_features, polygon_features, point_features)

    node_features, edge_contexts, _, _, _ = build_nodes_from_network_edges(network_features, report)
    report["mapping_counts"]["Node"].update(
        collections.Counter(feature["properties"]["node_type"] for feature in node_features)
    )
    edge_features, edge_id_by_source_id = convert_network_edges(edge_contexts, report)
    edge_metadata_by_id = build_edge_metadata_by_id(edge_contexts)
    zone_features = convert_polygon_zones(
        polygon_features,
        edge_features,
        edge_metadata_by_id,
        edge_id_by_source_id,
        report,
    )
    point_node_features, object_features = convert_point_features(
        point_features,
        node_features,
        edge_features,
        zone_features,
        report,
    )

    dataset = assemble_feature_collection(
        node_features + point_node_features + edge_features + zone_features + object_features
    )
    validation_errors = validate_feature_collection(schema, dataset)
    final_report = build_conversion_report(
        network_path,
        polygon_path,
        point_path,
        dataset,
        validation_errors,
        report,
    )
    return dataset, final_report, validation_errors, report["polygon_edge_binding_review"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert the Fairfield case-study source GeoJSON files into a pedestrian-network-schema v1.5 dataset.",
        epilog=(
            "Example:\n"
            "  python transforms/convert_fairfield_to_v1_5.py "
            "--network \"Fairfield_footpath_network_data 1.geojson\" "
            "--polygons \"Fairfield_footpath_polygon_data.geojson\" "
            "--points \"Fairfield_objects_on_footpath_point_data.geojson\" "
            "--schema \"pedestrian-network-schema-v1_5.json\" "
            "--output \"output/fairfield_pedestrian_network_v1_5_refined.geojson\" "
            "--report \"output/fairfield_conversion_report_refined.json\" "
            "--binding-review \"output/fairfield_polygon_edge_binding_review_refined.json\""
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--network", required=True)
    parser.add_argument("--polygons", required=True)
    parser.add_argument("--points", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--binding-review", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_base = Path("data/raw/data base")
    schema_base = Path("schema")

    network_path = resolve_input_path(args.network, raw_base)
    polygon_path = resolve_input_path(args.polygons, raw_base)
    point_path = resolve_input_path(args.points, raw_base)
    schema_path = resolve_input_path(args.schema, schema_base)
    output_path = Path(args.output)
    report_path = Path(args.report)
    binding_review_path = Path(args.binding_review)

    dataset, report, validation_errors, geometry_nearest_binding_review = convert_fairfield(
        network_path=network_path,
        polygon_path=polygon_path,
        point_path=point_path,
        schema_path=schema_path,
    )
    save_json(output_path, dataset)
    save_json(report_path, report)
    save_json(binding_review_path, {"bindings": geometry_nearest_binding_review})

    print(f"Saved dataset to {output_path}")
    print(f"Saved report to {report_path}")
    print(f"Saved binding review to {binding_review_path}")
    if Draft202012Validator is None:
        print("Validation skipped because jsonschema is not installed")
    elif validation_errors:
        print(f"Validation produced {len(validation_errors)} error(s)")
    else:
        print("Validation passed")


if __name__ == "__main__":
    main()
