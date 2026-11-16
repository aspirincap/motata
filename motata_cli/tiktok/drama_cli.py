"""CLI for local Drama Series planning and explicit remote execution."""
from __future__ import annotations

from pathlib import Path

from motata_cli.common.display import print_output
from motata_cli.common.errors import CliError
from motata_cli.common.utils import load_json_file, write_json_file
from motata_cli.tiktok.operations import OperationStore
from motata_cli.tiktok.services import drama_series as service


def run(args):
    from motata_cli.tiktok.commands import load_payload, resolve_tiktok_client
    command = args.drama_command
    if command == "plan":
        plan = service.build_plan(load_payload(args), getattr(args, "advertiser_id", None))
        if args.out:
            write_json_file(Path(args.out), plan)
        print_output(plan, as_json=True)
        return 0
    if command == "list":
        advertiser, client = resolve_tiktok_client(args)
        with client:
            result = client.list_native_series(advertiser, identity_id=args.identity_id,
                                               identity_type=args.identity_type,
                                               identity_authorized_bc_id=args.identity_authorized_bc_id,
                                               page=args.page, page_size=args.page_size)
    else:
        plan = load_json_file(Path(args.plan_file))
        service.validate_local(plan)
        if args.advertiser_id and str(args.advertiser_id) != plan["advertiser_id"]:
            raise CliError("CLI advertiser conflicts with the saved Drama Series plan.")
        args.advertiser_id = plan["advertiser_id"]
        _, client = resolve_tiktok_client(args)
        with client:
            if command == "validate":
                result = service.validate_remote(plan, client)
            else:
                store = OperationStore(getattr(args, "state_file", None))
                if command == "reconcile":
                    result = service.reconcile(plan, client, job_id=args.job_id or plan["plan_id"],
                                               step=args.step, object_id=args.object_id, store=store)
                else:
                    result = service.execute(plan, client, job_id=args.job_id, store=store,
                                             retry_failed=command == "resume")
    print_output(result, as_json=True)
    return result.get("exit_code", 0)


def register(parent, *, deps):
    root = parent.add_parser("drama-series", help="Upgraded Smart+ Drama Series workflows")
    sub = root.add_subparsers(dest="drama_command", required=True)
    for name in ("list", "plan", "validate", "create", "resume", "reconcile"):
        parser = sub.add_parser(name)
        deps.add_tiktok_auth_arguments(parser)
        if name == "list":
            parser.add_argument("--identity-id", required=True)
            parser.add_argument("--identity-type", choices=("TT_USER", "BC_AUTH_TT"), required=True)
            parser.add_argument("--identity-authorized-bc-id")
            parser.add_argument("--page", type=int, default=1)
            parser.add_argument("--page-size", type=int, default=20)
        elif name == "plan":
            deps.add_payload_arguments(parser)
            parser.add_argument("--out")
        else:
            parser.add_argument("--plan-file", required=True)
            if name != "validate":
                parser.add_argument("--job-id")
                parser.add_argument("--state-file", help="Optional local operation database path")
            if name == "reconcile":
                parser.add_argument("--step", choices=("campaign", "adgroup", "ad"), required=True)
                parser.add_argument("--object-id", required=True, help="Explicitly identified existing remote object")
        parser.set_defaults(func=run)
