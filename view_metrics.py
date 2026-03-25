#!/usr/bin/env python3
"""
LCO — view_metrics.py
Quick terminal viewer for lco_metrics.db

Usage:
    python3 view_metrics.py                  # last 20 requests + summary
    python3 view_metrics.py --limit 50       # last 50 requests
    python3 view_metrics.py --summary        # summary only
    python3 view_metrics.py --db ./my.db     # custom DB path
"""

import argparse
import sqlite3
from pathlib import Path
from datetime import datetime


def _fmt(val, suffix="", decimals=1):
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:.{decimals}f}{suffix}"
    return f"{val}{suffix}"


def print_summary(conn: sqlite3.Connection) -> None:
    row = conn.execute("""
        SELECT
            COUNT(*)                                          AS total_requests,
            SUM(CASE WHEN streaming=1 THEN 1 ELSE 0 END)     AS streaming,
            SUM(CASE WHEN safe_zone_hit=1 THEN 1 ELSE 0 END) AS safe_zone_hits,
            SUM(input_tokens)                                 AS total_input_tokens,
            SUM(output_tokens)                                AS total_output_tokens,
            SUM(total_tokens)                                 AS total_tokens,
            AVG(latency_ms)                                   AS avg_latency_ms,
            MIN(latency_ms)                                   AS min_latency_ms,
            MAX(latency_ms)                                   AS max_latency_ms
        FROM requests
    """).fetchone()

    print("\n  ╔══════════════════════════════╗")
    print("  ║  LCO Metrics Summary         ║")
    print("  ╚══════════════════════════════╝\n")
    print(f"  {'Total requests':<26} {_fmt(row[0])}")
    print(f"  {'Streaming requests':<26} {_fmt(row[1])}")
    print(f"  {'Safe zone hits':<26} {_fmt(row[2])}")
    print(f"  {'Input tokens':<26} {_fmt(row[3])}")
    print(f"  {'Output tokens':<26} {_fmt(row[4])}")
    print(f"  {'Total tokens':<26} {_fmt(row[5])}")
    print(f"  {'Avg latency':<26} {_fmt(row[6], ' ms')}")
    print(f"  {'Min latency':<26} {_fmt(row[7], ' ms')}")
    print(f"  {'Max latency':<26} {_fmt(row[8], ' ms')}")
    print()


def print_requests(conn: sqlite3.Connection, limit: int) -> None:
    rows = conn.execute(f"""
        SELECT ts, provider, model, path, streaming, safe_zone_hit,
               input_tokens, output_tokens, latency_ms, status_code, compression_mode
        FROM requests
        ORDER BY id DESC
        LIMIT {limit}
    """).fetchall()

    if not rows:
        print("  No requests recorded yet.")
        return

    print(f"  Last {limit} requests (newest first):\n")
    header = f"  {'Time':<10} {'Provider':<12} {'Model':<22} {'In tok':<8} {'Out tok':<8} {'Latency':<10} {'Status':<7} {'Safe':<5} {'Stream'}"
    print(header)
    print("  " + "─" * (len(header) - 2))

    for r in rows:
        ts, provider, model, _, streaming, safe_hit, inp, out, lat, status, _ = r
        dt = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        model_short = (model or "")[:20]
        print(
            f"  {dt:<10} {(provider or ''):<12} {model_short:<22} "
            f"{_fmt(inp):<8} {_fmt(out):<8} {_fmt(lat, 'ms', 0):<10} "
            f"{str(status):<7} {'✓' if safe_hit else '·':<5} {'✓' if streaming else '·'}"
        )
    print()


def main():
    parser = argparse.ArgumentParser(description="View LCO metrics database")
    parser.add_argument("--db", default="./lco_metrics.db", help="Path to DB file")
    parser.add_argument("--limit", type=int, default=20, help="Number of recent requests to show")
    parser.add_argument("--summary", action="store_true", help="Show summary only")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"\n  DB not found: {db_path}")
        print("  Start the proxy and make a request first.\n")
        return

    conn = sqlite3.connect(db_path)
    print_summary(conn)
    if not args.summary:
        print_requests(conn, args.limit)
    conn.close()


if __name__ == "__main__":
    main()