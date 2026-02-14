# OpenShift Infrastructure Components

Infrastructure-level components for OpenShift clusters, focused on GPU-enabled node provisioning and other foundational infrastructure elements.

## Overview

This repository provides production-ready infrastructure components for OpenShift:
- **GPU MachineSets**: Automated deployment of GPU-enabled nodes on AWS
- **CPU MachineSets**: Automated deployment of high-capacity CPU worker nodes on AWS
- **Multi-GPU Support**: Deploy different GPU instance types (g4dn, g6, g6e) simultaneously
- **GitOps Ready**: ArgoCD Application manifests with deployment scripts
- **Cost Optimized**: Choose the right instance type for your workload

## Repository Structure

```
openshift-infra/
├── README.md              # This file
├── bootstrap.sh           # GitOps installer script
├── bootstrap/             # GitOps operator manifests
│   └── gitops-operator/
├── gitops/               # ArgoCD Application manifests
│   └── infra/           # Infrastructure components
│       ├── gpu-machineset-aws-g4dn-xlarge.yaml
│       ├── gpu-machineset-aws-g6.yaml
│       ├── gpu-machineset-aws-g6-4xlarge.yaml
│       ├── gpu-machineset-aws-g6e.yaml
│       └── cpu-machineset-aws-m6a-4xlarge.yaml
└── infra/               # Infrastructure component definitions
    ├── gpu-machineset/  # GPU node templates
    │   ├── README.md    # GPU MachineSets documentation
    │   ├── base/        # Base MachineSet template
    │   └── aws/         # AWS-specific configurations
    │       ├── deploy.sh       # Deployment script
    │       ├── helm/           # Helm chart for ArgoCD
    │       └── overlays/       # Kustomize overlays
    └── cpu-machineset/  # CPU worker node templates
        └── aws/         # AWS-specific configurations
            ├── deploy.sh       # Deployment script
            └── helm/           # Helm chart for ArgoCD
```

## Prerequisites

- **OpenShift 4.19+** on AWS with cluster-admin access
- **OpenShift GitOps** (ArgoCD) installed - See installation below
- **`oc` CLI** and **`argocd` CLI** installed
- **AWS quota** for GPU and CPU instances in your region

### Install OpenShift GitOps (if needed)

```bash
./bootstrap.sh
```

**Note:** If GitOps is already installed (e.g., from deploying another repository), the bootstrap script will detect it and skip installation.

## Components

### GPU MachineSets

Deploy GPU-enabled worker nodes using the deployment script. See [GPU MachineSets Quick Start](#quick-start-deploy-gpu-nodes) below and [detailed GPU documentation](infra/gpu-machineset/README.md).

### CPU Worker MachineSets

High-capacity CPU worker nodes for workloads that need more compute but not GPUs (e.g., RHOAI platform pods, data preprocessing).

**Available CPU instance types:**
- **m6a.4xlarge**: 16 vCPU, 64GB RAM, AMD EPYC (~$0.69/hr)
- **m6i.4xlarge**: 16 vCPU, 64GB RAM, Intel Xeon (~$0.77/hr)

**Deploy CPU workers:**

```bash
# Deploy via script (recommended)
./infra/cpu-machineset/aws/deploy.sh

# Or deploy with custom settings
INSTANCE_TYPE=m6a.4xlarge \
ROOT_VOLUME_SIZE=200 \
./infra/cpu-machineset/aws/deploy.sh
```

## Quick Start: Deploy GPU Nodes

### Deployment Script (Recommended)

Deploy GPU nodes with automatic cluster configuration:

```bash
# Clone this repository
git clone https://github.com/dandawg/openshift-infra.git
cd openshift-infra

# Ensure you're logged into OpenShift and ArgoCD is installed
oc whoami
./bootstrap.sh  # If GitOps not already installed

# Deploy with default settings (g6.2xlarge, 120GB gp3 volume)
./infra/gpu-machineset/aws/deploy.sh

# Or deploy g4dn.xlarge (most cost-effective)
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh

# Or deploy g6e.2xlarge (high-performance)
INSTANCE_TYPE=g6e.2xlarge ./infra/gpu-machineset/aws/deploy.sh

# Or deploy g6.4xlarge with custom storage
INSTANCE_TYPE=g6.4xlarge \
ROOT_VOLUME_SIZE=200 \
ROOT_VOLUME_TYPE=gp3 \
ROOT_VOLUME_IOPS=5000 \
./infra/gpu-machineset/aws/deploy.sh
```

The script automatically:
1. Gathers cluster information (name, region, AZ, AMI)
2. Logs in to ArgoCD
3. Creates the ArgoCD Application
4. Sets cluster-specific Helm parameters
5. Syncs the application to deploy the MachineSet

### Alternative: Manual Deployment

For advanced users who want manual control:

```bash
# Apply the ArgoCD Application manifest
oc apply -f gitops/infra/gpu-machineset-aws-g6.yaml

# Gather cluster information
CLUSTER_NAME=$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')
REGION=$(oc get infrastructure cluster -o jsonpath='{.status.platformStatus.aws.region}')
AVAILABILITY_ZONE=$(oc get machines -n openshift-machine-api -l machine.openshift.io/cluster-api-machine-role=worker -o jsonpath='{.items[0].spec.providerSpec.value.placement.availabilityZone}')
INFRA_ID=$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')
AMI_ID=$(oc get machineset -n openshift-machine-api -o json | jq -r '.items[] | select(.metadata.name | contains("gpu") | not) | .spec.template.spec.providerSpec.value.ami.id' | head -1)

# Login to ArgoCD
ARGOCD_PASSWORD=$(oc get secret/openshift-gitops-cluster -n openshift-gitops -o jsonpath='{.data.admin\.password}' | base64 -d)
ARGOCD_SERVER=$(oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='{.spec.host}')
argocd login $ARGOCD_SERVER --username admin --password $ARGOCD_PASSWORD --insecure

# Set cluster parameters
argocd app set gpu-machineset-aws-g6 \
  -p clusterName="$CLUSTER_NAME" \
  -p region="$REGION" \
  -p availabilityZone="$AVAILABILITY_ZONE" \
  -p infraID="$INFRA_ID" \
  -p amiId="$AMI_ID"

# Enable auto-sync and sync
argocd app set gpu-machineset-aws-g6 --sync-policy automated --auto-prune --self-heal
argocd app sync gpu-machineset-aws-g6
```

## Available GPU Instance Types

| Instance Type | GPUs | GPU Memory | vCPUs | RAM | Cost/hr* | Use Case |
|---------------|------|------------|-------|-----|----------|----------|
| g4dn.xlarge | 1x T4 | 16GB | 4 | 16GB | ~$0.53 | Cost-effective, embedding models, small models |
| g6.2xlarge | 1x L4 | 24GB | 8 | 32GB | ~$1.10 | Production, single model inference |
| g6.4xlarge | 1x L4 | 24GB | 16 | 64GB | ~$2.15 | Large models, vision models, high throughput |

*Approximate on-demand pricing (us-east-1, subject to change)

### Deploy Multiple GPU Types

You can deploy multiple GPU instance types simultaneously:

```bash
# Deploy cost-effective g4dn for embedding models
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh

# Also deploy g6.2xlarge for production inference
INSTANCE_TYPE=g6.2xlarge ./infra/gpu-machineset/aws/deploy.sh

# And deploy g6.4xlarge for vision models
INSTANCE_TYPE=g6.4xlarge ./infra/gpu-machineset/aws/deploy.sh
```

Each instance type creates a uniquely named MachineSet to avoid conflicts:
- g4dn.xlarge: `{clusterName}-gpu-g4dn-{az}`
- g6.2xlarge: `{clusterName}-gpu-g6-{az}`
- g6.4xlarge: `{clusterName}-gpu-g6-4x-{az}`

## Verification

```bash
# Watch MachineSet creation
oc get machineset -n openshift-machine-api -w

# View all GPU machines
oc get machine -n openshift-machine-api -l gpu-node=true

# View machines by instance type
oc get machine -n openshift-machine-api -l gpu-instance-type=g4dn.xlarge
oc get machine -n openshift-machine-api -l gpu-instance-type=g6.2xlarge
oc get machine -n openshift-machine-api -l gpu-instance-type=g6.4xlarge

# Wait for GPU node to be ready (5-10 minutes)
oc wait --for=condition=Ready nodes -l nvidia.com/gpu.present=true --timeout=600s

# View all GPU nodes
oc get nodes -l nvidia.com/gpu.present=true

# View nodes by instance type
oc get nodes -l gpu-instance-type=g4dn.xlarge
oc get nodes -l gpu-instance-type=g6.2xlarge
oc get nodes -l gpu-instance-type=g6.4xlarge
```

## Cost Management

GPU nodes are expensive. Best practices:

### Scale Down When Not in Use

```bash
# Scale down specific GPU type
oc scale machineset $(oc get machineset -n openshift-machine-api -l gpu-instance-type=g6.2xlarge -o name) --replicas=0 -n openshift-machine-api

# Scale back up
oc scale machineset $(oc get machineset -n openshift-machine-api -l gpu-instance-type=g6.2xlarge -o name) --replicas=1 -n openshift-machine-api
```

### Cost Savings Tips

- Start with **g4dn.xlarge** for development and testing (~$0.53/hr)
- Use **g6.2xlarge** for production single-model workloads (~$1.10/hr)
- Reserve **g6.4xlarge** for high-performance scenarios (~$2.15/hr)
- Consider AWS Spot instances for 60-70% discount
- Set up billing alerts in AWS
- Use cluster autoscaler for dynamic scaling

### Model Recommendations by Instance

- **g4dn.xlarge**: Embedding models (Qwen3-VL-Embedding-2B), small 7B models
- **g6.2xlarge**: Granite 7B, Qwen3-VL-4B, general inference
- **g6.4xlarge**: Qwen3-VL-8B, Llama 3 8B, vision models, high-throughput

## Customization

### Fork This Repository

If you fork this repository, update the `repoURL` in all GitOps manifests:

```bash
# Update all ArgoCD Application manifests
find gitops/ -name "*.yaml" -type f -exec sed -i '' \
  's|repoURL: .*|repoURL: https://github.com/YOUR-ORG/openshift-infra|g' {} \;
```

### Adjust GPU Configuration

Edit parameters in `infra/gpu-machineset/aws/helm/values-*.yaml` to customize:
- Instance type
- Number of replicas
- Root volume size/type/IOPS
- Node taints and labels

## Troubleshooting

### GPU Nodes Not Ready

```bash
# Check MachineSet status
oc get machineset -n openshift-machine-api

# Check Machine status
oc describe machine <machine-name> -n openshift-machine-api

# Common issues:
# - AWS quota limit reached for GPU instances
# - Instance type not available in your availability zone
# - IAM permissions issue
```

### Node Doesn't Show GPU

```bash
# Check node labels
oc get nodes -L nvidia.com/gpu.present

# Check if NVIDIA GPU Operator is installed
oc get pods -n nvidia-gpu-operator

# If not installed, see RHOAI deployment guides
```

### Error: "Resource not found: REPLACE_ME-gpu-REPLACE_ME"

This error no longer occurs with the script-based deployment approach. If you see this error, it means the ArgoCD Application was applied without setting parameters.

**Solution**: Use the deployment script:
```bash
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh
```

Or manually set parameters using `argocd app set` (see Alternative: Manual Deployment section above).

## Documentation

- [GPU MachineSets README](infra/gpu-machineset/README.md) - Detailed GPU MachineSets documentation
- [AWS GPU MachineSets](infra/gpu-machineset/aws/README.md) - AWS-specific details

## Related Repositories

This repository is designed to work both:
1. **Standalone** - Deploy individual infrastructure components as needed
2. **As a dependency** - Referenced by other GitOps repositories

Related repositories:
- [rhoai-deploy](https://github.com/redhat-ai-americas/rhoai-deploy) - Red Hat OpenShift AI platform deployment
- [rhoai-anythingllm-demos](https://github.com/dandawg/rhoai-anythingllm-demos) - RHOAI AnythingLLM demos (references this repo for node provisioning)

## Contributing

Contributions are welcome! Please open an issue or pull request.

## License

Apache License 2.0
