# 获取应用 Token

获取应用 Token。调用前需应用管理员在「API 凭证管理」中新建 API 凭证，并获取对应的 Client ID 和 Client Secret，详见准备开发

## 请求方式
POST

## 接口路径
/auth/v1/appToken

## 请求

### 请求头
| 名称 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| Content-Type | 必填 string | 类型为 application/json | application/json |

### 请求体
| 名称 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| clientId | 必填 string | 由应用管理员在「API 凭证管理」获取 | "c_085d89cd532acx" |
| clientSecret | 必填 string | 由应用管理员在「API 凭证管理」获取 | "vb7085Xd89CD532acxCAS" |

## 响应
| 名称 | 类型 | 说明 |
| :--- | :--- | :--- |
| code | string | 响应码，0 代表成功，否则为失败，详情见错误码列表 |
| msg | string | 响应描述 |
| data | object | Token 的信息 |
| accessToken | string | Token 的值，以「T:」作为前缀 |
| expireTime | int | Token 的有效截止时间， Token 自获取起 2 小时内有效 |

### 请求示例 cURL：
```bash
curl --location --request POST 'https://ae-openapi.feishu.cn/auth/v1/appToken' \
--header 'Content-Type: application/json'   \
--data-raw '{
  "clientId": "c_085d89cd532acx",
  "clientSecret": "vb7085Xd89CD532acxCAS"
}'
```

### 响应示例：
```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "accessToken": "T:f582c06c6ed14686b0ef.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJBcHBJRCI6ImFhYWYyOWU0N2UwMzQ4MWNhZDNkIiwiVGVuYW50TmFtZSI6IiIsIlRlbmFudEtleSI6IiIsIlRlbmFudElEIjowLCJFbnYiOiJkZXZlbG9wbWVudCIsIkFwcFR5cGUiOjIsIk5hbWVzcGFjZSI6IiIsImV4cCI6MTYzNDcxNTAwMDQ4OX0.JstupwgMPiWmBoH0tgWVlOcMLsnEV4Lswbe6CAzntzA",
    "expireTime": 1634469584000
  }
}
```

### 请求示例 Python：
```python
import requests
import json

url = "https://ae-openapi.feishu.cn/auth/v1/appToken"
payload = "{\"clientId\":\"c_085d89cd532acx\",\"clientSecret\":\"vb7085Xd89CD532acxCAS\"}"
headers = {
  "Content-Type": "application/json"
}

response = requests.request("POST", url, headers=headers, data=payload.encode('utf-8'))

print(response.text)
```

### 响应示例：
```json
{
  "code": "0",
  "msg": "success",
  "data": {
    "accessToken": "T:f582c06c6ed14686b0ef.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJBcHBJRCI6ImFhYWYyOWU0N2UwMzQ4MWNhZDNkIiwiVGVuYW50TmFtZSI6IiIsIlRlbmFudEtleSI6IiIsIlRlbmFudElEIjowLCJFbnYiOiJkZXZlbG9wbWVudCIsIkFwcFR5cGUiOjIsIk5hbWVzcGFjZSI6IiIsImV4cCI6MTYzNDcxNTAwMDQ4OX0.JstupwgMPiWmBoH0tgWVlOcMLsnEV4Lswbe6CAzntzA",
    "expireTime": 1634469584000
  }
}
```
