# POC: instagrapi over proxy（临时验证，不属于生产流程）

## 目的

验证 **GitHub Actions（数据中心 IP）能否经代理跑通 `instagrapi.user_medias()`**，
以绕开当前的 `PleaseWaitFewMinutes` / HTTP 401 限流。

- 生产代码（`monitor/sources/ig_post.py`、`ig_story.py`、`monitor/main.py`）**不做任何改动**
- POC 不读、不写 `monitor_state.json`，不发通知邮件，不创建提交

## 用到的公开免费代理源

按顺序取**第一个**能返回列表的源，再从中**随机取 1 个**代理；只测这一个
（不建代理池、不重试、不轮换、不预筛）：

| 序号 | 源 | 协议 |
|---|---|---|
| 1 | `raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt` | http |
| 2 | `raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt` | socks5 |
| 3 | `raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt` | http |
| 4 | `raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt` | socks5 |

## 运行方式

GitHub → Actions → **poc-ig-proxy** → *Run workflow*：

| 输入 | 说明 |
|---|---|
| `proxy`（可选） | 只测这一个：`http://host:port` 或 `socks5://host:port`。留空 → 自动随机取 1 个免费代理 |
| `dry_run` | `true` = 只验证「取代理 + 连通性」，不请求 Instagram |

本地亦可运行（用于自检，不请求 IG）：

```bash
pip install instagrapi "requests[socks]"
python poc/ig_proxy_poc.py --dry-run
```

## 输出内容

1. 代理来源（`env:IG_PROXY` / `free:<源域名>`）与协议（**地址脱敏**）
2. 代理是否连通 + **出口 IP**（经代理访问 `api.ipify.org` 得到）
3. `user_medias(10584438821, 5)` 的**只调用一次**结果：成功（媒体数量、最新 code）或 `类型: 异常`
4. 代理不可用 → 立即结束，**不重试、不换第二个代理**

## 安全约定

- 脚本**不打印**代理地址、session、cookie；代理地址一律脱敏为 `<masked>`
- 推荐把代理放进仓库 secret `IG_PROXY`（避免出现在 run 的 inputs 中）；input 方式仅适合临时测试
- POC 仅需 `contents: read` 权限

## 已知局限

- 公开免费代理极不稳定，且大多数已被 Instagram 标记；本 POC 只回答「这条路是否可行」
- 若免费代理不可行，可考虑的备选（按可靠性）：住宅/移动代理（付费）→ self-hosted runner
  （家庭 IP 已实测可用）→ 降低频率 + 冷却（现状）
