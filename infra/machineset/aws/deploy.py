#!/usr/bin/env python3
"""AWS MachineSet deploy CLI (GPU + CPU). From openshift-infra: uv run ./infra/machineset/aws/deploy.py --help"""

from openshift_infra_deploy.cli_machineset_aws import main

if __name__ == "__main__":
    main()
