#!/usr/bin/env python
import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

from ..config import DistributedType
from .config import LaunchConfig, default_config_file


def launch_command_parser(subparsers=None):
    if subparsers is not None:
        parser = subparsers.add_parser("launch")
    else:
        parser = argparse.ArgumentParser("Accelerate launch command")

    parser.add_argument(
        "--config_file", default=None, help="The config file to use for the default values in the launching script."
    )
    parser.add_argument(
        "--multi_gpu",
        default=False,
        action="store_true",
        help="Whether or not this should launch a distributed GPU training.",
    )
    parser.add_argument(
        "--tpu", default=False, action="store_true", help="Whether or not this should launch a TPU training."
    )
    parser.add_argument(
        "--fp16", default=False, action="store_true", help="Whether or not to use mixed precision training."
    )
    parser.add_argument(
        "--num_processes", type=int, default=None, help="The number of processes to be launched in parallel."
    )
    parser.add_argument(
        "training_script",
        type=str,
        help=(
            "The full path to the script to be launched in parallel, followed by all the arguments for the training "
            "script."
        ),
    )
    # Other arguments of the training scripts
    parser.add_argument("training_script_args", nargs=argparse.REMAINDER, help="Arguments of the training script.")

    if subparsers is not None:
        parser.set_defaults(func=launch_command)
    return parser


def simple_launcher(args):
    cmd = [sys.executable, args.training_script]
    cmd.extend(args.training_script_args)

    current_env = os.environ.copy()
    current_env["USE_FP16"] = str(args.fp16)

    process = subprocess.Popen(cmd, env=current_env)
    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(returncode=process.returncode, cmd=cmd)


def multi_gpu_launcher(args):
    cmd = [sys.executable, "-m", "torch.distributed.launch"]
    cmd.extend(["--nproc_per_node", str(args.num_processes)])
    if args.main_process_ip is not None:
        cmd.extend(
            [
                "--nproc_per_node",
                str(args.num_processes // args.num_machines),
                "--nnodes",
                str(args.num_machines),
                "--node_rank",
                str(args.machine_rank),
                "--master_addr",
                args.main_process_ip,
                "--node_rank",
                str(args.main_process_port),
            ]
        )
    else:
        cmd.extend(["--nproc_per_node", str(args.num_processes)])
    cmd.append(args.training_script)
    cmd.extend(args.training_script_args)

    current_env = os.environ.copy()
    current_env["USE_FP16"] = str(args.fp16)

    process = subprocess.Popen(cmd, env=current_env)
    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(returncode=process.returncode, cmd=cmd)


def tpu_launcher(args):
    import torch_xla.distributed.xla_multiprocessing as xmp

    # Import training_script as a module.
    script_path = Path(args.training_script)
    sys.path.append(str(script_path.parent.resolve()))
    mod_name = script_path.stem
    mod = importlib.import_module(mod_name)

    # Patch sys.argv
    sys.argv = [args.training_script] + args.training_script_args + ["--tpu_num_cores", str(args.num_processes)]
    xmp.spawn(mod._mp_fn, args=(), nprocs=args.num_processes)


def launch_command(args):
    # Sanity checks
    if args.multi_gpu and args.tpu:
        raise ValueError("You can only pick one between `--multi_gpu` and `--tpu`.")

    # Get the default from the config file.
    if args.config_file is not None or os.path.isfile(default_config_file):
        defaults = LaunchConfig.from_json_file(json_file=args.config_file)
        if not args.multi_gpu and not args.tpu:
            args.multi_gpu = defaults.distributed_type == DistributedType.MULTI_GPU
            args.tpu = defaults.distributed_type == DistributedType.TPU
        if args.num_processes is None:
            args.num_processes = defaults.num_processes
        if not args.fp16:
            args.fp16 = defaults.fp16
    else:
        if args.num_processes is None:
            args.num_processes = 1

    # Use the proper launcher
    if args.multi_gpu:
        multi_gpu_launcher(args)
    elif args.tpu:
        tpu_launcher(args)
    else:
        simple_launcher(args)


def main():
    parser = launch_command_parser()
    args = parser.parse_args()
    launch_command(args)


if __name__ == "__main__":
    main()