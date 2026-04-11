# 安全配置

## 本机开发（无认证）

```json
{"host": "127.0.0.1", "port": 8888, "api_token": ""}
```

`/api/v1/health` 和 `/api/v1/health/{id}` 始终无需 Token，可直接用作存活探针。

---

## 局域网（Token + IP 白名单）

```json
{
  "host": "192.168.1.100",
  "port": 8888,
  "api_token": "at-least-32-char-random-string",
  "allowed_ips": ["192.168.1.0/24"],
  "rate_limit": 120
}
```

生成随机 Token：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## HTTPS（自签证书）

```bash
# 生成证书（包含 SAN for IP）
python xtquant_manager/utils/gen_cert.py --ip 192.168.1.100 --out certs/
```

```json
{
  "ssl_certfile": "certs/server.crt",
  "ssl_keyfile":  "certs/server.key"
}
```

客户端跳过证书验证（自签证书）：

```python
client = XtQuantClient(config=ClientConfig(
    base_url="https://192.168.1.100:8888",
    verify_ssl=False,
))
```

---

## HMAC 签名（公网/高安全）

```json
{"enable_hmac": true, "hmac_secret": "very-long-random-secret"}
```

Python 客户端生成签名请求头：

```python
from xtquant_manager.security import generate_hmac_headers

headers = generate_hmac_headers(
    method="GET",
    path="/api/v1/health",
    secret="very-long-random-secret",
)
```

---

## 安全级别对比

| 场景 | Token | IP 白名单 | HTTPS | HMAC |
|------|:-----:|:--------:|:-----:|:----:|
| 本机开发 | — | — | — | — |
| 局域网（受控内网） | ✓ | ✓ | — | — |
| 局域网（严格） | ✓ | ✓ | ✓ | — |
| 公网 | ✓ | ✓ | ✓ | ✓ |
