# Source Bias Estimator

`SourceBiasEstimator` estimates public source/creator bias indicators from provenance-backed public content patterns.

It is not a truth detector. It does not declare that a person or source is definitively bad, fake, or lying.

## Profile Model

`CreatorSourceProfile` includes:

- `profile_id`
- `public_handle_or_source_name`
- `platform`
- `observed_topics`
- `stance_indicators`
- `citation_quality`
- `affiliation_signals`
- `engagement_anomaly_signals`
- `confidence`
- `provenance`
- `last_updated`
- `privacy_notes`

## Indicator Output

The estimator returns:

- Topic skew
- Stance distribution
- Citation quality
- Correction history
- Emotional language
- Sponsorship/affiliation signals
- Engagement anomaly signals
- Confidence level

Every estimate requires provenance and includes explanation text.

## Prohibited Uses

- Protected-class profiling.
- Private-person dossiers.
- Doxxing or individual targeting.
- Unsupported claims that a person or source is bad, fake, or lying.
- Treating indicator scores as truth verdicts.
