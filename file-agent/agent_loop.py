"""Scratch-model and optional cloud function-calling loop for the file agent."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from agent_log import log_event, new_request_id
from file_agent import execute_tool, get_tool_definitions


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_MAX_ITERATIONS = 5
DEFAULT_WORKSPACE = Path(__file__).resolve().parent / "agent_workspace"
DEFAULT_SCRATCH_CHECKPOINT = (
    Path(__file__).resolve().parent.parent / "model" / "scratch" / "final.pt"
)
SYSTEM_PROMPT = """\
You are a careful local file assistant. Use only the provided tools to inspect
or modify files. All paths are relative to the configured workspace and must
stay inside it. Never claim an operation succeeded when a tool reports an
error. Explain the changes in your final response.
"""


class AgentLoopError(RuntimeError):
    """Expected, user-facing agent error."""


def _local_tool_call(message: str) -> tuple[str, dict[str, Any]] | None:
    """Translate a few explicit file requests into local tool calls.

    This is intentionally a small, deterministic fallback. It lets the app
    remain useful without an AI credential; a managed Replit AI connection can
    provide open-ended natural-language understanding when enabled.
    """
    text = message.strip()
    lower = text.lower()

    if lower in {"list", "ls", "list files", "show files", "اعرض الملفات", "اعرض الملفات والمجلدات"}:
        return "list_files", {"path": "."}

    match = re.match(r"^(?:read|cat|اقرأ(?: الملف)?)\s+(.+)$", text, re.IGNORECASE)
    if match:
        return "read_file", {"path": match.group(1).strip(" \"'")}

    match = re.match(
        r"^(?:create|write|أنشئ(?: ملف)?|اكتب(?: في ملف)?)\s+(\S+)\s+"
        r"(?:with content|content|واكتب بداخله|بمحتوى)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if match:
        return "write_file", {
            "path": match.group(1).strip(" \"'"),
            "content": match.group(2),
        }

    match = re.match(r"^(?:write|اكتب)\s+(\S+)\s*:::\s*(.*)$", text, re.IGNORECASE)
    if match:
        return "write_file", {"path": match.group(1), "content": match.group(2)}

    match = re.match(
        r"^(?:append|أضف)\s+(.+?)\s+(?:to|إلى)\s+(?:file\s+|الملف\s+)?(\S+)$",
        text,
        re.IGNORECASE,
    )
    if match:
        return "append_file", {"path": match.group(2), "content": match.group(1), "create": False}

    match = re.match(r"^(?:delete|remove|احذف(?: الملف)?)\s+(\S+)$", text, re.IGNORECASE)
    if match:
        return "delete_file", {"path": match.group(1)}

    match = re.match(
        r"^(?:move|انقل)\s+(\S+)\s+(?:to|إلى)\s+(\S+)$", text, re.IGNORECASE
    )
    if match:
        return "move_file", {"source": match.group(1), "destination": match.group(2)}

    match = re.match(r"^(?:mkdir|make directory|أنشئ مجلد)\s+(\S+)$", text, re.IGNORECASE)
    if match:
        return "make_directory", {"path": match.group(1)}

    return None


def _local_agent_loop(
    message: str,
    root: Path,
    request_id: str,
    *,
    fallback_reason: str | None = None,
) -> str:
    """Run the deterministic fallback used when no scratch checkpoint is ready."""
    tool_call = _local_tool_call(message)
    label = (
        f"[FALLBACK: scratch model unavailable ({fallback_reason})]"
        if fallback_reason
        else "[FALLBACK: deterministic local command mode]"
    )
    if tool_call is None:
        response = (
            f"{label}\n"
            "الوضع المحلي يعمل بدون API key. استخدم أحد الأوامر الواضحة مثل:\n"
            "• اعرض الملفات\n"
            "• اقرأ notes/today.txt\n"
            "• أنشئ ملف notes/test.txt واكتب بداخله مرحباً\n"
            "• أضف سطر جديد إلى notes/test.txt\n"
            "• انقل notes/test.txt إلى archive/test.txt\n"
            "• احذف الملف archive/test.txt"
        )
        log_event(request_id, "local_mode_help", response=response)
        return response

    tool_name, arguments = tool_call
    log_event(request_id, "tool_requested", tool=tool_name, arguments=arguments, mode="local")
    result = execute_tool(tool_name, arguments, root)
    log_event(request_id, "tool_result", tool=tool_name, result=result, mode="local")
    if result.get("ok"):
        response = (
            f"{label}\n"
            f"تم تنفيذ العملية بنجاح.\n"
            f"العملية: {tool_name}\n"
            f"النتيجة: {json.dumps(result['result'], ensure_ascii=False)}"
        )
    else:
        response = (
            f"{label}\n"
            f"تعذر تنفيذ العملية.\n"
            f"العملية: {tool_name}\n"
            f"الخطأ: {result['error']['message']}"
        )
    log_event(request_id, "model_response", mode="local", response=response, tool_count=1)
    return response


def _parse_local_model_response(text: str) -> tuple[str, str | None, dict[str, Any] | None]:
    """Parse JSON tool/final responses while accepting ordinary text responses."""
    stripped = text.strip()
    candidates = [stripped]
    tagged = re.search(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL)
    if tagged:
        candidates.insert(0, tagged.group(1).strip())
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    # A small model may put a short natural-language prefix before its JSON.
    # Decode every object that starts in the output instead of requiring a
    # perfect one-object response.
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            payload, end = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if end:
            candidates.insert(0, json.dumps(payload, ensure_ascii=False))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("tool") == "final" or payload.get("type") == "final":
            return "final", str(payload.get("content", "")), None
        tool_call = payload.get("tool_call")
        if isinstance(tool_call, dict):
            payload = tool_call
        tool_name = payload.get("tool") or payload.get("name")
        arguments = payload.get("arguments", payload.get("args", {}))
        if isinstance(tool_name, str) and isinstance(arguments, dict):
            return "tool", tool_name, arguments
    return "text", text.strip(), None


def _scratch_prompt(transcript: str) -> str:
    """Add the generation instruction without requiring a hosted chat format."""
    return (
        transcript
        + "\nReply with either a natural-language answer or exactly one JSON object. "
        'For a tool use {"tool":"tool_name","arguments":{...}}. '
        'For a final JSON answer use {"tool":"final","content":"..."}.'
        "\nAssistant:"
    )


def _scratch_tool_summary() -> str:
    """Keep the tool guidance small enough for the scratch model's context."""
    names = [
        definition.get("function", {}).get("name")
        for definition in get_tool_definitions()
        if isinstance(definition, dict)
    ]
    return ", ".join(name for name in names if isinstance(name, str))


def _local_model_loop(
    message: str,
    root: Path,
    request_id: str,
    model_path: str | Path,
    max_iterations: int,
) -> str:
    """Use the from-scratch checkpoint, or fall back without one."""
    path = Path(model_path).expanduser().resolve()
    if path.is_dir():
        path = path / "final.pt"
    if not path.exists():
        log_event(
            request_id,
            "local_model_fallback",
            reason="scratch_checkpoint_missing",
            model_path=str(path),
        )
        return _local_agent_loop(
            message,
            root,
            request_id,
            fallback_reason=f"checkpoint not found: {path}",
        )
    try:
        import torch
        from hwk_model import ByteTokenizer, load_checkpoint
        from hwk_model.generation import generate_text
    except ImportError:
        log_event(
            request_id,
            "local_model_fallback",
            reason="scratch_dependencies_missing",
            model_path=str(path),
        )
        return _local_agent_loop(
            message,
            root,
            request_id,
            fallback_reason="scratch model dependencies are not installed",
        )

    try:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        scratch_model, checkpoint = load_checkpoint(path, device=device)
    except Exception as exc:
        log_event(
            request_id,
            "local_model_fallback",
            reason="model_load_failed",
            error=str(exc),
            model_path=str(path),
        )
        return _local_agent_loop(
            message,
            root,
            request_id,
            fallback_reason=f"checkpoint could not be loaded: {exc}",
        )

    transcript = (
        f"System: {SYSTEM_PROMPT}\n"
        f"Available tools: {_scratch_tool_summary()}\n"
        f"User: {message.strip()}\n"
    )
    for iteration in range(1, max_iterations + 1):
        prompt = _scratch_prompt(transcript)
        log_event(
            request_id,
            "model_request",
            iteration=iteration,
            provider="scratch_model",
            model_path=str(path),
            checkpoint_step=checkpoint.get("step", 0),
            tool_choice="json_protocol",
        )
        try:
            tokenizer = ByteTokenizer()
            output = generate_text(
                scratch_model,
                tokenizer,
                prompt,
                max_new_tokens=256,
                temperature=0,
                top_k=0,
            )
        except Exception as exc:
            raise AgentLoopError(f"Scratch model generation failed: {exc}") from exc
        output = str(output).strip()

        kind, value, arguments = _parse_local_model_response(output)
        if kind in {"final", "text"}:
            final = value or "The scratch model returned an empty response."
            log_event(
                request_id,
                "model_response",
                iteration=iteration,
                provider="scratch_model",
                response=final,
                tool_count=0,
            )
            return final
        if value is None or arguments is None:
            raise AgentLoopError("The scratch model returned an invalid tool request")
        log_event(
            request_id,
            "tool_requested",
            tool=value,
            arguments=arguments,
            mode="scratch_model",
        )
        result = execute_tool(value, arguments, root)
        log_event(
            request_id,
            "tool_result",
            tool=value,
            result=result,
            mode="scratch_model",
        )
        transcript += (
            f"Assistant: {output}\n"
            f"Tool {value} result: {json.dumps(result, ensure_ascii=False)}\n"
        )
    raise AgentLoopError(
        f"The scratch model reached its maximum of {max_iterations} iterations."
    )


def _workspace_path(value: str | Path | None) -> Path:
    root = Path(value or os.getenv("AGENT_WORKSPACE", DEFAULT_WORKSPACE)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise AgentLoopError(f"Agent workspace is not a directory: {root}")
    return root


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise AgentLoopError("The model returned invalid tool arguments")
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentLoopError(f"The model returned malformed tool arguments: {exc}") from exc
    if not isinstance(arguments, dict):
        raise AgentLoopError("The model's tool arguments must be a JSON object")
    return arguments


def _run_tool_call(
    tool_call: dict[str, Any], root: Path, request_id: str
) -> dict[str, Any]:
    function = tool_call.get("function")
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        raise AgentLoopError("The model returned an invalid tool call")
    tool_name = function["name"]
    arguments = _parse_arguments(function.get("arguments", "{}"))
    log_event(
        request_id,
        "tool_requested",
        tool=tool_name,
        arguments=arguments,
    )
    result = execute_tool(
        tool_name,
        arguments,
        root,
    )
    log_event(request_id, "tool_result", tool=tool_name, result=result)
    return {
        "role": "tool",
        "tool_call_id": str(tool_call.get("id", "")),
        "content": json.dumps(result, ensure_ascii=False),
    }


def _api_error(response: Any) -> str:
    try:
        payload = response.json()
    except (ValueError, requests.RequestException):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return getattr(response, "text", "") or f"HTTP {response.status_code}"


def _agent_loop(
    user_message: str,
    workspace_root: str | Path | None = None,
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    print_final: bool = True,
    http_client: Any | None = None,
    request_id: str,
    mode: str = "local",
    local_model_path: str | Path | None = None,
) -> str:
    """Run the selected model/tool loop and return the final assistant message."""
    if not isinstance(user_message, str) or not user_message.strip():
        raise AgentLoopError("Please provide a non-empty user request")
    if max_iterations < 1:
        raise AgentLoopError("max_iterations must be at least 1")
    root = _workspace_path(workspace_root)
    if mode == "local":
        local_path = local_model_path or os.getenv(
            "LOCAL_MODEL_PATH",
            DEFAULT_SCRATCH_CHECKPOINT,
        )
        log_event(
            request_id,
            "provider_selected",
            provider="scratch_model",
            model_path=str(local_path),
        )
        response = _local_model_loop(
            user_message,
            root,
            request_id,
            local_path,
            max_iterations,
        )
        if print_final:
            print(response)
        return response
    if mode != "cloud":
        raise AgentLoopError("mode must be either 'local' or 'cloud'")

    managed_key = os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY")
    managed_base_url = os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if managed_key and managed_base_url:
        api_key = managed_key
        endpoint = f"{managed_base_url.rstrip('/')}/chat/completions"
        selected_model = (
            model if model != DEFAULT_MODEL else os.getenv("AI_INTEGRATIONS_OPENAI_MODEL", "gpt-4o-mini")
        )
        provider = "replit_ai"
    elif openrouter_key:
        api_key = openrouter_key
        endpoint = OPENROUTER_URL
        selected_model = model
        provider = "openrouter"
    else:
        raise AgentLoopError(
            "لا يوجد اتصال سحابي مفعّل. اختر «نموذج محلي» أو فعّل Replit AI "
            "المُدار؛ لا تحتاج إلى إدخال مفتاح شخصي للوضع المحلي."
        )

    log_event(
        request_id,
        "provider_selected",
        provider=provider,
        model=selected_model,
    )
    client = http_client or requests
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
        "X-Title": os.getenv("OPENROUTER_APP_NAME", "Local File Agent"),
    }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message.strip()},
    ]

    for _ in range(max_iterations):
        iteration = _ + 1
        payload = {
            "model": selected_model,
            "messages": list(messages),
            "tools": get_tool_definitions(),
            "tool_choice": "auto",
        }
        log_event(
            request_id,
            "model_request",
            iteration=iteration,
            model=selected_model,
            provider=provider,
            tool_choice="auto",
        )
        try:
            response = client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=90,
            )
        except requests.RequestException as exc:
            raise AgentLoopError(f"تعذر الاتصال بمزود الذكاء السحابي: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise AgentLoopError(
                f"AI provider returned HTTP {response.status_code}: {_api_error(response)}"
            )
        try:
            response_payload = response.json()
            assistant_message = response_payload["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AgentLoopError("أعاد مزود الذكاء السحابي استجابة غير صالحة") from exc
        if not isinstance(assistant_message, dict):
            raise AgentLoopError("أعاد مزود الذكاء السحابي رسالة غير صالحة")

        messages.append(assistant_message)
        tool_calls = assistant_message.get("tool_calls") or []
        log_event(
            request_id,
            "model_response",
            iteration=iteration,
            tool_count=len(tool_calls) if isinstance(tool_calls, list) else None,
            tool_names=[
                call.get("function", {}).get("name")
                for call in tool_calls
                if isinstance(call, dict)
                and isinstance(call.get("function"), dict)
            ]
            if isinstance(tool_calls, list)
            else None,
            response=assistant_message.get("content"),
        )
        if not tool_calls:
            final = assistant_message.get("content")
            if not isinstance(final, str) or not final.strip():
                raise AgentLoopError("The model returned an empty final response")
            if print_final:
                print(final)
            return final
        if not isinstance(tool_calls, list):
            raise AgentLoopError("The model returned invalid tool calls")
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                raise AgentLoopError("The model returned an invalid tool call")
            messages.append(_run_tool_call(tool_call, root, request_id))

    raise AgentLoopError(
        f"The agent reached its maximum of {max_iterations} iterations without a final response."
    )


def agent_loop(
    user_message: str,
    workspace_root: str | Path | None = None,
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    print_final: bool = True,
    http_client: Any | None = None,
    mode: str = "local",
    local_model_path: str | Path | None = None,
) -> str:
    """Run the agent and record the complete request lifecycle."""
    request_id = new_request_id()
    started_at = datetime.now(timezone.utc)
    log_event(
        request_id,
        "request_started",
        user_message=user_message,
        workspace=str(workspace_root or os.getenv("AGENT_WORKSPACE", DEFAULT_WORKSPACE)),
        model=model,
        max_iterations=max_iterations,
    )
    try:
        final_response = _agent_loop(
            user_message,
            workspace_root,
            model=model,
            max_iterations=max_iterations,
            print_final=print_final,
            http_client=http_client,
            request_id=request_id,
            mode=mode,
            local_model_path=local_model_path,
        )
    except Exception as exc:
        elapsed_ms = int(
            (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
        )
        log_event(
            request_id,
            "error",
            error_type=type(exc).__name__,
            error=str(exc),
            duration_ms=elapsed_ms,
        )
        raise

    elapsed_ms = int(
        (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
    )
    log_event(
        request_id,
        "response_sent",
        response=final_response,
        duration_ms=elapsed_ms,
    )
    return final_response


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local file agent.")
    parser.add_argument("request", help="Request to send to the agent.")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    args = parser.parse_args()
    try:
        agent_loop(args.request, args.workspace, max_iterations=args.max_iterations)
    except AgentLoopError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()