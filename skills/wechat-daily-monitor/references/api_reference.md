# 极致了 API 参考 - 公众号监控

> **注意**：本 skill 仅使用 `name` 参数调用 API，不支持 `biz` 或 `url`。以下文档保留完整 API 参数供参考。

## 1. 获取公众号当天发文情况

### 接口地址

```
POST https://www.dajiala.com/fbmain/monitor/v3/post_condition
```

### 请求参数

| 参数 | 类型 | 必填 | 说明 | 费用 |
|------|------|------|------|------|
| name | string | 条件必填 | 公众号名称/原始id/wxid | 0.08元/次 |
| biz | string | 条件必填 | 公众号唯一标识（优先级最高） | 0.06元/次 |
| url | string | 条件必填 | 微信文章链接 | 0.06-0.08元/次 |
| key | string | 是 | API密钥 | - |
| verifycode | string | 否 | 附加码 | - |

**注意**：biz、url、name 三选一，优先使用 biz

### 响应字段

| 字段 | 说明 |
|------|------|
| code | 状态码，0表示成功 |
| msg | 消息 |
| data | 文章列表数组 |
| data[i].position | 发文位置（1=头条，2=二条...） |
| data[i].title | 文章标题 |
| data[i].url | 文章链接 |
| data[i].post_time | 发文时间戳 |
| data[i].post_time_str | 发文时间文本格式 |
| data[i].cover_url | 封面图URL |
| data[i].original | 原创状态：1=原创 0=未声明 2=转载 |
| data[i].item_show_type | 内容类型：0=图文 5=视频 7=音乐 8=图片 10=文字 |
| data[i].digest | 摘要 |
| data[i].types | 类型：9=群发（有通知），1=发布（无通知） |
| total_num | 发文总次数 |
| masssend_count | 群发总次数 |
| publish_count | 发布总次数 |
| mp_nickname | 公众号名称（仅name参数调用时返回） |
| mp_wxid | 公众号wxid（仅name参数调用时返回） |
| mp_ghid | 公众号原始id（仅name参数调用时返回） |
| head_img | 公众号头像（仅name参数调用时返回） |
| cost_money | 消费金额 |
| remain_money | 剩余金额 |

### 状态码

| 状态码 | 说明 |
|--------|------|
| 0 | 成功 |
| -1 | QPS超限（≤5次/秒） |
| 100 | 缺少参数 |
| 101 | 文章被删除或账号被封/迁移 |
| 104 | 短链接已删除或迁移 |
| 105 | 未找到该名称的公众号 |
| 111 | 请求频繁 |
| 10002 | key或附加码不正确 |
| 20001 | 余额不足 |
| 20002 | 微信链接格式错误 |

### 响应示例

```json
{
  "code": 0,
  "msg": "success!",
  "data": [
    {
      "position": 1,
      "title": "文章标题",
      "url": "https://mp.weixin.qq.com/s?...",
      "post_time": 1722416179,
      "post_time_str": "2024-07-31 16:56:19",
      "cover_url": "https://mmbiz.qpic.cn/...",
      "original": 0,
      "item_show_type": 0,
      "digest": "摘要内容",
      "types": 9
    }
  ],
  "total_num": 5,
  "masssend_count": 1,
  "publish_count": 0,
  "cost_money": 0.06,
  "remain_money": 999999.39
}
```

---

## 2. 获取公众号历史发文列表

### 接口地址

```
POST https://www.dajiala.com/fbmain/monitor/v3/post_history
```

### 请求参数

| 参数 | 类型 | 必填 | 说明 | 费用 |
|------|------|------|------|------|
| biz | string | 条件必填 | 公众号唯一标识（优先级最高） | 0.06元/页 |
| url | string | 条件必填 | 微信文章链接（短链接0.08/页、长链接0.06/页） | 0.06-0.08元/页 |
| name | string | 条件必填 | 公众号名称/原始id/wxid | 0.08元/页 |
| page | string | 是 | 页码（默认1，每页返回5次发文） | - |
| key | string | 是 | API密钥 | - |
| verifycode | string | 否 | 附加码 | - |

**注意**：biz、url、name 三选一，优先使用 biz。每页返回5次发文（每次1~8篇），通过不断翻页可获取全部历史文章。首次调用会返回 total_page 字段。

### 响应字段

| 字段 | 说明 |
|------|------|
| code | 状态码，0表示成功 |
| msg | 消息 |
| data | 文章列表数组 |
| data[i].position | 发文位置（1=头条，2=二条...8=八条） |
| data[i].title | 文章标题 |
| data[i].url | 文章链接（短链接） |
| data[i].post_time | 发文时间戳 |
| data[i].post_time_str | 发文时间文本格式 |
| data[i].cover_url | 封面图URL |
| data[i].original | 原创状态：1=原创 0=未声明 2=转载 |
| data[i].item_show_type | 内容类型：0=图文 5=视频 7=音乐 8=图片 10=文字 11=转载文章 |
| data[i].digest | 摘要 |
| data[i].msg_status | 文章状态：2=正常 7=已删除 6=违规发送失败 104=审核中 105=发送中 |
| data[i].msg_fail_reason | 被删除或违规原因 |
| data[i].is_deleted | 0=正常 1=已删除 |
| data[i].types | 类型：9=群发（有通知），1=发布（无通知） |
| data[i].send_to_fans_num | 收到群发消息的粉丝数量 |
| data[i].pic_cdn_url_235_1 | 2.35:1封面 |
| data[i].pic_cdn_url_16_9 | 16:9封面 |
| data[i].pic_cdn_url_1_1 | 1:1封面 |
| total_num | 发文总次数（群发+发布） |
| total_page | 总页数 |
| publish_count | 发布总次数 |
| masssend_count | 群发总次数 |
| now_page | 当前页码 |
| now_page_articles_num | 当前页文章篇数 |
| cost_money | 本次消费金额 |
| remain_money | 剩余金额 |
| mp_nickname | 公众号名称（仅name参数调用时返回） |
| mp_wxid | 公众号wxid（仅name参数调用时返回） |
| mp_ghid | 公众号原始id（仅name参数调用时返回） |
| head_img | 公众号头像（仅name参数调用时返回） |

### 状态码

| 状态码 | 说明 |
|--------|------|
| 0 | 成功 |
| -1 | QPS超限（≤5次/秒），5秒后重试 |
| 101 | 文章被删除或账号被封/迁移 |
| 105 | 未找到该名称的公众号 |
| 110 | 翻页内没有文章了 |
| 111 | 请求频繁，请稍后再试 |
| 112 | 请求失败，请稍后再试 |
| 113 | 鉴权失败，请稍后再试 |
| 115 | 该页及后面的翻页文章已全部被删除 |
| 400 | 短链接转化失败，需先转长链接 |
| 2003 | 系统资源请求出错，重新尝试 |
| 2005 | 系统错误，2秒后重试 |
| 10002 | key或附加码不正确 |
| 20001 | 余额不足 |
| 20002 | 请输入正确的微信链接 |

### 使用脚本获取

```bash
# 从 watchlist.txt 批量获取每个账号最近 1 页（默认）
python3 scripts/fetch_history_posts.py -o history.json

# 获取 watchlist 中每个账号的前 5 页
python3 scripts/fetch_history_posts.py --max-pages 5 -o history.json

# 获取 watchlist 中每个账号的指定页码
python3 scripts/fetch_history_posts.py --page 3 -o history.json

# 获取全部历史（费用较高，谨慎使用）
python3 scripts/fetch_history_posts.py --max-pages 0 -o history.json
```

---

## 3. 获取公众号基础信息

> 注意：原"获取公众号基础信息"接口编号由 2 变更为 3

### 接口地址

```
POST https://www.dajiala.com/fbmain/monitor/v3/avatar_type
```

### 请求参数

| 参数 | 类型 | 必填 | 说明 | 费用 |
|------|------|------|------|------|
| name | string | 是 | 公众号名称/原始id/wxid | 0.5元/次 |
| key | string | 是 | API密钥 | - |
| verifycode | string | 条件必填 | 附加码 | - |

### 响应字段

| 字段 | 说明 |
|------|------|
| data.name | 公众号名称 |
| data.biz | 公众号唯一标识 |
| data.wxid | 公众号wxid |
| data.type | 公众号类型（订阅号/服务号） |
| data.avatar | 头像URL |
| data.desc | 公众号描述 |

### 响应示例

```json
{
  "code": 0,
  "msg": "成功",
  "data": {
    "name": "人民日报",
    "biz": "MjM5MjAxNDM4MA==",
    "wxid": "rmrbwx",
    "type": "订阅号",
    "avatar": "http://mmbiz.qpic.cn/...",
    "desc": "参与、沟通、记录时代。"
  }
}
```

---

## 使用建议

1. **优先使用公众号名称**：`name` 参数最直观，成本适中（0.08元/次）
2. **批量监控注意频率**：QPS限制5次/秒，每次请求间隔建议≥2秒
3. **账号未更新也扣费**：即使当天未发文，调用接口也会扣费
4. **屏蔽账号无法获取**：部分账号屏蔽搜索，返回 mode=2023
