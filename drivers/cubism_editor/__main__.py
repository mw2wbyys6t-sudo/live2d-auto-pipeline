"""CLI：python -m drivers.cubism_editor {probe,watch,drive}。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional

from .client import CubismEditorClient
from .protocol import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LATEST_VERSION,
    NOTIFY_METHODS,
    CubismProtocolError,
)


def _print_json(obj: Dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--name", default="Pygmalion Studio", help="RegisterPlugin 显示名")
    parser.add_argument("--version", default=LATEST_VERSION, help="协议版本")
    parser.add_argument("--token", default=None, help="复用已授权的插件 token")
    parser.add_argument("--token-file", default=None, help="token 存取文件（授权后自动写入）")
    parser.add_argument("--timeout", type=float, default=10.0, help="请求超时秒数")


def _make_client(args: argparse.Namespace) -> CubismEditorClient:
    return CubismEditorClient(
        args.host,
        args.port,
        name=args.name,
        version=args.version,
        token=args.token,
        token_file=args.token_file,
        request_timeout=args.timeout,
    )


async def _probe(args: argparse.Namespace) -> int:
    async with _make_client(args) as client:
        info: Dict[str, Any] = {
            "api_version": await client.get_api_version(),
            "edit_mode": await client.get_current_edit_mode(),
            "approved": await client.get_is_approval(),
            "token_saved": args.token_file if args.token_file else None,
        }
        try:
            uid = await client.get_current_model_uid()
            info["model_uid"] = uid
            params = await client.get_parameters(uid)
            info["parameter_count"] = len(params)
            info["parameter_ids"] = [p.id for p in params[:20]]
        except CubismProtocolError as exc:
            info["model_error"] = exc.error_type
        _print_json(info)
    return 0


async def _watch(args: argparse.Namespace) -> int:
    queue: asyncio.Queue = asyncio.Queue()

    def enqueue(method: str):
        def handler(data: Dict[str, Any]) -> None:
            queue.put_nowait({"event": method, "data": data})

        return handler

    async with _make_client(args) as client:
        methods: List[str] = args.methods or list(NOTIFY_METHODS)
        for method in methods:
            client.on_event(method, enqueue(method))
            accepted = await client.subscribe(method)
            _print_json({"subscribed": method, "accepted": accepted})
        _print_json({"watching": True, "hint": "Ctrl+C 退出"})
        while True:
            _print_json(await queue.get())


async def _drive(args: argparse.Namespace) -> int:
    async with _make_client(args) as client:
        uid = args.model_uid or await client.get_current_model_uid()
        if args.clear:
            await client.clear_parameter_values(uid)
            _print_json({"cleared": uid})
        if args.set:
            values: Dict[str, float] = {}
            for item in args.set:
                pid, _, raw = item.partition("=")
                if not pid or not raw:
                    raise SystemExit(f"--set 需要 ID=VALUE 形式: {item!r}")
                values[pid] = float(raw)
            await client.set_parameter_values(uid, values)
            _print_json({"set": values, "model_uid": uid})
        if args.get is not None:
            ids: Optional[List[str]] = args.get or None
            current = await client.get_parameter_values(uid, ids)
            _print_json({"values": current, "model_uid": uid})
        if not (args.clear or args.set or args.get is not None):
            current = await client.get_parameter_values(uid)
            _print_json({"values": current, "model_uid": uid})
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m drivers.cubism_editor",
        description="驱动真实 Cubism Editor（需在编辑器设置里启用外部联动）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="连接并汇报编辑器状态")
    _add_common_args(probe)

    watch = sub.add_parser("watch", help="订阅导出/编辑模式事件，逐行打印 JSON")
    _add_common_args(watch)
    watch.add_argument(
        "--methods", nargs="*", choices=list(NOTIFY_METHODS), default=None,
        help="只订阅指定事件（默认全部）",
    )

    drive = sub.add_parser("drive", help="读写当前模型参数")
    _add_common_args(drive)
    drive.add_argument("--model-uid", default=None, help="默认取编辑器当前模型")
    drive.add_argument("--set", nargs="+", metavar="ID=VALUE", help="写入参数值")
    drive.add_argument("--get", nargs="*", metavar="ID", help="读取指定参数（缺省读全部）")
    drive.add_argument("--clear", action="store_true", help="先清空外部写入的参数值")

    args = parser.parse_args(argv)
    handlers = {"probe": _probe, "watch": _watch, "drive": _drive}
    try:
        return asyncio.run(handlers[args.command](args))
    except KeyboardInterrupt:
        return 130
    except CubismProtocolError as exc:
        print(f"editor error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())