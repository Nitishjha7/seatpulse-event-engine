#!/usr/bin/env bash
#
# Phase 15 — Pessimistic vs Optimistic locking benchmark.
#
# Chaar scenarios chalata hai, har ek se pehle database aur Redis saaf
# karke. Har run ke baad integrity check bhi chalti hai — kyunki "kaunsa
# tez hai" ka jawab tabhi matlab rakhta hai jab dono SAHI hon.
#
# Chalao (project root se):
#     bash loadtest/run_benchmark.sh
#
# ⚠️ Backend BENCHMARK_MODE=true ke saath chal raha hona chahiye, warna
# server strategy params ignore kar dega aur chaaron runs ek jaise honge.
#
# Har run ke beech reset hota hai kyunki:
#   - target seat book ho chuki hoti hai (agli run me sabko 409 milta)
#   - rate limit buckets khaali ho chuke hote (agli run 429 se shuru hoti)

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

# Redis ON — asli production path. Yahan dono strategies ka farak
# chhupa rehta hai, kyunki 99% requests DB tak pahunchti hi nahi.
run_scenario "optimistic-redis-on"   optimistic  1
run_scenario "pessimistic-redis-on"  pessimistic 1

# Redis OFF — poora load database pe. Asli farak yahan dikhta hai.
run_scenario "optimistic-redis-off"  optimistic  0
run_scenario "pessimistic-redis-off" pessimistic 0

echo ""
echo "CSV reports: $OUT/"
