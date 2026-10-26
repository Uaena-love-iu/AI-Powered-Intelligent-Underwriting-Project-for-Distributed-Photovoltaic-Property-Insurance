# API接口
基路径 /api/v1/risk/unified。JSON UTF-8。鉴权沿用工作台：启用时传 Authorization: Bearer <本系统令牌>，GET需查看权限，POST需核保权限。密钥只保留在服务端。

| 方法/路径 | 用途 | 请求 | 成功响应 |
|---|---|---|---|
| GET /component?model=完整型号 | 服务端查询型号能力 | query model | component、capacities |
| GET /catalog | 可选城市/站点目录 | 无 | version、return_period_years、records |
| POST /location | 地址匹配 | site对象 | status及城市、基本风雪压、来源或候选 |
| POST /assess | 查询真实组件并计算 | examples/01_assess_request.json | assessment、report_markdown |
| POST /merge-report | 合并原案件生成新报告 | assess请求+case_id | case、parent_case_id、report_markdown |

assess顶层字段仅支持project_id、model、address、screening_input，额外字段返回422。能力值不是assess客户端输入字段。
merge-report必须提供已经存在的case_id；地址及完整组件型号必须与原案件一致。修改地址或型号应先重跑整单。

## 常见错误
- 401/403：未登录或权限不足。
- 404：组件型号未查到（component接口），或原案件不存在。
- 409：合并时地址/型号与原案件不同。
- 422：输入合同校验失败。
- 502：组件provider访问失败。
- 200但assessment.status为PARTIAL/INSUFFICIENT_DATA：业务缺值，前端应展示缺口，不能视作计算通过。
- /location返回非MATCHED是匹配业务结果，不一定是HTTP错误；展示message和candidates。

## 合并与版本
合并时服务端重新查询型号并重新计算，再复跑DecisionEngine和整单报告。新的case_id格式为原ID-FS-随机后缀。原案件及其人工复核不动，新案件不继承“已人工批准”状态。客户端必须保存新case_id、更新历史和报告版本，避免仍展示旧整单报告。

GET接口可用浏览器查看；POST联调示例：
```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8768/api/v1/risk/unified/assess' -ContentType 'application/json; charset=utf-8' -Body (Get-Content examples/01_assess_request.json -Raw -Encoding utf8)
```
此请求中的TEST-5400仅为离线测试型号；真实API调用需替换成可查询的真实完整型号。离线测试由SDK使用显式模拟组件记录完成。
