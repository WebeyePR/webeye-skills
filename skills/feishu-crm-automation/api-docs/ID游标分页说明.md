# ID 游标分页说明

OpenAPI 请求限制了最大 Limit，大多数情况下拉取全部数据时需要分页请求多次，选择合适的分页方式可以提高效率，避免被飞书 aPaaS 的限流器拦截。常见的 Offset 分页方式在 Offset 很大时会产生慢 SQL ，导致查询时间过长，建议使用 ID 游标分页方式进行分页。

## ID 游标分页

虽然 ID 游标分页是串行的，但是单次查询性能非常好，数据库压力更小，上游系统也能更快地拉取到全部数据。在有限流器的情况下，串行的 ID 游标分页比并行的 Offset 分页的整体效率更高。

### 示例

```javascript
async function getAllUsers() {
  let minID = 0; // ID 游标
  let limit = 200;
  let res = [];
  while (true) {
    const data = {
      "offset": 0,
      "limit": limit,
      "count": false, // 传递 count: false 参数以禁止 count(*) 查询 (否则 count(*) SQL 更慢)
      "fields": ["userName"],
      "filter": [
        {
          "operator": "gt", // 传递条件 _id > minID, 即游标分页
          "rightValue": minID,
          "leftValue": "_id",
        },
      ],
      "sort": [
        {
          "field": "_id", // 传递条件 order by _id asc, 即游标分页
          "direction": "asc",
        },
      ],
    };
    let users = (await getUsers(data)).records; // 使用 data 作为参数请求 openapi
    res.push(...users);
    if (users.length < limit) {
      break;
    }
    minID = users[limit - 1]._id; // 每一批查询后更新游标
  }
  return res;
}
```

## limit/offset 分页（不推荐使用）

常用的 Limit/Offset 分页单次查询性能较差（尤其在 Offset 较大时），因此很多同学会开并发以加速整体效率，但是这样会导致单次查询性能更差，并且更容易被飞书 aPaaS 查询限流器拦截。

### 示例

```javascript
async function getAllUsers() {
  let offset = 0;
  let limit = 200;
  let res = [];
  while (true) {
    const data = {
      "offset": offset,
      "limit": limit,
      "fields": ["userName"],
    };
    let users = (await getUsers(data)).records; // 使用 data 作为参数请求 openapi
    res.push(...users);
    if (users.length < limit) {
      break;
    }
    offset += limit; // offset 偏移
  }
  return res;
}
```
