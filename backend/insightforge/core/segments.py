from typing import Any

import pandas as pd

MAX_FEATURES = 8
MAX_GROUPS = 8
MIN_ROWS = 20
SILHOUETTE_SAMPLE = 5000


class SegmentError(ValueError):
    pass


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}".rstrip("0").rstrip(".")


def segments(frame: pd.DataFrame, features: list[str], k: int | None = None) -> dict[str, Any]:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    features = list(dict.fromkeys(features))
    if not 2 <= len(features) <= MAX_FEATURES:
        raise SegmentError(f"Finding groups needs 2 to {MAX_FEATURES} numeric columns")
    missing = [column for column in features if column not in frame.columns]
    if missing:
        raise SegmentError(
            f"Column {missing[0]!r} is not in the result; available: {', '.join(map(str, frame.columns))}"
        )
    data = frame[features].apply(pd.to_numeric, errors="coerce")
    text = [column for column in features if data[column].isna().all()]
    if text:
        raise SegmentError(f"Column {text[0]!r} has no numeric values; groups use numeric columns only")
    dropped = int(data.isna().any(axis=1).sum())
    data = data.dropna()
    if len(data) < MIN_ROWS:
        raise SegmentError(f"Finding groups needs at least {MIN_ROWS} complete rows; found {len(data)}")
    constant = [column for column in features if data[column].nunique() < 2]
    if constant:
        raise SegmentError(f"{', '.join(constant)} does not vary, so it cannot separate groups")
    scaled = StandardScaler().fit_transform(data.to_numpy(dtype=float))
    sample = min(len(data), SILHOUETTE_SAMPLE)
    candidates = [k] if k else list(range(2, min(MAX_GROUPS, len(data) // 10) + 1)) or [2]
    scores: dict[int, float] = {}
    models = {}
    for count in candidates:
        if count < 2 or count >= len(data):
            continue
        model = KMeans(n_clusters=count, n_init=10, random_state=42).fit(scaled)
        scores[count] = float(silhouette_score(scaled, model.labels_, sample_size=sample, random_state=42))
        models[count] = model
    if not scores:
        raise SegmentError("Could not form groups with this many rows")
    chosen = max(scores, key=scores.__getitem__)
    labels = models[chosen].labels_
    overall_mean, overall_sd = data.mean(), data.std(ddof=0).replace(0, 1)
    order = pd.Series(labels).value_counts().index
    groups = []
    for rank, label in enumerate(order, start=1):
        members = data[labels == label]
        means = members.mean()
        z = (means - overall_mean) / overall_sd
        standout = [
            f"{'high' if z[column] > 0 else 'low'} {column}"
            for column in z.abs().sort_values(ascending=False).index[:2]
            if abs(z[column]) >= 0.5
        ]
        groups.append(
            {
                "group": f"Group {rank}",
                "rows": int(len(members)),
                "share": float(len(members) / len(data)),
                "profile": ", ".join(standout) or "close to average",
                **{f"mean_{column}": float(means[column]) for column in features},
            }
        )
    silhouette = scores[chosen]
    magnitude = (
        "negligible"
        if silhouette < 0.25
        else "small"
        if silhouette < 0.5
        else "medium"
        if silhouette < 0.7
        else "large"
    )
    checks = [
        "Columns were standardised so each counts equally; k-means ran 10 starts with a fixed seed.",
        (
            f"The number of groups was set to {chosen}."
            if k
            else "Silhouette score by number of groups: "
            + "; ".join(f"{count}: {score:.2f}" for count, score in scores.items())
            + f". {chosen} groups separated best."
        ),
    ]
    if dropped:
        checks.append(f"{dropped} rows with missing values were left out.")
    cautions = [
        "Groups describe patterns in this data; they are not fixed types, and new data can shift them."
    ]
    if silhouette < 0.25:
        cautions.append("The groups overlap heavily (silhouette below 0.25); treat them as rough.")
    largest = groups[0]
    return {
        "test": "Customer groups (k-means)",
        "statistic": float(chosen),
        "p_value": None,
        "effect_size": {"name": "silhouette", "value": silhouette, "magnitude": magnitude},
        "groups": groups,
        "checks": checks,
        "cautions": cautions,
        "interpretation": (
            f"The {len(data):,} rows fall into {chosen} groups by {', '.join(features)}. The largest, "
            f"{largest['group']} ({largest['share']:.0%}), has {largest['profile']}. "
            + " ".join(
                f"{group['group']} ({group['share']:.0%}): {group['profile']}." for group in groups[1:4]
            )
        ).strip(),
        "n": len(data),
    }
