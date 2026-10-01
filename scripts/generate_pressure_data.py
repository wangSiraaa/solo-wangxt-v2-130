import csv
import random


def main(path: str, segments: int = 100_000) -> None:
    random.seed(20261001)
    rows = []
    # One connected large component, one disconnected subnet, and one extra
    # datum conflict around BM-CONFLICT within the main component.
    for i in range(segments - 19):
        a = f"BM-{i:06d}" if i else "BM-START"
        b = f"BM-{i + 1:06d}"
        delta = random.gauss(0.002 * (i % 7 + 1), 0.002)
        dist = random.uniform(0.25, 2.5)
        sigma = 0.002 + 0.001 * dist
        rows.append((f"L-{i:06d}", 1, a, b, delta, -delta + random.gauss(0, 0.0008), dist, 1.0, 2.0))
    rows.append(("L-CONFLICT", 1, "BM-000010", "BM-CONFLICT", 12.000, -12.001, 1.2, 1.0, 2.0))
    for j in range(10):
        i = 100 * (j + 1)
        # Sum of the 100 individual deltas along the chain; add only measurement
        # noise so this is a legitimate redundant loop, not an impossible datum.
        expected_delta = sum(random.gauss(0.002 * ((i - 100 + k) % 7 + 1), 0.002) for k in range(100))
        forward = expected_delta + random.gauss(0, 0.002)
        rows.append((f"LC-{j:02d}", 1, f"BM-{i - 100:06d}", f"BM-{i:06d}", forward, -(forward + random.gauss(0, 0.0008)), 2.0, 1.0, 2.0))
    for i in range(8):
        a = f"IS-{i}"
        b = f"IS-{i + 1}"
        delta = 0.01 * (i + 1)
        rows.append((f"LI-{i}", 1, a, b, delta, -delta + 0.0002, 0.8, 1.0, 2.0))

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_code", "sequence", "from_code", "to_code", "raw_forward", "raw_backward", "distance_km", "sigma_add_mm", "sigma_per_km_mm"])
        w.writerows(rows)


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else "pressure_100k.csv", int(sys.argv[2]) if len(sys.argv) > 2 else 100_000)
