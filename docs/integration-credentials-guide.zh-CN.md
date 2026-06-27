[English](integration-credentials-guide.md) · **简体中文**

# 连接器凭据指南

Code-KB 的外部连接器(IM 鉴权/通知、Wiki 回写、issue tracker/Git 工单,以及入站诊断 webhook)均为**可选**。每个连接器通过设置对应的 `CODEKB_*` 环境变量启用;在未设置时,相应功能将保持在安全的本地/outbox 模式。

> 请将凭据存放在服务器端的密钥文件中(例如 `chmod 600`),而不要置于仓库内。切勿提交真实 token。完整列表参见 `.env.example`。

## IM（网页 OAuth 登录 + 确认推送）

在 IM 管理后台创建一个自建应用,获取 Corp ID、Agent ID 与 Secret,随后配置可信回调域名。

```
CODEKB_IM_CORP_ID=<corp-id>
CODEKB_IM_AGENT_ID=<agent-id>
CODEKB_IM_APP_SECRET=<secret>
CODEKB_IM_API_BASE=https://im-api.example.com
CODEKB_IM_OAUTH_AUTHORIZE_BASE=https://im-oauth.example.com/connect/oauth2/authorize
CODEKB_IM_OAUTH_REDIRECT_URI=https://<your-domain>/auth/im/callback
CODEKB_IM_OAUTH_SCOPE=snsapi_base
CODEKB_IM_OAUTH_STATE_SECRET=<random-string>
CODEKB_IM_OAUTH_TOKEN_TTL_DAYS=7
CODEKB_IM_CONFIRM_URL_BASE=https://<your-domain>
```

`im-api.example.com` / `im-oauth.example.com` 为 IM 服务商的公开 API 主机。一种更轻量的替代方案是 IM 群机器人 webhook,它仅支持推送,且无需 OAuth 登录。

## Wiki 回写（发布已审核的候选）

为 wiki 获取一个 OpenAPI token,并将发布模式从 outbox 切换为真实写入。

```
CODEKB_WIKI_API_BASE_URL=<wiki-openapi-base>
CODEKB_WIKI_API_TOKEN=<token>
CODEKB_PUBLISH_MODE=http            # switch from outbox to real writes
CODEKB_PUBLISH_TARGET_PARENTID=<parent-page-docid>
CODEKB_PUBLISH_TEMPLATE_DOCID=<template-docid, optional>
CODEKB_PUBLISH_INDEX_DOCID=<index-page-docid, optional>
```

## issue tracker 工单（治理 → 缺陷/需求工单）

在 issue tracker 的开发者控制台创建一个 API 账号,并授权目标 workspace。

```
CODEKB_ISSUE_TRACKER_API_BASE_URL=https://api.issuetracker.example.com
CODEKB_ISSUE_TRACKER_API_TOKEN=<api-token>
CODEKB_ISSUE_TRACKER_WORKSPACE_ID=<workspace-id>
```

## Git 工单（GitLab 风格）

在 Git 托管平台创建一个 Personal Access Token。

```
CODEKB_GIT_API_BASE_URL=https://git.example.com/api/v3
CODEKB_GIT_API_TOKEN=<personal-access-token>
CODEKB_GIT_PROJECT_ID=<group/repo or numeric project id>
```

## 入站诊断 webhook（code review / CI / MR / issue tracker / crash）

将各平台的 webhook 指向 `https://<your-domain>/diagnose/webhook`,并启用 HMAC 签名校验。

```
CODEKB_DIAGNOSIS_WEBHOOK_SIGNING_SECRET=<random-string>
CODEKB_DIAGNOSIS_WEBHOOK_ENFORCE=1
```

## 模型线

| 用途 | 方式 | 凭据 |
|---|---|---|
| 生成式 LLM | 任意 OpenAI 兼容端点(`CODEKB_LLM_*`) | API key(可选) |
| Embedding | 本地或托管的 embedding 模型 | 本地无需 |
| Reranker | 本地或托管的 cross-encoder | 本地无需 |

在未配置 LLM 时,抽取式基线仍可正常运行;生成式路径在缺少 key 时会优雅降级为抽取式。
