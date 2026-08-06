#!/bin/bash
# bootstrap.sh - Smart GitOps installer for OpenShift Infra
set -e

echo "🔍 Checking for OpenShift GitOps..."

# Check if GitOps is already installed
if oc get deployment openshift-gitops-server -n openshift-gitops &>/dev/null; then
  echo "✅ OpenShift GitOps is already installed. Skipping installation."
  echo ""
  echo "ArgoCD URL:"
  oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='https://{.spec.host}{"\n"}' 2>/dev/null || echo "  (Route not yet available)"
  exit 0
fi

echo "📦 Installing OpenShift GitOps Operator..."
oc apply -k bootstrap/gitops-operator/base/

echo "⏳ Waiting for GitOps Operator to be ready..."
# OLM sometimes bundles the GitOps install plan with other subscriptions that have
# Manual approval (e.g. a pinned CloudNativePG). When that happens the shared
# InstallPlan lands in RequiresApproval and nothing installs until it is approved.
# Detect and approve that plan automatically so bootstrap is not blocked.
echo "  Checking for blocked InstallPlan..."
for attempt in $(seq 1 12); do
  gitops_ip=$(oc get subscription openshift-gitops-operator -n openshift-operators \
    -o jsonpath='{.status.installPlanRef.name}' 2>/dev/null || true)
  if [ -n "$gitops_ip" ]; then
    ip_phase=$(oc get installplan "$gitops_ip" -n openshift-operators \
      -o jsonpath='{.status.phase}' 2>/dev/null || true)
    if [ "$ip_phase" = "RequiresApproval" ]; then
      echo "  ⚠️  InstallPlan $gitops_ip is RequiresApproval (likely shared with a Manual-approval subscription)."
      echo "     Auto-approving so GitOps installation can proceed..."
      oc patch installplan "$gitops_ip" -n openshift-operators \
        --type merge --patch '{"spec":{"approved":true}}'
      echo "  ✅ InstallPlan approved."
    fi
    break
  fi
  sleep 5
done

# oc wait fails immediately if the Deployment does not exist yet; OLM creates it
# after the Subscription reconciles. Poll until it appears, then wait for Available.
# Newer GitOps defaults may place the operator in openshift-gitops-operator; older
# installs use openshift-operators (matching our Subscription namespace).
timeout=300
elapsed=0
operator_ns=""
while [ "$elapsed" -lt "$timeout" ]; do
  for try_ns in openshift-operators openshift-gitops-operator; do
    if oc get deployment openshift-gitops-operator-controller-manager -n "$try_ns" &>/dev/null; then
      operator_ns=$try_ns
      break 2
    fi
  done
  echo "  Waiting for operator deployment to appear... (${elapsed}s / ${timeout}s)"
  sleep 5
  elapsed=$((elapsed + 5))
done

if [ -z "$operator_ns" ]; then
  echo "❌ Timeout: deployment/openshift-gitops-operator-controller-manager not found."
  echo "   Check: oc get subscription openshift-gitops-operator -n openshift-operators"
  echo "   and: oc get csv -n openshift-operators; oc get installplan -n openshift-operators"
  exit 1
fi

oc wait --for=condition=Available \
  deployment/openshift-gitops-operator-controller-manager \
  -n "$operator_ns" --timeout=300s

echo "🚀 Creating ArgoCD instance..."
oc apply -k bootstrap/gitops-operator/instance/

echo "⏳ Waiting for ArgoCD to be ready..."
# oc wait exits immediately with "no matching resources found" if no pods exist yet.
# Poll until at least one pod appears before handing off to oc wait.
elapsed=0
while [ "$elapsed" -lt "$timeout" ]; do
  if oc get pod -l app.kubernetes.io/name=openshift-gitops-server \
       -n openshift-gitops --no-headers 2>/dev/null | grep -q .; then
    break
  fi
  echo "  Waiting for openshift-gitops-server pod to appear... (${elapsed}s / ${timeout}s)"
  sleep 5
  elapsed=$((elapsed + 5))
done

oc wait --for=condition=Ready \
  pod -l app.kubernetes.io/name=openshift-gitops-server \
  -n openshift-gitops --timeout=300s

echo ""
echo "✅ GitOps installation complete!"
echo ""
echo "ArgoCD URL:"
echo "  https://$(oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='{.spec.host}')"
echo ""
echo "To retrieve ArgoCD credentials:"
echo "  oc get secret openshift-gitops-cluster -n openshift-gitops -o jsonpath='{.data.admin\.password}' | base64 -d && echo"
echo ""
