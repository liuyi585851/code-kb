**English** · [简体中文](integration-credentials-guide.zh-CN.md)

# Connector credentials guide

Code-KB's external connectors (IM auth/notifications, Wiki write-back,
issue tracker/Git ticketing, and inbound diagnostic webhooks) are **optional**. Each is
enabled by setting its `CODEKB_*` environment variables; when these are unset, the
corresponding feature remains in a safe local/outbox mode.

> Store credentials in a server-side secrets file (e.g. `chmod 600`), not in the
> repository. Never commit real tokens. See `.env.example` for the full list.

## IM (web OAuth login + confirmation push)

Create a self-built app in your IM admin console to obtain a Corp ID, Agent
ID and Secret, then configure a trusted callback domain.

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

`im-api.example.com` / `im-oauth.example.com` are the IM provider's public API hosts. A
lighter alternative is an IM group-robot webhook, which supports push only and requires no OAuth login.

## Wiki write-back (publish approved candidates)

Obtain an OpenAPI token for your wiki and switch the publish mode from outbox to
real writes.

```
CODEKB_WIKI_API_BASE_URL=<wiki-openapi-base>
CODEKB_WIKI_API_TOKEN=<token>
CODEKB_PUBLISH_MODE=http            # switch from outbox to real writes
CODEKB_PUBLISH_TARGET_PARENTID=<parent-page-docid>
CODEKB_PUBLISH_TEMPLATE_DOCID=<template-docid, optional>
CODEKB_PUBLISH_INDEX_DOCID=<index-page-docid, optional>
```

## issue tracker ticketing (governance → defect/requirement tickets)

Create an API account in your issue tracker developer console and authorize the target
workspace.

```
CODEKB_ISSUE_TRACKER_API_BASE_URL=https://api.issuetracker.example.com
CODEKB_ISSUE_TRACKER_API_TOKEN=<api-token>
CODEKB_ISSUE_TRACKER_WORKSPACE_ID=<workspace-id>
```

## Git ticketing (GitLab-style)

Create a Personal Access Token in your Git host.

```
CODEKB_GIT_API_BASE_URL=https://git.example.com/api/v3
CODEKB_GIT_API_TOKEN=<personal-access-token>
CODEKB_GIT_PROJECT_ID=<group/repo or numeric project id>
```

## Inbound diagnostic webhooks (code review / CI / MR / issue tracker / crash)

Point each platform's webhook at `https://<your-domain>/diagnose/webhook` and
enable HMAC signature verification.

```
CODEKB_DIAGNOSIS_WEBHOOK_SIGNING_SECRET=<random-string>
CODEKB_DIAGNOSIS_WEBHOOK_ENFORCE=1
```

## Model line

| Use | How | Credentials |
|---|---|---|
| Generative LLM | Any OpenAI-compatible endpoint (`CODEKB_LLM_*`) | API key (optional) |
| Embedding | Local or hosted embedding model | none for local |
| Reranker | Local or hosted cross-encoder | none for local |

The extractive baseline runs with no LLM configured; the generative path degrades
gracefully to extractive when no key is present.
