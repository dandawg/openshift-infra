# OpenShift Infrastructure Components

Infrastructure-level components for OpenShift clusters, focused on GPU-enabled node provisioning and other foundational infrastructure elements.

## Overview

This repository provides production-ready infrastructure components for OpenShift:
- **Cluster Discovery**: Automated job to gather cluster information for GitOps deployments
- **GPU MachineSets**: Automated deployment of GPU-enabled nodes on AWS
- **CPU MachineSets**: Automated deployment of high-capacity CPU worker nodes on AWS
- **Multi-GPU Support**: Deploy different GPU instance types (g4dn, g6) simultaneously
- **GitOps Ready**: ArgoCD Application manifests for automated deployment
- **Cost Optimized**: Choose the right instance type for your workload

## Repository Structure

```
openshift-infra/
├── README.md              # This file
├── gitops/               # ArgoCD Application manifests
│   └── infra/           # Infrastructure components
│       ├── cluster-discovery.yaml
│       ├── gpu-machineset-aws-g4dn-xlarge.yaml
│       ├── gpu-machineset-aws-g6.yaml
│       ├── gpu-machineset-aws-g6-4xlarge.yaml
│       └── cpu-machineset-aws-m6a-4xlarge.yaml
└── infra/               # Infrastructure component definitions
    ├── cluster-discovery/  # Cluster info discovery job
    │   ├── kustomization.yaml
    │   ├── job.yaml
    │   └── rbac files...
    ├── gpu-machineset/  # GPU node templates
    │   ├── README.md    # GPU MachineSets documentation
    │   ├── base/        # Base MachineSet template
    │   └── aws/         # AWS-specific configurations
    │       ├── deploy.sh       # Automated deployment script
    │       ├── helm/           # Helm chart for ArgoCD
    │       └── overlays/       # Kustomize overlays
    └── cpu-machineset/  # CPU worker node templates
        └── aws/         # AWS-specific configurations
            └── helm/           # Helm chart for ArgoCD
```

## Prerequisites

- **OpenShift 4.16+** on AWS with cluster-admin access
- **OpenShift GitOps** (ArgoCD) installed
- **`oc` CLI** and **`argocd` CLI** installed
- **AWS quota** for GPU and CPU instances in your region

## Components

### Cluster Discovery

Automated Kubernetes Job that discovers AWS cluster information and creates a ConfigMap for use by other GitOps applications.

**What it discovers:**
- Cluster name and infrastructure ID
- AWS region and availability zone
- AMI ID for worker nodes

**Usage:**
```bash
# Standalone deployment
oc apply -f gitops/infra/cluster-discovery.yaml

# Or reference in ArgoCD multi-source applications
```

The cluster-discovery job runs as a PreSync hook (Wave 0) and creates a ConfigMap that other applications can reference for cluster-specific parameters.

### GPU MachineSets

See the [GPU MachineSets Quick Start](#quick-start-deploy-gpu-nodes) below.

### CPU Worker MachineSets

High-capacity CPU worker nodes for workloads that need more compute but not GPUs (e.g., RHOAI platform pods, data preprocessing).

**Available CPU instance types:**
- **m6a.4xlarge**: 16 vCPU, 64GB RAM, AMD EPYC (~$0.69/hr)
- **m6i.4xlarge**: 16 vCPU, 64GB RAM, Intel Xeon (~$0.77/hr)

**Deploy CPU workers:**
```bash
# Deploy via GitOps (manual parameter setup required)
oc apply -f gitops/infra/cpu-machineset-aws-m6a-4xlarge.yaml

# Then set cluster parameters using argocd CLI
CLUSTER_NAME=$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')
REGION=$(oc get infrastructure cluster -o jsonpath='{.status.platformStatus.aws.region}')
# ... etc

argocd app set cpu-machineset-aws-m6a-4xlarge \
  -p clusterName="$CLUSTER_NAME" \
  -p region="$REGION" \
  # ... etc
```

## Quick Start: Deploy GPU Nodes

### Option 1: Automated Deployment Script (Recommended)

Deploy GPU nodes with automatic cluster configuration:

```bash
# Clone this repository
git clone https://github.com/dandawg/openshift-infra.git
cd openshift-infra

# Deploy with default settings (g6.2xlarge, 120GB gp3 volume)
./infra/gpu-machineset/aws/deploy.sh

# Or deploy g4dn.xlarge (most cost-effective)
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh

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

### Option 2: Manual GitOps Deployment

If you prefer to manually configure the deployment:

```bash
# Apply the ArgoCD Application manifest
oc apply -f gitops/infra/gpu-machineset-aws-g6.yaml

# Set cluster-specific parameters using argocd CLI
# (Gather cluster info first)
CLUSTER_NAME=$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')
REGION=$(oc get infrastructure cluster -o jsonpath='{.status.platformStatus.aws.region}')
# ... etc

argocd app set gpu-machineset-aws-g6 \
  -p clusterName="$CLUSTER_NAME" \
  -p region="$REGION" \
  # ... etc

# Sync the application
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

This error occurs when the GitOps Application is applied without setting Helm parameters. 

**Solution**: Use the deployment script which handles this automatically:
```bash
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh
```

## Documentation

- [GPU MachineSets README](infra/gpu-machineset/README.md) - Detailed GPU MachineSets documentation
- [AWS GPU MachineSets](infra/gpu-machineset/aws/README.md) - AWS-specific details

## Related Repositories

This repository is designed to work both:
1. **Standalone** - Deploy individual infrastructure components as needed
2. **As a dependency** - Referenced by other GitOps repositories (e.g., rhoai-app-demos-new)

Related repositories:
- [rhoai-deploy](https://github.com/redhat-ai-americas/rhoai-deploy) - Red Hat OpenShift AI platform deployment
- [rhoai-app-demos-new](https://github.com/redhat-ai-americas/rhoai-app-demos-new) - RHOAI application demos (references this repo for cluster-discovery and node provisioning)

## Contributing

Contributions are welcome! Please open an issue or pull request.

## License

Apache License 2.0
