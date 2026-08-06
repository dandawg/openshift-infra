# OpenShift Infrastructure Components

Infrastructure-level components for OpenShift clusters on AWS: GPU nodes, CPU worker nodes, and RWX shared storage (EFS).

## Prerequisites

- **OpenShift 4.19+** on AWS with cluster-admin access
- **`oc` CLI** logged in to your cluster (`oc whoami` should return your user)
- **`argocd` CLI** installed on your workstation ([install guide](https://argo-cd.readthedocs.io/en/stable/cli_installation/)) — the deploy CLIs use this to configure and sync apps against the ArgoCD server that `bootstrap.sh` installs on the cluster
- **[uv](https://docs.astral.sh/uv/)** and **Python 3.11+** for the deploy CLIs
- **AWS quota** for the instance types you plan to deploy

## Getting Started

```bash
git clone https://github.com/dandawg/openshift-infra.git
cd openshift-infra

# Confirm you're logged in to OpenShift
oc whoami

# Install OpenShift GitOps / ArgoCD (safe to re-run — skips if already installed)
./bootstrap.sh

# Install the deploy CLIs
uv sync
source .venv/bin/activate
```

Once bootstrap completes, it prints the ArgoCD URL and a command to retrieve the credentials. After activating the virtualenv you have two commands available:

- `openshift-infra-machineset-aws` — deploy GPU or CPU MachineSets
- `openshift-infra-efs-aws` — deploy EFS / RWX storage

Run either with `--help` to see all options. You're now ready to deploy any component below.

---

## Deploy RWX Storage (AWS EFS)

Deploys the **AWS EFS CSI Driver Operator**, creates an EFS file system in your cluster's VPC, and creates a `StorageClass` (default `efs-csi`) for **ReadWriteMany** PVCs.

> **Prerequisite:** Run [Getting Started](#getting-started) first (`./bootstrap.sh`, `uv sync`, `source .venv/bin/activate`).

### Quick deploy

```bash
openshift-infra-efs-aws
```

The CLI automatically:
1. Gathers cluster info (name, region, VPC)
2. Provisions an EFS file system via a short-lived Job using `kube-system/aws-creds`
3. Logs in to ArgoCD
4. Creates and syncs the ArgoCD Application (EFS CSI operator + StorageClass)

### Reuse an existing EFS file system

If you already have an EFS `fs-xxxxxxxx` with mount targets in the cluster VPC:

```bash
openshift-infra-efs-aws --efs-file-system-id fs-xxxxxxxx
```

### Custom StorageClass name

```bash
openshift-infra-efs-aws --storage-class-name efs-scratch
```

### Verify

```bash
oc get storageclass efs-csi
oc get clusterscsidriver efs.csi.aws.com
oc get pods -n openshift-cluster-csi-drivers -l app.kubernetes.io/name=aws-efs-csi-driver
```

### List EFS file systems (no AWS console needed)

```bash
# EFS with mount targets in this cluster's VPC (default)
./infra/efs/aws/list-efs.sh

# All EFS in the account/region
LIST_EFS_SCOPE=region ./infra/efs/aws/list-efs.sh
```

### Tear down

Removes mount targets, security group, file system in AWS, plus the ArgoCD app and StorageClass:

```bash
EFS_FILE_SYSTEM_ID=fs-xxxxxxxx ./infra/efs/aws/teardown.sh
```

For manual steps, troubleshooting, and production notes, see [infra/efs/aws/README.md](infra/efs/aws/README.md).

---

## Deploy GPU Nodes

Deploys GPU-enabled worker nodes as an OpenShift MachineSet via ArgoCD.

> **Prerequisite:** Run [Getting Started](#getting-started) first (`./bootstrap.sh`, `uv sync`, `source .venv/bin/activate`).

### Quick deploy (default: g6.2xlarge)

```bash
openshift-infra-machineset-aws
```

### Choose an instance type

```bash
# Cost-effective (T4)
openshift-infra-machineset-aws --instance-type g4dn.xlarge

# High GPU memory (L40S, 48GB)
openshift-infra-machineset-aws --instance-type g6e.2xlarge

# NVIDIA H100 (see availability note below)
openshift-infra-machineset-aws --instance-type p5.4xlarge
```

### Configuration options

| Flag | Env Variable | Default | Description |
|---|---|---|---|
| `--instance-type` | `INSTANCE_TYPE` | `g6.2xlarge` | AWS instance type (see [Instance Types](#available-gpu-instance-types)) |
| `--root-volume-size` | `ROOT_VOLUME_SIZE` | `120` (200 for p5) | Root volume size in GB |
| `--root-volume-type` | `ROOT_VOLUME_TYPE` | `gp3` | EBS volume type |
| `--root-volume-iops` | `ROOT_VOLUME_IOPS` | `3000` | IOPS for gp3 volumes |
| `--replicas` | `REPLICAS` | `1` | Number of GPU nodes to create |
| `--availability-zone` | `AVAILABILITY_ZONE` | auto-detected | Override the AZ (useful when capacity is limited) |

**Example with custom settings:**

```bash
openshift-infra-machineset-aws --instance-type g6.4xlarge --root-volume-size 200 --replicas 2
```

### Deploy multiple GPU types

Each instance type creates a uniquely named MachineSet, so you can deploy several at once:

```bash
openshift-infra-machineset-aws --instance-type g4dn.xlarge   # Embedding models
openshift-infra-machineset-aws --instance-type g6.2xlarge     # Production inference
openshift-infra-machineset-aws --instance-type g6.4xlarge     # Vision models / high throughput
```

### Verify

```bash
# Watch MachineSet creation
oc get machineset -n openshift-machine-api -w

# Wait for GPU node to be ready (5-10 minutes)
oc wait --for=condition=Ready nodes -l nvidia.com/gpu.present=true --timeout=600s

# View all GPU nodes
oc get nodes -l nvidia.com/gpu.present=true

# View nodes by instance type
oc get nodes -l gpu-instance-type=g6.2xlarge
```

For detailed GPU documentation, see [infra/gpu-machineset/README.md](infra/gpu-machineset/README.md).

---

## Deploy CPU Worker Nodes

Deploys high-capacity CPU worker nodes for workloads that need more compute but not GPUs (e.g., RHOAI platform pods, data preprocessing).

> **Prerequisite:** Run [Getting Started](#getting-started) first (`./bootstrap.sh`, `uv sync`, `source .venv/bin/activate`).

The same `openshift-infra-machineset-aws` command handles CPU instance types — it detects GPU vs CPU automatically.

### Quick deploy (default CPU: m6a.4xlarge)

```bash
openshift-infra-machineset-aws --instance-type m6a.4xlarge
```

### Configuration options

Same flags as GPU (see [above](#configuration-options)). Available CPU instance types:

- **m6a.4xlarge**: 16 vCPU, 64GB RAM, AMD EPYC (~$0.69/hr)
- **m6i.4xlarge**: 16 vCPU, 64GB RAM, Intel Xeon (~$0.77/hr)

**Example:**

```bash
openshift-infra-machineset-aws --instance-type m6a.4xlarge --root-volume-size 200 --replicas 2
```

### Verify

```bash
oc get machineset -n openshift-machine-api -w
oc get nodes -l node-role.kubernetes.io/worker
```

---

## Available GPU Instance Types

| Instance Type | GPUs | GPU Memory | vCPUs | RAM | Cost/hr* | Best For |
|---|---|---|---|---|---|---|
| g4dn.xlarge | 1x T4 | 16GB | 4 | 16GB | ~$0.53 | Embedding models, small 7B models |
| g6.2xlarge | 1x L4 | 24GB | 8 | 32GB | ~$1.10 | Production inference, Granite 7B |
| g6.4xlarge | 1x L4 | 24GB | 16 | 64GB | ~$2.15 | Vision models, high throughput |
| g6e.2xlarge | 1x L40S | 48GB | 8 | 64GB | ~$2.24 | Large models (13B+), multi-modal |
| p5.4xlarge | 1x H100 | 80GB | 16 | 256GB | varies | H100 training, very large models |

*Approximate on-demand pricing (us-east-1, subject to change)

List all supported types: `openshift-infra-machineset-aws --list-instance-types`

> **P5 availability:** `p5.4xlarge` capacity is limited and may not be available in all regions or AZs. Check availability with the [EC2 DescribeInstanceTypeOfferings API](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html). If your default AZ lacks capacity, use `--availability-zone us-east-2a`.

---

## Cost Management

GPU nodes are expensive. Scale down when not in use:

```bash
# Scale down a specific GPU type
oc scale machineset $(oc get machineset -n openshift-machine-api -l gpu-instance-type=g6.2xlarge -o name) \
  --replicas=0 -n openshift-machine-api

# Scale back up
oc scale machineset $(oc get machineset -n openshift-machine-api -l gpu-instance-type=g6.2xlarge -o name) \
  --replicas=1 -n openshift-machine-api
```

**Tips:**
- Start with **g4dn.xlarge** for development and testing (~$0.53/hr)
- Use **g6.2xlarge** for production single-model workloads (~$1.10/hr)
- Reserve **g6.4xlarge** and above for high-performance scenarios
- Consider AWS Spot instances for 60-70% discount
- Set up billing alerts in AWS

---

## Alternative: Bash Scripts

If you prefer not to install the Python CLIs, each component also has a standalone bash deploy script. These accept configuration via environment variables:

```bash
# EFS / RWX storage
./infra/efs/aws/deploy.sh
EFS_FILE_SYSTEM_ID=fs-xxxxxxxx ./infra/efs/aws/deploy.sh

# GPU MachineSets
./infra/gpu-machineset/aws/deploy.sh
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh

# CPU MachineSets
./infra/cpu-machineset/aws/deploy.sh
```

---

## Customization

### Fork this repository

If you fork this repository, update the `repoURL` in all GitOps manifests:

```bash
find gitops/ -name "*.yaml" -type f -exec sed -i '' \
  's|repoURL: .*|repoURL: https://github.com/YOUR-ORG/openshift-infra|g' {} \;
```

### Adjust GPU/CPU configuration

Edit parameters in the Helm values files to customize instance type, replicas, volume size, taints, and labels:
- GPU: `infra/gpu-machineset/aws/helm/values-*.yaml`
- CPU: `infra/cpu-machineset/aws/helm/values-*.yaml`

---

## Troubleshooting

### ArgoCD not found / deploy fails

If a deploy command fails with "OpenShift GitOps not found", run bootstrap first:

```bash
./bootstrap.sh
```

### GPU nodes not ready

```bash
oc get machineset -n openshift-machine-api
oc describe machine <machine-name> -n openshift-machine-api
```

Common causes: AWS quota limit reached, instance type not available in your AZ, IAM permissions issue.

### Node doesn't show GPU

```bash
oc get nodes -L nvidia.com/gpu.present
oc get pods -n nvidia-gpu-operator
```

The NVIDIA GPU Operator must be installed (typically as part of RHOAI deployment).

### Error: "Resource not found: REPLACE_ME-gpu-REPLACE_ME"

This means the ArgoCD Application was applied without setting Helm parameters. Re-run the deploy CLI to fix:

```bash
openshift-infra-machineset-aws --instance-type g4dn.xlarge
```

---

## Repository Structure

```
openshift-infra/
├── README.md
├── bootstrap.sh                 # Installs OpenShift GitOps (run first)
├── bootstrap/                   # GitOps operator manifests
├── pyproject.toml               # Python package — installs the deploy CLIs
├── openshift_infra_deploy/      # CLI source (Click commands + deploy logic)
├── gitops/infra/                # ArgoCD Application manifests
├── infra/
│   ├── efs/aws/                 # EFS RWX storage
│   │   ├── deploy.sh            # Bash deploy script (alternative)
│   │   ├── teardown.sh          # Tear down EFS + ArgoCD app
│   │   ├── list-efs.sh          # List EFS file systems
│   │   └── helm/                # Helm chart
│   ├── gpu-machineset/aws/      # GPU node MachineSets
│   │   ├── deploy.sh            # Bash deploy script (alternative)
│   │   └── helm/                # Helm chart + per-type values
│   ├── cpu-machineset/aws/      # CPU node MachineSets
│   │   ├── deploy.sh            # Bash deploy script (alternative)
│   │   └── helm/                # Helm chart
│   └── machineset/aws/
│       └── deploy.py            # Standalone script (uv run ./infra/machineset/aws/deploy.py)
```

## Documentation

- [GPU MachineSets](infra/gpu-machineset/README.md) — Detailed GPU documentation, scheduling, labels, cost comparison
- [AWS GPU MachineSets](infra/gpu-machineset/aws/README.md) — AWS-specific details, Helm chart config, regional availability
- [AWS EFS RWX Storage](infra/efs/aws/README.md) — Manual steps, troubleshooting, production notes

## Related Repositories

- [rhoai-deploy](https://github.com/redhat-ai-americas/rhoai-deploy) — Red Hat OpenShift AI platform deployment
- [rhoai-anythingllm-demos](https://github.com/dandawg/rhoai-anythingllm-demos) — RHOAI AnythingLLM demos (references this repo for node provisioning)

## License

Apache License 2.0
