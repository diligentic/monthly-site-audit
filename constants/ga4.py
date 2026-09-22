from dataclasses import dataclass, field
from typing import Any

GA4_PROPERTY_ID_DILIGENTIC = "GA4_PROPERTY_ID_DILIGENTIC"
GA4_PROPERTY_ID_AJAYKUMAR = "GA4_PROPERTY_ID_AJAYKUMAR"


@dataclass(frozen=True)
class GA4Report:
    file_stem: str
    label: str
    key_column: str
    dimensions: list[dict[str, str]]
    metrics: list[dict[str, str]]
    extra_payload: dict[str, Any] = field(default_factory=dict)

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
    file_stem="events",
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
                "inListFilter": {
                    "values": [
                        "booking_link_click",
                        "cal_cta_click",
                        "calendly_cta_click",
                    ]
                },
            }
        }
    },
)

GA4_REPORTS = (GA4_TRAFFIC_ACQUISITION, GA4_LANDING_PAGE, GA4_EVENTS)
