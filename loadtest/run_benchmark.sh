#!/usr/bin/env bash
#
# Phase 15 — Pessimistic vs Optimistic locking benchmark.
#
# Runs four scenarios, clearing the database and Redis before each. An integrity check follows every run — because performance comparisons are only valid if both are correct.
#
# Run from project root:
#     bash loadtest/run_benchmark.sh
#
# ⚠️ Backend must run with BENCHMARK_MODE=true, otherwise it ignores strategy parameters and all runs will be identical.
#
# Reset between runs is necessary because:
#   - Target seats are already booked (subsequent runs would return 409)
#   - Rate limit buckets are exhausted (subsequent runs would start with 429)

set -u

USERS=${USERS:-300}
SPAWN=${SPAWN:-100}
TIME=${TIME:-30s}
OUT=${OUT:-loadtest/results}

mkdir -p "$OUT"

run_scenario() {
  local name=$1 strategy=$2 redis=$3

  echo ""
  echo "=================================================================="
  echo "  $name   (strategy=$strategy, redis_lock=$redis)"
  echo "=================================================================="

  docker compose exec -T backend python reset_state.py > /dev/null 2>&1

  docker compose --profile loadtest run --rm \
    -e BOOKING_STRATEGY="$strategy" \
    -e USE_REDIS_LOCK="$redis" \
    locust -f locustfile.py FlashSaleUser \
    --headless -u "$USERS" -r "$SPAWN" -t "$TIME" \
    --host http://backend:8000 \
    --csv "results/$name" \
    2>&1 | tail -12

  echo ""
  echo "--- integrity ---"
  docker compose exec -T backend python verify_integrity.py 2>&1 | grep -E "✅|❌|OVERSOLD|confirmed"
}

# Redis ON — the production path. The difference between strategies is hidden here, as 99% of requests never reach the DB.
run_scenario "optimistic-redis-on"   optimistic  1
run_scenario "pessimistic-redis-on"  pessimistic 1

# Redis OFF — full load on the database. The true difference is visible here.
run_scenario "optimistic-redis-off"  optimistic  0
run_scenario "pessimistic-redis-off" pessimistic 0

echo ""
echo "CSV reports: $OUT/"
