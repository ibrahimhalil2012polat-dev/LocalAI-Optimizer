"""Conservative screening for three-round benchmarks."""
import math


def finalists(candidates, measurements, candidate_key, speed_key, baseline,
              margin=0.15):
    """Keep baseline and candidates not clearly slower after two rounds.

    Fall back to full testing for inconsistent outputs or noisy measurements.
    This is a heuristic, not a statistical guarantee of the global optimum.
    """
    candidates = list(candidates)
    if not candidates or baseline not in candidates:
        raise ValueError("Invalid candidates or baseline")
    groups = {c: [r for r in measurements if r[candidate_key] == c]
              for c in candidates}
    if any(len(rows) != 2 for rows in groups.values()):
        return candidates
    if len({r.get("output_hash") for r in measurements}) != 1 or any(
            not r.get("output_hash") for r in measurements):
        return candidates
    for key in ("tokens", "prompt_tokens", "generation_tokens"):
        values = [r.get(key) for r in measurements]
        if len(set(values)) != 1:
            return candidates
    speeds = {c: [r[speed_key] for r in rows] for c, rows in groups.items()}
    if any(not math.isfinite(v) or v <= 0 for vs in speeds.values() for v in vs):
        return candidates
    if any(min(vs) < max(vs) * 0.90 for vs in speeds.values()):
        return candidates
    leader_floor = max(min(vs) for vs in speeds.values())
    return [c for c in candidates
            if c == baseline or max(speeds[c]) >= leader_floor * (1 - margin)]
