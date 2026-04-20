#!/usr/bin/env python3
"""EFS deploy CLI. From openshift-infra: uv run ./infra/efs/aws/deploy.py --help"""

from openshift_infra_deploy.cli_efs import main

if __name__ == "__main__":
    main()
