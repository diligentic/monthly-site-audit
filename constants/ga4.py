from dataclasses import dataclass, field
from typing import Any

GA4_PROPERTY_ID_DILIGENTIC = "GA4_PROPERTY_ID_DILIGENTIC"
GA4_PROPERTY_ID_AJAYKUMAR = "GA4_PROPERTY_ID_AJAYKUMAR"

GA4_TRACKED_EVENTS: tuple[str, ...] = (
    "booking_link_click",
    "cal_cta_click",
    "call_booked",
)

GA4_EVENT_DETAIL_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("country", "country"),
    ("customEvent:link_path", "link_path"),
    ("customEvent:link_medium", "link_medium"),
    ("customEvent:link_source", "link_source"),
)

GA4_EVENT_DETAIL_METRICS: tuple[dict[str, str], ...] = (
    {"name": "eventCount"},
    {"name": "totalUsers"},
)


@dataclass(frozen=True)
class GA4Report:
    file_stem: str
    label: str
    key_column: str
    dimensions: list[dict[str, str]]
    metrics: list[dict[str, str]]
    extra_dimensions: tuple[str, ...] = ()
    extra_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def dimension_columns(self) -> tuple[str, ...]:
        """CSV column names for the requested dimensions, in API order."""
        return (self.key_column, *self.extra_dimensions)

    @property
    def metric_columns(self) -> tuple[str, ...]:
        return tuple(metric["name"] for metric in self.metrics)


GA4_TRAFFIC_ACQUISITION = GA4Report(
    file_stem="traffic_acquisition",
    label="traffic acquisition",
    key_column="session_source_medium",
    dimensions=[{"name": "sessionSourceMedium"}],
    metrics=[
        {"name": "sessions"},
        {"name": "engagedSessions"},
        {"name": "engagementRate"},
        {"name": "averageSessionDuration"},
        {"name": "keyEvents"},
        {"name": "sessionKeyEventRate"},
    ],
)

GA4_LANDING_PAGE = GA4Report(
    file_stem="landing",
    label="landing pages",
    key_column="landing_page",
    dimensions=[{"name": "landingPage"}],
    metrics=[
        {"name": "sessions"},
        {"name": "activeUsers"},
        {"name": "newUsers"},
        {
            "name": "averageEngagementTimePerSession",
            "expression": "userEngagementDuration/sessions",
        },
        {"name": "keyEvents"},
        {"name": "sessionKeyEventRate"},
    ],
    extra_payload={"metricAggregations": ["TOTAL"]},
)

GA4_EVENTS = GA4Report(
    file_stem="events/events",
    label="key events",
    key_column="event_name",
    dimensions=[{"name": "eventName"}],
    metrics=[
        {"name": "eventCount"},
        {"name": "totalUsers"},
        {"name": "eventCountPerUser"},
    ],
    extra_payload={
        "dimensionFilter": {
            "filter": {
                "fieldName": "eventName",
                "inListFilter": {"values": list(GA4_TRACKED_EVENTS)},
            }
        }
    },
)


def ga4_event_detail_report(
    event_name: str,
    *,
    available_dimensions: frozenset[str],
) -> GA4Report | None:
    """Build the detail report for one event, or ``None`` when unsupported.

    Only the dimensions the property actually exposes are requested. When the
    property has none of the custom event dimensions the report would just
    repeat the demographics data, so it is skipped entirely; this is what keeps
    properties such as AjayKumar (whose custom event dimensions are not
    configured yet) from failing with an HTTP 400.
    """
    requested = [
        (api_name, column)
        for api_name, column in GA4_EVENT_DETAIL_DIMENSIONS
        if api_name in available_dimensions
    ]
    if not any(api_name.startswith("customEvent:") for api_name, _ in requested):
        return None

    dimensions = [{"name": api_name} for api_name, _ in requested]
    extra_dimensions = tuple(column for _, column in requested[1:])
    return GA4Report(
        file_stem=f"events/{event_name}",
        label=f"{event_name} event details",
        key_column=requested[0][1],
        dimensions=dimensions,
        metrics=list(GA4_EVENT_DETAIL_METRICS),
        extra_dimensions=extra_dimensions,
        extra_payload={
            "dimensionFilter": {
                "filter": {
                    "fieldName": "eventName",
                    "stringFilter": {
                        "matchType": "EXACT",
                        "value": event_name,
                        "caseSensitive": True,
                    },
                }
            },
            "metricAggregations": ["TOTAL"],
            "orderBys": [{"metric": {"metricName": "eventCount"}, "desc": True}],
            "limit": 10000,
            "offset": 0,
            "keepEmptyRows": False,
        },
    )


_DEMOGRAPHIC_METRICS = [
    {"name": "activeUsers"},
    {"name": "newUsers"},
    {"name": "engagedSessions"},
    {"name": "engagementRate"},
    {"name": "engagedSessionsPerUser", "expression": "engagedSessions/activeUsers"},
    {
        "name": "averageEngagementTimePerUser",
        "expression": "userEngagementDuration/activeUsers",
    },
    {"name": "eventCount"},
    {"name": "keyEvents"},
    {"name": "userKeyEventRate"},
    {"name": "totalRevenue"},
]

GA4_DEMOGRAPHIC_CANADA = GA4Report(
    file_stem="demographic_canada",
    label="Canada demographic",
    key_column="country",
    dimensions=[{"name": "country"}],
    metrics=_DEMOGRAPHIC_METRICS,
    extra_payload={
        "dimensionFilter": {
            "filter": {
                "fieldName": "country",
                "stringFilter": {"matchType": "EXACT", "value": "Canada"},
            }
        }
    },
)

GA4_DEMOGRAPHIC_CALGARY = GA4Report(
    file_stem="demographic_calgary",
    label="Calgary demographic",
    key_column="city",
    dimensions=[{"name": "city"}],
    metrics=_DEMOGRAPHIC_METRICS,
    extra_payload={
        "dimensionFilter": {
            "filter": {
                "fieldName": "city",
                "stringFilter": {"matchType": "EXACT", "value": "Calgary"},
            }
        }
    },
)

GA4_REPORTS = (
    GA4_TRAFFIC_ACQUISITION,
    GA4_LANDING_PAGE,
    GA4_EVENTS,
    GA4_DEMOGRAPHIC_CANADA,
    GA4_DEMOGRAPHIC_CALGARY,
)
