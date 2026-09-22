GA4_DIMENSIONS = [{"name": "sessionSourceMedium"}]
GA4_METRICS = [
    {"name": "sessions"},
    {"name": "engagedSessions"},
    {"name": "engagementRate"},
    {"name": "averageSessionDuration"},
    {"name": "keyEvents"},
    {"name": "sessionKeyEventRate"},
]

GA4_KEY_COLUMN = "session_source_medium"
GA4_METRIC_COLUMNS = tuple(metric["name"] for metric in GA4_METRICS)

GA4_PROPERTY_ID_DILIGENTIC = "GA4_PROPERTY_ID_DILIGENTIC"
GA4_PROPERTY_ID_AJAYKUMAR = "GA4_PROPERTY_ID_AJAYKUMAR"
