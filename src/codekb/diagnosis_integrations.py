from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .diagnosis_webhook import SUPPORTED_WEBHOOK_SOURCE_CHOICES


DEFAULT_API_BASE_URL = "http://127.0.0.1:8080"


def diagnose_mcp_tool_definitions(*, api_base_url: str = DEFAULT_API_BASE_URL) -> tuple[dict[str, Any], ...]:
    base_url = api_base_url.rstrip("/")
    return (
        {
            "name": "codekb_diagnose",
            "description": "Run Code-KB diagnosis with citation-backed findings, optional external context, and optional current-user confirmation.",
            "inputSchema": {
                "type": "object",
                "required": ["auth_token"],
                "properties": {
                    "query": {"type": "string"},
                    "context": {"type": "object"},
                    "sub_kbs": {"type": "array", "items": {"type": "string"}},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "include_governance": {"type": "boolean"},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                    "confirmation_policy": {
                        "type": "string",
                        "enum": ["never", "always", "needs_review"],
                        "description": "When not never, write a confirmation request to the current user's outbox. needs_review triggers on refusal, findings, or gap candidates.",
                    },
                    "confirmation_reason": {
                        "type": "string",
                        "enum": ["interaction_complete", "problem_solved", "human_review_required", "gap_candidate_review"],
                    },
                    "confirmation_message": {"type": "string"},
                    "confirmation_payload": {"type": "object"},
                },
                "additionalProperties": True,
            },
            "http": {"method": "POST", "url": f"{base_url}/diagnose"},
        },
        {
            "name": "codekb_diagnose_webhook_validate",
            "description": "Validate an external webhook payload mapping before running diagnosis.",
            "inputSchema": {
                "type": "object",
                "required": ["auth_token", "source", "payload"],
                "properties": {
                    "source": {"type": "string", "enum": list(SUPPORTED_WEBHOOK_SOURCE_CHOICES)},
                    "payload": {"type": "object"},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                },
                "additionalProperties": False,
            },
            "http": {"method": "POST", "url": f"{base_url}/diagnose/webhook/{{source}}/validate"},
        },
        {
            "name": "codekb_diagnose_webhook_normalize",
            "description": "Preview a webhook payload as a diagnostic request without running diagnosis.",
            "inputSchema": {
                "type": "object",
                "required": ["auth_token", "source", "payload"],
                "properties": {
                    "source": {"type": "string", "enum": list(SUPPORTED_WEBHOOK_SOURCE_CHOICES)},
                    "payload": {"type": "object"},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                },
                "additionalProperties": False,
            },
            "http": {"method": "POST", "url": f"{base_url}/diagnose/webhook/{{source}}/normalize"},
        },
        {
            "name": "codekb_request_user_confirmation",
            "description": "Request confirmation from the current authenticated user after diagnosis or before human review.",
            "inputSchema": {
                "type": "object",
                "required": ["auth_token", "reason", "message"],
                "properties": {
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                    "reason": {
                        "type": "string",
                        "enum": ["interaction_complete", "problem_solved", "human_review_required", "gap_candidate_review"],
                    },
                    "message": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "additionalProperties": False,
            },
            "outbox": "user_confirmation",
        },
    )


def code_nav_mcp_tool_definitions(*, api_base_url: str = DEFAULT_API_BASE_URL) -> tuple[dict[str, Any], ...]:
    """只读的代码导航工具(独立于 diagnose 那套工具)。

    让客户端的强模型驱动多跳:search -> get_symbol/outline -> read_file_range。
    每条结果都带 repo/file:line 和一行能自解释的出处头。
    """
    base_url = api_base_url.rstrip("/")
    return (
        {
            "name": "codekb_search_code",
            "description": (
                "Hybrid lexical+vector search over the code/doc KB; returns citable snippets "
                "(each carries repo/file:line + a self-describing header). Read-only. "
                "RECIPE: for a natural-language or non-English ask, first EXPAND the concept to likely "
                "English identifiers and search those — a single literal/Chinese query often misses code "
                "(e.g. '第三方登录/third-party login' -> login, signin, account, oauth, sdk, authsdk, union, connect). "
                "To locate where something lives, search then use find_files/list_dir to pin the module. "
                "If results look generic, retry with more specific code terms or codekb_get_symbol. "
                "NOTE: hits are repo-relative file:line — OPEN THEM IN YOUR LOCAL MONOREPO to read the real, "
                "current code (the KB is an index snapshot and may be stale; local checkout is authoritative)."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["query", "auth_token"],
                "properties": {
                    "query": {"type": "string"},
                    "sub_kbs": {"type": "array", "items": {"type": "string"}, "description": "default ['code','docs']"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                },
                "additionalProperties": False,
            },
            "http": {"method": "POST", "url": f"{base_url}/code/search"},
        },
        {
            "name": "codekb_get_symbol",
            "description": (
                "Find code atoms that DEFINE/contain an exact symbol (function/class/method/constant, e.g. "
                "AuthSDKAccountService, LOGIN_NO_CACHED_DATA). Read-only. Use when you know or can guess a symbol "
                "name; complements codekb_search_code (search_code for concepts, get_symbol for names). "
                "Confirm with codekb_read_file_range / codekb_file_outline."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["name", "auth_token"],
                "properties": {
                    "name": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                },
                "additionalProperties": False,
            },
            "http": {"method": "POST", "url": f"{base_url}/code/symbol"},
        },
        {
            "name": "codekb_read_file_range",
            "description": (
                "FALLBACK content read for a file's line range, for when you do NOT have the repo locally. "
                "Prefer opening the local file at this path (this returns the KB's indexed SNAPSHOT, which may "
                "lag the current code; the local checkout is authoritative). Works without the repo on the server."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["path", "start_line", "end_line", "auth_token"],
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                },
                "additionalProperties": False,
            },
            "http": {"method": "POST", "url": f"{base_url}/code/read"},
        },
        {
            "name": "codekb_file_outline",
            "description": "List a file's symbols with their line ranges. Read-only.",
            "inputSchema": {
                "type": "object",
                "required": ["path", "auth_token"],
                "properties": {
                    "path": {"type": "string"},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                },
                "additionalProperties": False,
            },
            "http": {"method": "POST", "url": f"{base_url}/code/outline"},
        },
        {
            "name": "codekb_find_files",
            "description": (
                "Find indexed files whose PATH contains a substring (case-insensitive). Read-only. "
                "Structural discovery — locate a module by name when you don't know the symbol yet, e.g. "
                "find_files('login') -> .../auth/Login/LoginManager.cpp. Use this when concept search "
                "returns generic docs; then outline/read the central file. Complements search_code."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["pattern", "auth_token"],
                "properties": {
                    "pattern": {"type": "string", "description": "substring matched against file paths, e.g. 'login', 'AuthSDK'"},
                    "sub_kbs": {"type": "array", "items": {"type": "string"}, "description": "default ['code','docs']"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                },
                "additionalProperties": False,
            },
            "http": {"method": "POST", "url": f"{base_url}/code/files"},
        },
        {
            "name": "codekb_list_dir",
            "description": (
                "List the immediate sub-directories and files under a repo/path prefix (reconstructed from "
                "indexed paths). Read-only. Browse the tree top-down to understand module layout, e.g. "
                "list_dir('src/Plugins/.../SDKWrapper') -> its modules. Pair with find_files/outline/read."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["auth_token"],
                "properties": {
                    "prefix": {"type": "string", "description": "repo/path prefix; empty lists top level"},
                    "sub_kbs": {"type": "array", "items": {"type": "string"}, "description": "default ['code','docs']"},
                    "auth_token": {"type": "string", "description": "Bound IM/web user token for MCP auth."},
                },
                "additionalProperties": False,
            },
            "http": {"method": "POST", "url": f"{base_url}/code/dir"},
        },
    )


def mr_candidate_card_template(*, api_base_url: str = DEFAULT_API_BASE_URL) -> dict[str, Any]:
    base_url = api_base_url.rstrip("/")
    return {
        "schema_version": "1",
        "card_type": "codekb_diagnose_candidate",
        "title": "Code-KB 诊断候选",
        "trigger": "MR 或 CI 失败事件",
        "actions": [
            {
                "id": "validate_payload",
                "label": "校验诊断上下文",
                "method": "POST",
                "url": f"{base_url}/diagnose/webhook/{{source}}/validate",
            },
            {
                "id": "sample_suite",
                "label": "检查样例套件",
                "method": "GET",
                "url": f"{base_url}/diagnose/webhook/sample-suite",
            },
            {
                "id": "run_diagnosis",
                "label": "运行 KB 诊断",
                "method": "POST",
                "url": f"{base_url}/diagnose/webhook/{{source}}",
            },
            {
                "id": "submit_gap",
                "label": "提交知识缺口候选",
                "method": "POST",
                "url": f"{base_url}/diagnose/webhook/{{source}}/gap-candidate",
            },
        ],
        "display_fields": [
            "diagnosis.confidence",
            "diagnosis.findings",
            "diagnosis.citations",
            "diagnosis.gap_candidate",
            "event.status",
        ],
        "write_policy": "read_only_by_default; gap_candidate requires explicit user action",
    }


def render_code_review_skill(*, api_base_url: str = DEFAULT_API_BASE_URL) -> str:
    base_url = api_base_url.rstrip("/")
    return "\n".join(
        [
            "# Code-KB Diagnose Skill",
            "",
            "Use this skill when a code review, CI, MR, crash, or build failure needs KB-backed diagnosis.",
            "",
            "## Endpoints",
            "",
            f"- Validate payload: `POST {base_url}/diagnose/webhook/{{source}}/validate`",
            f"- Preview mapping: `POST {base_url}/diagnose/webhook/{{source}}/normalize`",
            f"- Check sample suite: `GET {base_url}/diagnose/webhook/sample-suite`",
            f"- Run diagnosis: `POST {base_url}/diagnose/webhook/{{source}}`",
            f"- Submit KB gap: `POST {base_url}/diagnose/webhook/{{source}}/gap-candidate`",
            f"- Inspect events: `GET {base_url}/diagnose/webhook/events`",
            "",
            "## Required Behavior",
            "",
            "- Call validate before diagnosis when integrating a new payload shape.",
            "- Use `diagnose-webhook-sample-import` to sanitize real platform payloads before replacing synthetic samples.",
            "- Run the sample suite after replacing synthetic payloads with sanitized real platform samples.",
            "- Do not include raw credentials, cookies, tokens, or private logs in payloads.",
            "- Treat diagnosis answers as citation-backed guidance, not an automatic code change.",
            f"- Require the current user to open `{base_url}/auth/im/mcp/setup` before MCP use.",
            f"- If OAuth is temporarily unavailable, use the self-service binding page `{base_url}/auth/im/self-bindings/page` with a short binding code and the user's reachable IM route.",
            f"- Keep the admin-controlled fallback page `{base_url}/auth/im/token-bindings/page` for operator recovery only.",
            "- Pass the current user's bound `auth_token` in every production MCP tool call.",
            "- Do not use a shared static MCP token for production; when token-store auth is configured, current-user tokens are authoritative.",
            "- Shared static MCP token auth is disabled by default; `--allow-static-mcp-token` is local smoke-only and must not be enabled for production MCP.",
            "- Use `confirmation_policy=needs_review` or `always` on `codekb_diagnose` when the diagnosis itself should create the current-user confirmation.",
            "- For `POST /diagnose/webhook/{source}`, keep `X-CodeKB-Token` for platform webhook authentication and include the current user's `auth_token` only when `confirmation_policy` should queue a confirmation.",
            "- Webhook confirmation requests are routed only to the current user bound to `auth_token`; do not infer an interface person from repository, owner, or payload fields.",
            "- Use `codekb_request_user_confirmation` for explicit problem solved or interaction complete moments after the AI conversation.",
            "- Submit gap candidates only after the current user confirms the missing KB should enter review.",
            "- Do not route P5 confirmations by owner/interface-person lookup; owner data remains governance context only.",
            "",
            "## Supported Sources",
            "",
            "`" + "`, `".join(SUPPORTED_WEBHOOK_SOURCE_CHOICES) + "`.",
            "",
        ]
    )


def render_im_entry_guide(*, api_base_url: str = DEFAULT_API_BASE_URL) -> str:
    base_url = api_base_url.rstrip("/")
    return "\n".join(
        [
            "# Code-KB Diagnose IM Entry",
            "",
            "企微入口应复用同一 `/diagnose` Hub，不绕过 KB 引用、脱敏、trace 和 gap candidate 逻辑。",
            "",
            "## Recommended Flow",
            "",
            f"1. 将消息或卡片上下文映射为 `POST {base_url}/diagnose` 的 `query/context/sub_kbs`。",
            f"2. 对来自平台事件的结构化 payload，先调用 `POST {base_url}/diagnose/webhook/generic/validate`。",
            f"3. 展示 `answer/citations/findings/suggested_actions`，并提供“提交知识缺口”按钮。",
            "4. 缺口提交只调用 gap candidate 入口，进入 P3 人审，不直接写 Wiki。",
            f"5. 用户使用 MCP 前先打开 `GET {base_url}/auth/im/mcp/setup` 完成自助绑定或IM授权，并检查 token。",
            f"6. 用户可在 setup 页点击自助 smoke，或调用 `POST {base_url}/auth/im/current-user/smoke` 验证当前 token、确认 outbox 和 dry-run 路由。",
            f"7. OAuth 暂不可用时，用户可使用 `{base_url}/auth/im/self-bindings/page` 通过 binding code 绑定当前 MCP token 和企微可达路由。",
            "8. MCP 工具调用必须传当前用户 `auth_token`；诊断时可设置 `confirmation_policy=needs_review` 或 `always` 直接写入 user confirmation outbox。",
            f"9. HTTP `POST {base_url}/diagnose` 同样支持当前用户 `auth_token` + `confirmation_policy`；后置确认可调用 `POST {base_url}/auth/im/confirmations/request`。",
            f"10. webhook 诊断 `POST {base_url}/diagnose/webhook/{{source}}` 如需人审确认，仍必须带平台 `X-CodeKB-Token`，并在 payload 中传当前用户 `auth_token` + `confirmation_policy=needs_review|always`。",
            "11. 生产 MCP 配置 token store 后不接受共享静态 token；确认请求只能路由到当前授权用户。",
            "12. `codekb-confirmation-worker` 通过 token binding 中的当前用户 route dry-run；真实发送可接入企微应用消息、主动消息或机器人通道，并用 delivery log 防重复投递。",
            "13. 不通过复杂接口人识别来决定 P5 确认人；owner 信息只作为治理和候选分派上下文。",
            "",
        ]
    )


def render_current_user_auth_guide(*, api_base_url: str = DEFAULT_API_BASE_URL) -> str:
    base_url = api_base_url.rstrip("/")
    return "\n".join(
        [
            "# Code-KB Current User Auth",
            "",
            "P5 MCP calls use the current user's bound token. Do not use a shared static MCP token for production confirmation routing.",
            "When token-store auth is configured, shared static MCP tokens are ignored. Confirmation requests require a valid current-user token binding.",
            "",
            "## Browser Setup",
            "",
            f"1. Open `{base_url}/auth/im/mcp/setup`.",
            "2. Complete self-service binding with the binding code, or use IM OAuth when it is configured.",
            "3. Verify the browser token status on the setup page.",
            "4. Run the setup-page self-test to create a current-user confirmation and validate dry-run delivery routing.",
            "5. Copy the MCP `auth_token` argument into the MCP client call context.",
            "6. Confirmation prompts after AI interaction, problem solved, or human review are sent to this authenticated user.",
            "7. For diagnosis-triggered review, pass `confirmation_policy=needs_review` or `always` to `codekb_diagnose`; for later conversation milestones, call `codekb_request_user_confirmation`.",
            "8. If an MCP call is missing or rejects `auth_token`, the JSON-RPC error includes `error.data.setup_url`, `error.data.self_binding_page_url`, `error.data.im_oauth_login_url`, `error.data.token_binding_page_url`, `error.data.auth_token_argument`, and a remediation string; clients should show the setup or self-binding URL instead of asking for a shared token.",
            "",
            "## API Checks",
            "",
            f"- Current user token status: `POST {base_url}/auth/im/current-user/status` with `{{\"auth_token\":\"<current-user-token>\"}}`.",
            f"- Current user route self-test: `POST {base_url}/auth/im/current-user/smoke` with `{{\"auth_token\":\"<current-user-token>\"}}`.",
            f"- HTTP diagnosis with current-user confirmation: `POST {base_url}/diagnose` with `auth_token` and `confirmation_policy=needs_review|always`.",
            f"- Webhook diagnosis with current-user confirmation: `POST {base_url}/diagnose/webhook/{{source}}` with `X-CodeKB-Token`, plus payload `auth_token` and `confirmation_policy=needs_review|always`.",
            f"- Explicit current-user confirmation: `POST {base_url}/auth/im/confirmations/request` with `auth_token`, `reason`, `message`, and optional `payload`.",
            f"- Web push inbox while IM delivery is pending: `GET {base_url}/auth/im/confirmations/page`.",
            f"- Confirmation detail page: `GET {base_url}/auth/im/confirmations/page?confirmation_id=<confirmation_id>`.",
            f"- Pending confirmations: `POST {base_url}/auth/im/confirmations/pending`.",
            "",
            "## Confirmation Worker",
            "",
            "- Before IM/TOF delivery is approved, use the web push inbox as the visible delivery surface.",
            "- `deploy/codekb-confirmation-worker once` validates pending confirmation routing.",
            "- `deploy/codekb-confirmation-worker start` runs the worker loop after IM/TOF send is configured.",
            "- Real app-message sends require `CODEKB_ENABLE_IM_SEND=1` and IM app credentials; robot/message push routes can reuse the same token binding metadata when that external sender is available.",
            "- Delivery receipts are written to `CODEKB_USER_CONFIRMATION_DELIVERY_LOG` so repeated loops skip already delivered confirmations.",
            "",
            "## Required Environment",
            "",
            "- `CODEKB_USER_TOKEN_STORE`",
            "- `CODEKB_USER_BINDING_CODE`",
            "- `CODEKB_USER_CONFIRMATION_OUTBOX`",
            "- `CODEKB_USER_CONFIRMATION_REPORT`",
            "- `CODEKB_USER_CONFIRMATION_DELIVERY_LOG`",
            "- `CODEKB_IM_CORP_ID`",
            "- `CODEKB_IM_AGENT_ID`",
            "- `CODEKB_IM_APP_SECRET`",
            "- `CODEKB_IM_OAUTH_STATE_SECRET`",
            "- `CODEKB_AUTH_ADMIN_TOKEN` for admin-only token management and real sample import",
            "",
            "## Fallback",
            "",
            "- If the MCP host cannot perform OAuth itself, have the user complete self-service binding or IM OAuth in the browser setup page and pass the resulting token into MCP.",
            f"- If OAuth is temporarily unavailable, use `{base_url}/auth/im/self-bindings/page` as the user-facing fallback.",
            f"- Use `{base_url}/auth/im/token-bindings/page` as an admin-only fallback for operator recovery after verifying the user's reachable IM route.",
            "- Manual HTTP admin token binding is operator-only; normal users should use self-service binding or OAuth.",
            "- Shared static MCP token fallback is disabled by default; `--allow-static-mcp-token` is only for local smoke and must not be used in production.",
            "",
        ]
    )


def render_external_handoff_checklist(*, api_base_url: str = DEFAULT_API_BASE_URL) -> str:
    base_url = api_base_url.rstrip("/")
    sources = "`, `".join(SUPPORTED_WEBHOOK_SOURCE_CHOICES)
    return "\n".join(
        [
            "# Code-KB P5 External Handoff",
            "",
            "This checklist is for the remaining production inputs that cannot be completed by repository code alone.",
            f"Current task plan: `GET {base_url}/diagnose/external-inputs` or `python3 -m codekb diagnose-external-inputs --env-file /data/codekb/state/p5-secrets.env --json`.",
            f"Browser checklist: `{base_url}/diagnose/external-inputs/page`.",
            "",
            "## Current User MCP Auth",
            "",
            f"- User setup page: `{base_url}/auth/im/mcp/setup`.",
            f"- Self-service token binding: `{base_url}/auth/im/self-bindings/page`.",
            f"- Admin-controlled token binding fallback: `{base_url}/auth/im/token-bindings/page`.",
            f"- Token status check: `POST {base_url}/auth/im/current-user/status` with `{{\"auth_token\":\"<current-user-token>\"}}`.",
            f"- Browser/HTTP self-test: `POST {base_url}/auth/im/current-user/smoke` creates one current-user confirmation and validates dry-run delivery routing.",
            "- Production MCP must start with `--token-store /data/codekb/state/user-tokens.json`.",
            "- `CODEKB_MCP_TOKEN` is local diagnose smoke-only and is ignored when token-store auth is configured.",
            "- `--allow-static-mcp-token` is disabled by default and may only be used for local smoke without token store; production MCP must reject shared static tokens.",
            "- Self-service binding requires `CODEKB_USER_BINDING_CODE`; it maps the current MCP token to the user's reachable IM route without requiring a IM app.",
            "- Manual HTTP admin token binding requires `CODEKB_AUTH_ADMIN_TOKEN` and is operator recovery only.",
            "- End-to-end smoke after binding: `CODEKB_USER_AUTH_TOKEN=<current-user-token> python3 -m codekb diagnose-current-user-smoke --respond --json`.",
            "- Acceptance: at least one real current user completes self-service binding or IM OAuth and `/diagnose/readiness` no longer warns on `mcp_auth`.",
            "",
            "## Current User Binding",
            "",
            "- Required minimum environment: `CODEKB_USER_BINDING_CODE` and `CODEKB_USER_TOKEN_STORE`.",
            f"- User-facing page: `{base_url}/auth/im/self-bindings/page`.",
            "- Route value can be a IM message target, robot route, IM userid, or manual contact route. Public responses return only route hashes.",
            "- Acceptance: current user self-binds, copies `auth_token`, calls MCP, and confirmation outbox targets that token hash.",
            "",
            "## IM OAuth",
            "",
            "- Optional enhanced path environment: `CODEKB_IM_CORP_ID`, `CODEKB_IM_AGENT_ID`, `CODEKB_IM_APP_SECRET`, `CODEKB_IM_OAUTH_STATE_SECRET`.",
            f"- Redirect URL to allow in the IM app: `{base_url}/auth/im/oauth/callback`.",
            "- Generate state secret with `diagnose-security-bootstrap`; do not send secret values through chat or commit them.",
            f"- Template: `python3 -m codekb diagnose-im-configure --env-file /data/codekb/state/p5-secrets.env --template-output /data/codekb/state/im-config.todo.env --api-base-url {base_url} --json` writes a 0600 fill-in file without copying existing secrets.",
            "- Safe env update: `python3 -m codekb diagnose-im-configure --env-file /data/codekb/state/p5-secrets.env --from-template /data/codekb/state/im-config.todo.env --apply --json` reads the filled server-side template and writes only the server-only env file.",
            "- Setup smoke: `python3 -m codekb diagnose-im-oauth-smoke --env-file /data/codekb/state/p5-secrets.env --json` verifies state signing, authorize URL shape, and token-store status.",
            "- Acceptance: `GET /auth/im/oauth/login?next=/auth/im/mcp/setup` redirects to IM and callback issues a token once.",
            "",
            "## IM Confirmation Delivery",
            "",
            "- Required environment: `CODEKB_ENABLE_IM_SEND=1`, `CODEKB_IM_CORP_ID`, `CODEKB_IM_AGENT_ID`, `CODEKB_IM_APP_SECRET`.",
            f"- Recommended confirmation URL base: `{base_url}/auth/im/confirmations/page`.",
            f"- Temporary web inbox fallback: `{base_url}/auth/im/confirmations/page` shows all current-user messages by polling the outbox.",
            "- IM app-message send requires permission to call `message/send` to the current user's `im_userid` or compatible OpenId route.",
            "- If using robot/message push instead, bind the user token to that route and attach the corresponding sender outside the OAuth path.",
            "- Enable app-message send safely with `diagnose-im-configure --enable-send --confirm-real-send --apply --json` after OAuth and routing are verified.",
            "- Dry-run smoke: `CODEKB_USER_AUTH_TOKEN=<current-user-token> python3 -m codekb diagnose-im-smoke --env-file /data/codekb/state/p5-secrets.env --json`.",
            "- Real send smoke: add `--execute` after setting `CODEKB_ENABLE_IM_SEND=1`; the output only includes token and route hashes.",
            "- Acceptance: `deploy/codekb-confirmation-worker once` validates routing, then `start` sends one real confirmation and records delivery log.",
            "",
            "## External Webhook Samples",
            "",
            f"- Supported sources: `{sources}`.",
            "- Provide one real JSON payload per enabled platform event after removing bulky private logs; raw payloads must not be committed.",
            f"- Import over HTTP: `POST {base_url}/diagnose/webhook/{{source}}/sample-import` with `X-CodeKB-Admin-Token`.",
            "- `CODEKB_AUTH_ADMIN_TOKEN` must be configured before sample import or token management endpoints are usable.",
            "- Import output defaults to `/data/codekb/state/diagnose-webhook-samples.real.yaml`.",
            "- Safe activation: `python3 -m codekb diagnose-webhook-sample-activate --env-file /data/codekb/state/p5-secrets.env --apply --confirm-real-samples --json`.",
            "- This validates the real suite and writes `CODEKB_DIAGNOSE_WEBHOOK_SAMPLES` plus `CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES` to the server-only env file.",
            "- Restart API after activation so `/diagnose/webhook/sample-suite` uses the real suite.",
            "- Acceptance: `/diagnose/webhook/sample-suite` passes and `/diagnose/readiness` no longer warns on `external_platform_samples`.",
            "",
            "## Webhook Security",
            "",
            "- Generate `CODEKB_DIAGNOSE_WEBHOOK_TOKEN` with `diagnose-security-bootstrap`.",
            "- Configure platform callers to send `X-CodeKB-Token`; do not log or share the token value.",
            "- Acceptance: webhook endpoints reject missing token and accept the configured token.",
            "- If a webhook-triggered diagnosis needs human confirmation, the caller must also provide the current user's `auth_token` in the JSON payload with `confirmation_policy=needs_review|always`.",
            "- Confirmation target is always the current authenticated user bound to `auth_token`; repository owner, KB owner, and interface-person fields are not used for P5 routing.",
            "- Webhook audit events intentionally exclude `auth_token` and confirmation control fields.",
            "",
            "## Final Verification",
            "",
            "- `GET /diagnose/readiness` returns `ready`.",
            "- `GET /diagnose/external-state` returns `ready` and `secret_values_written=false`.",
            "- `GET /diagnose/external-inputs` returns `status=complete`.",
            "- `GET /diagnose/acceptance` returns `accepted=true`.",
            "- `diagnose-p5-external-state --env-file /data/codekb/state/p5-secrets.env --json` exits 0 and reports no pending checks.",
            "- `diagnose-acceptance --env-file /data/codekb/state/p5-secrets.env --json` exits 0.",
            f"- `diagnose-p5-final-verify --env-file /data/codekb/state/p5-secrets.env --api-base-url {base_url} --output /data/codekb/logs/p5-final-verify-report.json --json` returns `accepted=true`, `failed_required=[]`, and `pending_required=[]`.",
            "- `python3 -m unittest discover -s tests` passes on the server release.",
            "- `quality-check --fixtures data/fixtures/sample_corpus.jsonl --prefix REL --prefix TST --prefix INC --skip-missing-expected` returns `quality_gate=PASS`.",
            "- `diagnose-im-oauth-smoke --env-file /data/codekb/state/p5-secrets.env --json` verifies OAuth setup.",
            "- `diagnose-im-smoke --env-file /data/codekb/state/p5-secrets.env --json` verifies credentials and current-user delivery routing.",
            "- One real user can run `diagnose-current-user-smoke --respond --json` and receive/respond to a current-user confirmation.",
            f"- One real user can call `POST {base_url}/auth/im/confirmations/request` and see the queued confirmation in pending list.",
            "",
        ]
    )


def export_diagnose_integration_pack(
    output_dir: str | Path,
    *,
    api_base_url: str = DEFAULT_API_BASE_URL,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = diagnose_integration_artifacts(api_base_url=api_base_url)
    for name, content in _artifact_file_contents(artifacts).items():
        (output / name).write_text(content, encoding="utf-8")
    summary = {
        "api_base_url": artifacts["api_base_url"],
        "files": artifacts["files"],
        "mcp_tools": artifacts["mcp_tools"],
        "mr_card_actions": artifacts["mr_card_actions"],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def diagnose_integration_artifacts(*, api_base_url: str = DEFAULT_API_BASE_URL) -> dict[str, Any]:
    base_url = api_base_url.rstrip("/")
    tools = diagnose_mcp_tool_definitions(api_base_url=api_base_url)
    card = mr_candidate_card_template(api_base_url=api_base_url)
    artifacts: dict[str, Any] = {
        "mcp_tools.json": list(tools),
        "mr_candidate_card.json": card,
        "code_review_skill.md": render_code_review_skill(api_base_url=base_url),
        "im_entry.md": render_im_entry_guide(api_base_url=base_url),
        "current_user_auth.md": render_current_user_auth_guide(api_base_url=base_url),
        "external_handoff.md": render_external_handoff_checklist(api_base_url=base_url),
    }
    return {
        "status": "ok",
        "api_base_url": base_url,
        "files": sorted(artifacts),
        "mcp_tools": len(tools),
        "mr_card_actions": len(card["actions"]),
        "artifacts": artifacts,
    }


def _artifact_file_contents(artifacts: dict[str, Any]) -> dict[str, str]:
    payload = dict(artifacts.get("artifacts") or {})
    return {
        "mcp_tools.json": json.dumps(payload["mcp_tools.json"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "mr_candidate_card.json": json.dumps(
            payload["mr_candidate_card.json"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "code_review_skill.md": str(payload["code_review_skill.md"]),
        "im_entry.md": str(payload["im_entry.md"]),
        "current_user_auth.md": str(payload["current_user_auth.md"]),
        "external_handoff.md": str(payload["external_handoff.md"]),
    }
