---
name: billing-config-error
description: 处理账单系统配置重复报错。当用户收到类似 "配置数据有重复配置，请重新配置正确数据" 的报错时，自动提取 contract_id，查询 Redis 中的 owner_name，并返回合同负责人信息。

**触发条件：**
1. 用户提到账单配置重复、配置数据有重复
2. 报错信息中包含 contract_id 列表
3. 需要查询 contract_owner_map 获取负责人信息

**使用流程：**
1. 从报错中提取所有 contract_id
2. 执行脚本查询 Redis: scripts/check_redis.sh <contract_id1> [contract_id2] ...
3. 整理结果返回给用户
---

# Billing Config Error Handler

处理账单系统配置重复报错，快速定位问题合同负责人。

## 工作流程

### 1. 解析报错信息

从报错中提取重复配置的 contract_id。典型报错格式：

```
Exception: Job SOURCE_TO_ODS_CONTRACT params ('2026-03-01', '2026-03-02') failed: 配置数据有重复配置，请重新配置正确数据:
_PARTITIONTIME  month  contract_id  billing_account_id ...
200526         2026-03  4034         01D368-B3214C-AECD1C ...
204830         2026-03  9601         01D368-B3214C-AECD1C ...
```

提取所有 contract_id 列的值（如：4034, 9601）。

### 2. 查询 Redis

使用提供的脚本查询每个 contract_id 对应的 owner_name：

```bash
./scripts/check_redis.sh 4034 9601
```

输出格式：
```
4034|陶胜旭🔺
9601|朱秋贺
```

### 3. 返回结果

用文字总结返回，格式如下：

> 时间：从 `params` 中提取（如：2026-03-01 至 2026-03-02）
> 问题：XX🔺 和 XX 的配置重复
> 下一步：联系对应负责人核对修正配置

## 脚本说明

- **scripts/check_redis.sh**: 查询本地 Docker Redis 容器中的 contract_owner_map
  - 参数：一个或多个 contract_id
  - 依赖：本地运行的 Redis 容器，名为 `my-redis`
  - 返回值：contract_id|owner_name 格式，如果找不到则返回 NOT_FOUND

## 注意事项

1. 脚本依赖本地 Docker 环境和名为 `my-redis` 的 Redis 容器
2. 如果 Redis 容器名不同，需要修改脚本中的 `REDIS_CONTAINER` 变量
3. contract_owner_map 是 Redis Hash 类型，key 为 contract_id，value 为 owner_name
