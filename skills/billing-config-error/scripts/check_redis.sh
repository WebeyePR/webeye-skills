#!/bin/bash
# billing-check-redis.sh - 查询合同ID对应的owner_name
# 用法: ./billing-check-redis.sh <contract_id1> [contract_id2] ...

REDIS_CONTAINER="my-redis"

if [ $# -eq 0 ]; then
    echo "用法: $0 <contract_id1> [contract_id2] ..."
    exit 1
fi

for contract_id in "$@"; do
    owner=$(docker exec "$REDIS_CONTAINER" redis-cli HGET contract_owner_map "$contract_id" 2>/dev/null)
    if [ -n "$owner" ]; then
        echo "$contract_id|$owner"
    else
        echo "$contract_id|NOT_FOUND"
    fi
done
